"""Archive reading and writing: ZIP, 7Z, TAR (+ gz/xz/bz2), and RAR (read-only).

ZIP: pyzipper (reads ZipCrypto + AES, writes AES-256). 7Z: py7zr. TAR: tarfile. RAR is read with
bsdtar (libarchive, BSD) — built into Windows 10+ (System32\\tar.exe) and macOS (/usr/bin/tar);
on Linux it's the ``bsdtar`` package. No unRAR code is used (its licence isn't AGPL-compatible).

Extraction is defensive: absolute paths, ``..`` and links are never written outside the target.
"""

from __future__ import annotations

import contextlib
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from umc.core.errors import ConversionCancelled, ConversionError

# canonical ext → (label, tarfile mode suffix)
TAR_MODES = {"tar": "", "tar.gz": "gz", "tar.xz": "xz", "tar.bz2": "bz2"}
READABLE = {"zip", "7z", "rar", *TAR_MODES}
WRITABLE = ["zip", "7z", "tar", "tar.gz", "tar.xz", "tar.bz2"]
ENCRYPTABLE = {"zip", "7z"}

# level name → (zip deflate level, 7z LZMA2 preset, gzip/bz2 level, xz preset)
LEVELS = {
    "store": (0, 0, 0, 0),
    "fast": (1, 1, 1, 1),
    "normal": (6, 5, 6, 6),
    "maximum": (9, 9, 9, 9),
}

Progress = Callable[[float], None]


def _safe_target(root: Path, name: str) -> Path | None:
    """Where ``name`` goes below ``root``; None if it would escape (absolute, ``..``, drive)."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


class PasswordNeeded(ConversionError):
    """The archive (or one of its files) is password-protected and no / a wrong one was given."""


def _wrong_password(name: str) -> ConversionError:
    return PasswordNeeded(f"“{name}” is password-protected. Enter the right password in Settings.")


def norm_name(name: str) -> str:
    """A name inside an archive as ``a/b/c`` (no leading ``./`` or ``/``, no trailing ``/``)."""
    return "/".join(p for p in name.replace("\\", "/").split("/") if p not in ("", "."))


Picker = Callable[[str], "str | None"]


def _picker(only: Collection[str] | None, strip: str) -> Picker:
    """Maps a name inside the archive to where it goes below the target (None = skip it).
    ``only``: names (files or folders, a folder takes everything below it) to extract;
    ``strip``: a folder whose path is cut off the front (extracting from inside a folder)."""
    wanted = None if only is None else [norm_name(n) for n in only]
    strip = norm_name(strip)

    def pick(name: str) -> str | None:
        n = norm_name(name)
        if wanted is not None and not any(n == w or n.startswith(w + "/") for w in wanted):
            return None
        if strip:
            if not n.startswith(strip + "/"):
                return None
            n = n[len(strip) + 1 :]
        return n or None

    return pick


def _place(dest: Path, rel: str, unique: bool) -> Path | None:
    """The file to write for ``rel`` below ``dest`` (None = unsafe name); with ``unique`` an
    existing file is never replaced: ``name (1).ext``."""
    from umc.core.job import unique_path

    target = _safe_target(dest, rel)
    if target is not None and unique and target.exists():
        target = unique_path(target.parent, target.stem, target.suffix.lstrip("."))
    return target


def _set_mtime(path: Path, when: float | None) -> None:
    if when:
        with contextlib.suppress(OSError, OverflowError, ValueError):
            os.utime(path, (when, when))


# --------------------------------------------------------------------------- reading


def extract(
    src: Path,
    ext: str,
    dest: Path,
    password: str,
    progress: Progress,
    cancel: threading.Event,
    only: Collection[str] | None = None,
    strip: str = "",
    unique: bool = False,
) -> list[Path]:
    """Extract the regular files of ``src`` into ``dest``; return the written paths.

    ``only`` limits it to these names (a folder name takes everything below it), ``strip``
    cuts a folder off the front of every name, and ``unique`` never replaces a file that is
    already there (``name (1).ext``)."""
    dest.mkdir(parents=True, exist_ok=True)
    pick = _picker(only, strip)
    try:
        if ext == "zip":
            return _extract_zip(src, dest, password, progress, cancel, pick, unique)
        if ext == "7z":
            return _extract_7z(src, dest, password, progress, cancel, pick, unique)
        if ext in TAR_MODES:
            return _extract_tar(src, dest, progress, cancel, pick, unique)
        if ext == "rar":
            return _extract_bsdtar(src, dest, password, cancel, pick, unique, only)
    except ConversionError:
        raise
    except (OSError, EOFError, tarfile.TarError, ValueError, RuntimeError) as exc:
        raise ConversionError(
            f"Couldn't open “{src.name}”. Is it a complete, valid archive?", details=repr(exc)
        ) from exc
    raise ConversionError(f"{ext.upper()} archives can't be opened.")


def _zip_is_link(info) -> bool:
    # a symlink stored by Unix zip tools has S_IFLNK in the high attribute bits
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _zip_time(info) -> datetime | None:
    try:
        return datetime(*info.date_time)
    except (TypeError, ValueError):
        return None


def _extract_zip(src, dest, password, progress, cancel, pick, unique) -> list[Path]:
    import pyzipper

    out = []
    with pyzipper.AESZipFile(src) as zf:
        if password:
            zf.setpassword(password.encode())
        infos = [
            i for i in zf.infolist() if not i.is_dir() and not _zip_is_link(i) and pick(i.filename)
        ]
        for n, info in enumerate(infos):
            if cancel.is_set():
                raise ConversionCancelled()
            target = _place(dest, pick(info.filename), unique)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as fin, target.open("wb") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            except RuntimeError as exc:
                target.unlink(missing_ok=True)
                if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                    raise _wrong_password(src.name) from exc
                raise
            when = _zip_time(info)
            _set_mtime(target, when.timestamp() if when else None)
            out.append(target)
            progress((n + 1) / len(infos))
    return out


def _open_7z(src: Path, password: str):
    """py7zr's reader; a missing / wrong password becomes PasswordNeeded."""
    import py7zr

    try:
        return py7zr.SevenZipFile(src, password=password or None)
    except py7zr.exceptions.PasswordRequired as exc:
        raise _wrong_password(src.name) from exc
    except (TypeError, KeyError, py7zr.exceptions.Bad7zFile) as exc:
        # names hidden by a password: a wrong one makes the header unreadable
        if password:
            raise _wrong_password(src.name) from exc
        raise


def _extract_7z(src, dest, password, progress, cancel, pick, unique) -> list[Path]:
    import py7zr

    staging = Path(tempfile.mkdtemp(prefix=".umc-extract-", dir=dest))
    try:
        try:
            with _open_7z(src, password) as zf:
                if zf.needs_password() and not password:
                    raise _wrong_password(src.name)
                entries = [
                    e
                    for e in zf.list()
                    if not e.is_directory and not e.is_symlink and pick(e.filename)
                ]
                names = [e.filename for e in entries if _safe_target(staging, e.filename)]
                if cancel.is_set():
                    raise ConversionCancelled()
                if names:
                    zf.extract(path=staging, targets=names)
        except py7zr.exceptions.PasswordRequired as exc:
            raise _wrong_password(src.name) from exc
        except (py7zr.exceptions.Bad7zFile, py7zr.exceptions.CrcError, lzma.LZMAError) as exc:
            if password:  # py7zr can't tell a wrong password from damaged data
                raise PasswordNeeded(
                    f"Couldn't open “{src.name}” — wrong password?", details=repr(exc)
                ) from exc
            raise
        times = {
            norm_name(e.filename): e.creationtime.timestamp()
            for e in entries
            if getattr(e, "creationtime", None)
        }
        out = []
        for name in names:
            found = _safe_target(staging, name)
            rel = pick(name)
            if found is None or rel is None or not found.is_file() or found.is_symlink():
                continue
            target = _place(dest, rel, unique)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(found), target)
            _set_mtime(target, times.get(norm_name(name)))
            out.append(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    progress(1.0)
    return out


def _extract_tar(src, dest, progress, cancel, pick, unique) -> list[Path]:
    out = []
    with tarfile.open(src) as tf:
        members = [m for m in tf.getmembers() if m.isfile() and pick(m.name)]
        for n, m in enumerate(members):
            if cancel.is_set():
                raise ConversionCancelled()
            target = _place(dest, pick(m.name), unique)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fin = tf.extractfile(m)
            if fin is None:
                continue
            with fin, target.open("wb") as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
            _set_mtime(target, m.mtime)
            out.append(target)
            progress((n + 1) / len(members))
    return out


def bsdtar_path() -> str | None:
    candidates = []
    if sys.platform == "win32":
        candidates.append(
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
        )
    elif sys.platform == "darwin":
        candidates.append(Path("/usr/bin/tar"))
    found = shutil.which("bsdtar")
    if found:
        candidates.insert(0, Path(found))
    return next((str(p) for p in candidates if p.is_file()), None)


def _pattern(name: str) -> str:
    """A name as a bsdtar pattern that matches only itself (and, for a folder, what's in it)."""
    return re.sub(r"([\\*?\[])", r"\\\1", norm_name(name))


def _extract_bsdtar(src, dest, password, cancel, pick, unique, only=None) -> list[Path]:
    from umc.tools import process

    tool = bsdtar_path()
    if tool is None:
        raise ConversionError(
            "Opening RAR files needs “bsdtar”. It's built into Windows 10/11 and macOS; "
            "on Linux install the “libarchive-tools” (or “bsdtar”) package."
        )
    staging = Path(tempfile.mkdtemp(prefix=".umc-extract-", dir=dest))
    args = [tool, "-x", "-f", str(src), "-C", str(staging), "--no-same-owner"]
    if password:
        args += ["--passphrase", password]
    if only is not None:
        args += [_pattern(n) for n in only]
    try:
        process.run(args, cancel_event=cancel, error_message=f"Couldn't open “{src.name}”.")
    except ConversionError as exc:
        if not _only_link_errors(tool, src, exc.details):
            shutil.rmtree(staging, ignore_errors=True)
            _raise_bsdtar_error(src, exc)
        # else: only links failed (Windows won't let normal users create them); they're dropped
        # below anyway, so everything else that was extracted is kept.
    # bsdtar refuses absolute and ``..`` paths itself; also drop links and move into place.
    out = []
    for p in sorted(staging.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = pick(p.relative_to(staging).as_posix())
        target = _place(dest, rel, unique) if rel else None
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), target)
        out.append(target)
    shutil.rmtree(staging, ignore_errors=True)
    return out


_CANT_CREATE = re.compile(r"^(.+?): Can't create '", re.MULTILINE)


def _link_names(tool: str, src: Path) -> set[str]:
    """Names of the symbolic / hard links in an archive (from ``bsdtar -tv``)."""
    from umc.tools import process

    try:
        listing = subprocess.run(
            [tool, "-tv", "-f", str(src)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=process._creationflags(),
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return set()
    names = set()
    for line in listing.splitlines():
        # "lrwxrwxrwx  0 0 0  0 Jun 10 12:00 testlink -> test.txt" / "... a link to b"
        for sep in (" -> ", " link to "):
            if line[:1] in ("l", "h", "-") and sep in line:
                names.add(line.split(sep, 1)[0].rsplit(" ", 1)[-1])
    return names


def _only_link_errors(tool: str, src: Path, stderr: str) -> bool:
    """True if bsdtar only failed to create links — e.g. Windows' tar.exe, which normal users
    can't make symbolic links with. Links are never extracted anyway (see _extract_bsdtar)."""
    failed = set(_CANT_CREATE.findall(stderr))
    other = [
        line
        for line in stderr.splitlines()
        if line.strip() and "Can't create" not in line and "Error exit delayed" not in line
    ]
    return bool(failed) and not other and failed <= _link_names(tool, src)


def _raise_bsdtar_error(src: Path, exc: ConversionError) -> None:
    text = exc.details.lower()
    if "passphrase" in text or "encrypt" in text:
        raise ConversionError(
            f"“{src.name}” is encrypted. Password-protected RAR files can't be opened yet."
        ) from exc
    raise exc


# --------------------------------------------------------------------------- listing


@dataclass(frozen=True)
class Entry:
    """One file or folder inside an archive (``name`` as given by ``norm_name``)."""

    name: str
    is_dir: bool = False
    size: int = 0
    packed: int | None = None  # None = not known (solid 7Z blocks, RAR, TAR)
    modified: datetime | None = None
    encrypted: bool = False


@dataclass(frozen=True)
class Contents:
    entries: list[Entry]
    encrypted: bool = False  # any file needs a password
    names_hidden: bool = False  # 7Z whose list of names is encrypted too
    links: int = 0  # symbolic / hard links (left out of ``entries``)


def entries(src: Path, ext: str, password: str = "") -> Contents:
    """What ``src`` holds: files and folders (links are left out — they're never extracted).

    Raises PasswordNeeded when even the names can't be read without (the right) password."""
    try:
        if ext == "zip":
            return _list_zip(src)
        if ext == "7z":
            return _list_7z(src, password)
        if ext in TAR_MODES:
            return _list_tar(src)
        if ext == "rar":
            return _list_bsdtar(src)
    except ConversionError:
        raise
    except (OSError, EOFError, tarfile.TarError, ValueError, RuntimeError) as exc:
        raise ConversionError(
            f"Couldn't open “{src.name}”. Is it a complete, valid archive?", details=repr(exc)
        ) from exc
    raise ConversionError(f"{ext.upper()} archives can't be opened.")


def _list_zip(src: Path) -> Contents:
    import pyzipper

    out = []
    with pyzipper.AESZipFile(src) as zf:
        for i in zf.infolist():
            name = norm_name(i.filename)
            if not name or _zip_is_link(i):
                continue
            out.append(
                Entry(
                    name,
                    is_dir=i.is_dir(),
                    size=0 if i.is_dir() else i.file_size,
                    packed=None if i.is_dir() else i.compress_size,
                    modified=_zip_time(i),
                    encrypted=bool(i.flag_bits & 0x1),
                )
            )
    return Contents(out, encrypted=any(e.encrypted for e in out))


def _list_7z(src: Path, password: str) -> Contents:
    hidden = False
    try:
        zf = _open_7z(src, "")
    except PasswordNeeded:
        if not password:
            raise
        hidden = True
        zf = _open_7z(src, password)
    out = []
    links = 0
    with zf:
        locked = zf.needs_password()
        for e in zf.list():
            name = norm_name(e.filename)
            links += bool(e.is_symlink)
            if not name or e.is_symlink:
                continue
            when = getattr(e, "creationtime", None)
            if when is not None and when.tzinfo is not None:
                when = when.astimezone().replace(tzinfo=None)
            out.append(
                Entry(
                    name,
                    is_dir=e.is_directory,
                    size=0 if e.is_directory else int(e.uncompressed or 0),
                    packed=None if e.is_directory or not e.compressed else int(e.compressed),
                    modified=when,
                    encrypted=locked and not e.is_directory,
                )
            )
    return Contents(out, encrypted=locked or hidden, names_hidden=hidden, links=links)


def _list_tar(src: Path) -> Contents:
    out = []
    with tarfile.open(src) as tf:
        for m in tf.getmembers():
            name = norm_name(m.name)
            if not name or not (m.isfile() or m.isdir()):
                continue
            out.append(
                Entry(
                    name,
                    is_dir=m.isdir(),
                    size=m.size if m.isfile() else 0,
                    modified=datetime.fromtimestamp(m.mtime) if m.mtime else None,
                )
            )
    return Contents(out)


# "-rw-r--r--  0 user group  1234 Jun 10 12:00 name" (bsdtar -tv, also Windows' tar.exe)
_TV_LINE = re.compile(
    r"^(?P<mode>[-dlhbcps][-rwxsStT]{9}\S*)\s+\d+\s+\S+\s+\S+\s+(?P<size>\d+)\s+"
    r"(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{1,2}:\d{2}|\d{4})\s(?P<name>.+)$"
)
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS = {m: n for n, m in enumerate(_MONTH_NAMES, 1)}


def parse_tv_listing(text: str, now: datetime | None = None) -> list[Entry]:
    """Entries from ``bsdtar -tv`` output (links skipped; a time without a year is this year's,
    or last year's when that would be in the future — like ``ls``)."""
    now = now or datetime.now()
    out = []
    for line in text.splitlines():
        m = _TV_LINE.match(line.rstrip("\r"))
        if not m or m["mode"][0] not in "-d":
            continue
        name = norm_name(m["name"])
        if not name:
            continue
        when = None
        try:
            month, day = _MONTHS[m["mon"].title()], int(m["day"])
            if ":" in m["time"]:
                hour, minute = (int(x) for x in m["time"].split(":"))
                when = datetime(now.year, month, day, hour, minute)
                if when > now.replace(microsecond=0) and (when - now).days > 1:
                    when = when.replace(year=now.year - 1)
            else:
                when = datetime(int(m["time"]), month, day)
        except (KeyError, ValueError):
            when = None
        is_dir = m["mode"][0] == "d" or m["name"].endswith("/")
        out.append(Entry(name, is_dir=is_dir, size=0 if is_dir else int(m["size"]), modified=when))
    return out


def _list_bsdtar(src: Path) -> Contents:
    from umc.tools import process

    tool = bsdtar_path()
    if tool is None:
        raise ConversionError(
            "Opening RAR files needs “bsdtar”. It's built into Windows 10/11 and macOS; "
            "on Linux install the “libarchive-tools” (or “bsdtar”) package."
        )
    try:
        done = subprocess.run(
            [tool, "-tv", "-f", str(src)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=process._creationflags(),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ConversionError(f"Couldn't open “{src.name}”.", details=repr(exc)) from exc
    if done.returncode != 0:
        problem = ConversionError(f"Couldn't open “{src.name}”.", details=done.stderr)
        _raise_bsdtar_error(src, problem)
    return Contents(parse_tv_listing(done.stdout))


def can_change(ext: str) -> bool:
    """Whether files can be added to / removed from this kind of archive (RAR is read-only)."""
    return ext in ("zip", "7z", *TAR_MODES)


def rewrite(
    src: Path,
    ext: str,
    password: str,
    add: list[tuple[Path, str]],
    remove: Collection[str],
    progress: Progress,
    cancel: threading.Event,
) -> None:
    """Change ``src`` in place: drop what ``remove`` names (a folder takes what's in it), then
    add ``add`` (file on disk, name inside; a name already there is replaced). Everything else
    is copied across as it was — permissions, dates, links and empty folders included (ZIP /
    TAR; 7Z keeps files and folders, and refuses when it holds links). Written to a new file
    next to it that then replaces it, so a failure or Cancel leaves the original as it was. A
    password-protected archive stays protected with the same password."""
    if not can_change(ext):
        raise ConversionError(f"{ext.upper()} archives can only be read, not changed.")
    contents = entries(src, ext, password)
    if contents.encrypted and not password:
        raise _wrong_password(src.name)
    gone = [norm_name(n) for n in remove]
    replaced = {norm_name(name) for _p, name in add}

    def dropped(name: str) -> bool:
        n = norm_name(name)
        return n in replaced or any(n == g or n.startswith(g + "/") for g in gone)

    tmp = src.with_name(f".umc-tmp-{os.getpid()}-{src.name}")
    adds = [(p, norm_name(n)) for p, n in add]
    try:
        if ext == "zip":
            _rewrite_zip(
                src, tmp, password if contents.encrypted else "", dropped, adds, progress, cancel
            )
        elif ext in TAR_MODES:
            _rewrite_tar(src, tmp, ext, dropped, adds, progress, cancel)
        else:
            _rewrite_7z(src, tmp, password, contents, dropped, adds, progress, cancel)
        if cancel.is_set():
            raise ConversionCancelled()
        os.replace(tmp, src)
    except ConversionError:
        raise
    except (OSError, EOFError, tarfile.TarError, ValueError, RuntimeError) as exc:
        if "password" in str(exc).lower():
            raise _wrong_password(src.name) from exc
        raise ConversionError(f"Couldn't change “{src.name}”.", details=repr(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    progress(1.0)


def _rewrite_zip(src, tmp, password, dropped, adds, progress, cancel) -> None:
    import copy

    import pyzipper

    kw = {"encryption": pyzipper.WZ_AES} if password else {}
    with pyzipper.AESZipFile(src) as zin, pyzipper.AESZipFile(tmp, "w", **kw) as zout:
        if password:
            zin.setpassword(password.encode())
            zout.setpassword(password.encode())
        infos = [i for i in zin.infolist() if not dropped(i.filename)]
        total = len(infos) + len(adds) or 1
        for n, info in enumerate(infos):
            if cancel.is_set():
                raise ConversionCancelled()
            new = copy.copy(info)
            if info.is_dir():
                zout.writestr(new, b"")
            else:
                try:
                    with zin.open(info) as fin, zout.open(new, "w") as fout:
                        shutil.copyfileobj(fin, fout, 1 << 20)
                except RuntimeError as exc:
                    if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                        raise _wrong_password(src.name) from exc
                    raise
            progress((n + 1) / total)
        for n, (path, name) in enumerate(adds, len(infos)):
            if cancel.is_set():
                raise ConversionCancelled()
            zout.write(path, name, compress_type=pyzipper.ZIP_DEFLATED)
            progress((n + 1) / total)


def _rewrite_tar(src, tmp, ext, dropped, adds, progress, cancel) -> None:
    mode = TAR_MODES[ext]
    with tarfile.open(src) as tin, tarfile.open(tmp, f"w:{mode}", format=tin.format) as tout:
        members = [m for m in tin.getmembers() if not dropped(m.name)]
        total = len(members) + len(adds) or 1
        for n, m in enumerate(members):
            if cancel.is_set():
                raise ConversionCancelled()
            if m.isfile():
                fin = tin.extractfile(m)
                with fin:
                    tout.addfile(m, fin)
            else:  # folders, links, devices: the header is all there is
                tout.addfile(m)
            progress((n + 1) / total)
        for n, (path, name) in enumerate(adds, len(members)):
            if cancel.is_set():
                raise ConversionCancelled()
            tout.add(path, arcname=name, recursive=False)
            progress((n + 1) / total)


def _rewrite_7z(src, tmp, password, contents, dropped, adds, progress, cancel) -> None:
    if contents.links:
        raise ConversionError(
            f"“{src.name}” holds links, which would be lost: it can't be changed here. "
            "Extract it, change the files and pack them again instead."
        )
    keep = [e for e in contents.entries if not dropped(e.name)]
    with tempfile.TemporaryDirectory(prefix="umc-archive-") as work:
        work_dir = Path(work)
        names = [e.name for e in keep if not e.is_dir]
        if names:
            extract(src, "7z", work_dir, password, lambda f: progress(f * 0.5), cancel, only=names)
        files: list[tuple[Path, str]] = []
        for e in keep:
            path = _safe_target(work_dir, e.name)
            if path is None:
                continue
            if e.is_dir:
                path.mkdir(parents=True, exist_ok=True)
            elif not path.is_file():
                continue
            _set_mtime(path, e.modified.timestamp() if e.modified else None)
            files.append((path, e.name))
        if cancel.is_set():
            raise ConversionCancelled()
        create(
            files + adds,
            tmp,
            "7z",
            "normal",
            password if contents.encrypted else "",
            contents.names_hidden,
            lambda f: progress(0.5 + f * 0.5),
            cancel,
        )


# --------------------------------------------------------------------------- writing


def create(
    entries: list[tuple[Path, str]],
    dest: Path,
    ext: str,
    level: str,
    password: str,
    encrypt_names: bool,
    progress: Progress,
    cancel: threading.Event,
) -> Path:
    """Write ``entries`` (file on disk, name inside the archive) to ``dest``."""
    zlvl, sevenz, gzlvl, xzlvl = LEVELS[level]
    try:
        if ext == "zip":
            import pyzipper

            method = pyzipper.ZIP_STORED if zlvl == 0 else pyzipper.ZIP_DEFLATED
            kw = {"encryption": pyzipper.WZ_AES} if password else {}
            with pyzipper.AESZipFile(
                dest, "w", compression=method, compresslevel=zlvl or None, **kw
            ) as zf:
                if password:
                    zf.setpassword(password.encode())
                for n, (path, name) in enumerate(entries):
                    if cancel.is_set():
                        raise ConversionCancelled()
                    zf.write(path, name)
                    progress((n + 1) / len(entries))
        elif ext == "7z":
            import py7zr

            filters = (
                [{"id": py7zr.FILTER_COPY}]
                if sevenz == 0
                else [{"id": py7zr.FILTER_LZMA2, "preset": sevenz}]
            )
            if password:
                filters.append({"id": py7zr.FILTER_CRYPTO_AES256_SHA256})
            with py7zr.SevenZipFile(
                dest,
                "w",
                password=password or None,
                filters=filters,
                header_encryption=bool(password and encrypt_names),
            ) as zf:
                for n, (path, name) in enumerate(entries):
                    if cancel.is_set():
                        raise ConversionCancelled()
                    zf.write(path, name)
                    progress((n + 1) / len(entries))
        elif ext in TAR_MODES:
            mode = TAR_MODES[ext]
            kw: dict = {}
            if mode in ("gz", "bz2"):
                kw["compresslevel"] = max(gzlvl, 1)
            elif mode == "xz":
                kw["preset"] = xzlvl
            with tarfile.open(dest, f"w:{mode}", format=tarfile.PAX_FORMAT, **kw) as tf:
                for n, (path, name) in enumerate(entries):
                    if cancel.is_set():
                        raise ConversionCancelled()
                    tf.add(path, arcname=name, recursive=False)
                    progress((n + 1) / len(entries))
        else:
            raise ConversionError(f"Can't create {ext.upper()} archives.")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def list_names(path: Path, ext: str, password: str = "") -> list[str]:
    """Names of the regular files inside an archive (used by tests)."""
    if ext == "zip":
        import pyzipper

        with pyzipper.AESZipFile(path) as zf:
            return [i.filename for i in zf.infolist() if not i.is_dir()]
    if ext == "7z":
        import py7zr

        with py7zr.SevenZipFile(path, password=password or None) as zf:
            return [e.filename for e in zf.list() if not e.is_directory]
    if ext in TAR_MODES:
        with tarfile.open(path) as tf:
            return [m.name for m in tf.getmembers() if m.isfile()]
    tool = bsdtar_path()
    if tool:
        out = subprocess.run([tool, "-tf", str(path)], capture_output=True, text=True, check=True)
        return [n for n in out.stdout.splitlines() if n and not n.endswith("/")]
    return []
