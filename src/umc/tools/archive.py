"""Archive reading and writing: ZIP, 7Z, TAR (+ gz/xz/bz2), and RAR (read-only).

ZIP: pyzipper (reads ZipCrypto + AES, writes AES-256). 7Z: py7zr. TAR: tarfile. RAR is read with
bsdtar (libarchive, BSD) — built into Windows 10+ (System32\\tar.exe) and macOS (/usr/bin/tar);
on Linux it's the ``bsdtar`` package. No unRAR code is used (its licence isn't AGPL-compatible).

Extraction is defensive: absolute paths, ``..`` and links are never written outside the target.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from collections.abc import Callable
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


def _wrong_password(name: str) -> ConversionError:
    return ConversionError(f"“{name}” is password-protected. Enter the right password in Settings.")


# --------------------------------------------------------------------------- reading


def extract(
    src: Path, ext: str, dest: Path, password: str, progress: Progress, cancel: threading.Event
) -> list[Path]:
    """Extract every regular file of ``src`` into ``dest``; return the written paths."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if ext == "zip":
            return _extract_zip(src, dest, password, progress, cancel)
        if ext == "7z":
            return _extract_7z(src, dest, password, progress, cancel)
        if ext in TAR_MODES:
            return _extract_tar(src, dest, progress, cancel)
        if ext == "rar":
            return _extract_bsdtar(src, dest, password, cancel)
    except ConversionError:
        raise
    except (OSError, EOFError, tarfile.TarError, ValueError, RuntimeError) as exc:
        raise ConversionError(
            f"Couldn't open “{src.name}”. Is it a complete, valid archive?", details=repr(exc)
        ) from exc
    raise ConversionError(f"{ext.upper()} archives can't be opened.")


def _extract_zip(src, dest, password, progress, cancel) -> list[Path]:
    import pyzipper

    out = []
    with pyzipper.AESZipFile(src) as zf:
        if password:
            zf.setpassword(password.encode())
        infos = [i for i in zf.infolist() if not i.is_dir()]
        for n, info in enumerate(infos):
            if cancel.is_set():
                raise ConversionCancelled()
            # a symlink stored by Unix zip tools has S_IFLNK in the high attribute bits
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            target = _safe_target(dest, info.filename)
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
            out.append(target)
            progress((n + 1) / len(infos))
    return out


def _extract_7z(src, dest, password, progress, cancel) -> list[Path]:
    import py7zr

    try:
        with py7zr.SevenZipFile(src, password=password or None) as zf:
            if zf.needs_password() and not password:
                raise _wrong_password(src.name)
            entries = [e for e in zf.list() if not e.is_directory and not e.is_symlink]
            names = [e.filename for e in entries if _safe_target(dest, e.filename) is not None]
            if cancel.is_set():
                raise ConversionCancelled()
            zf.extract(path=dest, targets=names)
    except py7zr.exceptions.PasswordRequired as exc:
        raise _wrong_password(src.name) from exc
    except (py7zr.exceptions.Bad7zFile, py7zr.exceptions.CrcError) as exc:
        if password:
            raise ConversionError(
                f"Couldn't open “{src.name}” — wrong password?", details=repr(exc)
            ) from exc
        raise
    progress(1.0)
    return [p for n in names if (p := _safe_target(dest, n)) and p.is_file()]


def _extract_tar(src, dest, progress, cancel) -> list[Path]:
    out = []
    with tarfile.open(src) as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        for n, m in enumerate(members):
            if cancel.is_set():
                raise ConversionCancelled()
            target = _safe_target(dest, m.name)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fin = tf.extractfile(m)
            if fin is None:
                continue
            with fin, target.open("wb") as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
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


def _extract_bsdtar(src, dest, password, cancel) -> list[Path]:
    from umc.tools import process

    tool = bsdtar_path()
    if tool is None:
        raise ConversionError(
            "Opening RAR files needs “bsdtar”. It's built into Windows 10/11 and macOS; "
            "on Linux install the “libarchive-tools” (or “bsdtar”) package."
        )
    staging = dest / ".umc-extract"
    staging.mkdir(parents=True, exist_ok=True)
    args = [tool, "-x", "-f", str(src), "-C", str(staging), "--no-same-owner"]
    if password:
        args += ["--passphrase", password]
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
        rel = p.relative_to(staging).as_posix()
        target = _safe_target(dest, rel)
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
