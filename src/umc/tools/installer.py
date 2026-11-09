"""One-click installation of LibreOffice, Pandoc, Ghostscript, Calibre and LibreDWG (Windows).

Both are installed *privately* into the app's tools folder (see :func:`paths.tools_dir`):

* **Pandoc** — the official Windows zip from GitHub, verified against the SHA-256 digest
  GitHub publishes for the release asset, then extracted.
* **LibreOffice** — the official MSI from download.documentfoundation.org. Its Authenticode
  signature must be valid and issued to The Document Foundation. It is unpacked with an
  *administrative install* (``msiexec /a``): no admin rights, no file-association changes,
  nothing outside our folder. We then run a real test conversion. If that copy can't run
  (e.g. a missing Visual C++ runtime), we fall back to a normal install (``msiexec /i``),
  for which Windows shows its usual "allow changes" prompt once.

No Qt here: progress goes through a ``progress(fraction, message)`` callback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from umc import __version__
from umc.core.errors import ConversionCancelled, ConversionError
from umc.core.paths import tools_dir
from umc.tools import process

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]

LO_INDEX = "https://download.documentfoundation.org/libreoffice/stable/"
LO_PUBLISHER = "The Document Foundation"
PANDOC_LATEST = "https://api.github.com/repos/jgm/pandoc/releases/latest"
USER_AGENT = f"UniversalMediaConverter/{__version__}"

# Free space needed (download + unpacked), with some headroom.
LO_SPACE_BYTES = 2_000_000_000
PANDOC_SPACE_BYTES = 400_000_000


class InstallError(ConversionError):
    pass


def supported(tool: str) -> bool:
    """Whether the app can install ``tool`` by itself on this computer."""
    return sys.platform == "win32" and tool in INSTALLERS


# --------------------------------------------------------------------------- helpers


def _get(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except OSError as exc:
        raise InstallError(
            "Couldn't reach the download server. Check your internet connection and try again.",
            details=f"{url}: {exc!r}",
        ) from exc


def download(
    url: str,
    dest: Path,
    *,
    progress: Progress,
    cancel_event: threading.Event,
    label: str,
    expected_sha256: str | None = None,
) -> Path:
    """Stream ``url`` to ``dest`` with progress; verify SHA-256 if given."""
    digest = hashlib.sha256()
    with _get(url, timeout=60) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel_event.is_set():
                raise ConversionCancelled()
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if total:
                progress(done / total, f"Downloading {label}… {done >> 20} of {total >> 20} MB")
            else:
                progress(0.0, f"Downloading {label}… {done >> 20} MB")
    if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise InstallError(
            f"The {label} download was damaged (checksum mismatch). Please try again.",
            details=f"expected {expected_sha256}, got {digest.hexdigest()}",
        )
    return dest


def _check_space(path: Path, needed: int, label: str) -> None:
    free = shutil.disk_usage(path).free
    if free < needed:
        raise InstallError(
            f"Not enough free disk space to install {label}: about {needed / 1e9:.1f} GB is "
            f"needed, {free / 1e9:.1f} GB is free."
        )


def _require_msi(path: Path) -> None:
    """MSI files are OLE compound documents; anything else (e.g. an HTML page) is rejected."""
    head = path.read_bytes()[:512] if path.stat().st_size < 4096 else path.open("rb").read(512)
    if not head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise InstallError(
            "The LibreOffice download wasn't a valid installer. Please try again later.",
            details=f"size={path.stat().st_size} head={head[:200]!r}",
        )


def verify_authenticode(path: Path, publisher: str) -> None:
    """Require a valid, trusted Windows code signature from ``publisher``."""
    try:
        from umc.tools import winsign

        trusted, hr = winsign.is_trusted(str(path))
        signer = winsign.signer_name(str(path))
    except (OSError, AttributeError, ValueError) as exc:
        raise InstallError("Couldn't check the installer's signature.", details=repr(exc)) from exc
    if not trusted or publisher not in signer:
        raise InstallError(
            "The downloaded installer isn't correctly signed, so it wasn't installed.",
            details=f"trusted={trusted} hresult=0x{hr:08X} signer={signer!r}",
        )
    log.info("signature OK: %s", signer)


# --------------------------------------------------------------------------- Pandoc


def latest_pandoc() -> tuple[str, str, str | None]:
    """Return ``(version, zip_url, sha256_or_None)`` of the newest Windows x86-64 build."""
    with _get(PANDOC_LATEST) as resp:
        release = json.load(resp)
    return pick_pandoc_asset(release)


def pick_pandoc_asset(release: dict) -> tuple[str, str, str | None]:
    version = str(release.get("tag_name", "")).lstrip("v")
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if re.fullmatch(r"pandoc-[\d.]+-windows-x86_64\.zip", name):
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return version, asset["browser_download_url"], sha
    raise InstallError(
        "Couldn't find a Windows download of Pandoc.", details=json.dumps(release)[:500]
    )


def install_pandoc(progress: Progress, cancel_event: threading.Event) -> str:
    target = tools_dir() / "pandoc"
    _check_space(tools_dir(), PANDOC_SPACE_BYTES, "Pandoc")
    progress(0.0, "Finding the latest Pandoc…")
    version, url, sha = latest_pandoc()
    with tempfile.TemporaryDirectory(prefix="umc-pandoc-", dir=tools_dir()) as tmp:
        archive = Path(tmp) / "pandoc.zip"
        download(
            url,
            archive,
            label=f"Pandoc {version}",
            expected_sha256=sha,
            progress=lambda f, m: progress(0.9 * f, m),
            cancel_event=cancel_event,
        )
        progress(0.92, "Unpacking Pandoc…")
        staging = Path(tmp) / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            _safe_extract(zf, staging)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(staging), target)
    from umc.core.paths import find_in_tools

    exe = find_in_tools("pandoc", "pandoc.exe")
    if exe is None:
        raise InstallError("Pandoc was downloaded but pandoc.exe wasn't found in it.")
    progress(1.0, f"Pandoc {version} installed.")
    return exe


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(dest):
            raise InstallError(
                "The Pandoc download contains unsafe paths.", details=member.filename
            )
    zf.extractall(dest)


# --------------------------------------------------------------------------- LibreOffice


def latest_libreoffice() -> tuple[str, str]:
    """Return ``(version, msi_url)`` of the newest stable Windows x86-64 build."""
    with _get(LO_INDEX) as resp:
        html = resp.read().decode("utf-8", "replace")
    version = pick_libreoffice_version(html)
    return version, libreoffice_msi_url(version)


def pick_libreoffice_version(index_html: str) -> str:
    versions = set(re.findall(r'href="(\d+\.\d+\.\d+)/"', index_html))
    if not versions:
        raise InstallError(
            "Couldn't find a LibreOffice version to download.", details=index_html[:300]
        )
    return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def libreoffice_msi_url(version: str) -> str:
    return f"{LO_INDEX}{version}/win/x86_64/LibreOffice_{version}_Win_x86-64.msi"


def install_libreoffice(progress: Progress, cancel_event: threading.Event) -> str:
    from umc.core.paths import find_in_tools
    from umc.tools import libreoffice

    root = tools_dir()
    _check_space(root, LO_SPACE_BYTES, "LibreOffice")
    progress(0.0, "Finding the latest LibreOffice…")
    version, url = latest_libreoffice()
    downloads = root / "downloads"
    downloads.mkdir(exist_ok=True)
    msi = downloads / url.rsplit("/", 1)[1]
    try:
        download(
            url,
            msi,
            label=f"LibreOffice {version}",
            progress=lambda f, m: progress(0.6 * f, m),
            cancel_event=cancel_event,
        )
        progress(0.61, "Checking the installer's signature…")
        _require_msi(msi)
        verify_authenticode(msi, LO_PUBLISHER)

        # 1) Private copy: unpack the MSI into our folder (no admin, no system changes).
        progress(0.63, "Installing LibreOffice (this takes a few minutes)…")
        target = root / "libreoffice"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        _msiexec(
            f'/a "{msi}" /qn TARGETDIR="{target}"',
            root / "libreoffice-unpack.log",
            cancel_event,
            "Unpacking LibreOffice failed.",
        )
        exe = find_in_tools("libreoffice", "soffice.exe")
        progress(0.9, "Testing LibreOffice…")
        if exe and _works(exe, cancel_event):
            progress(1.0, f"LibreOffice {version} installed.")
            return exe

        # 2) Fallback: a normal install (Windows asks for permission once).
        log.warning("private LibreOffice copy didn't run; falling back to a normal install")
        shutil.rmtree(target, ignore_errors=True)
        progress(0.92, "Finishing the installation — please allow it if Windows asks…")
        _msiexec(
            f'/i "{msi}" /passive /norestart ISCHECKFORPRODUCTUPDATES=0 QUICKSTART=0 '
            "REGISTER_NO_MSO_TYPES=1 REGISTER_ALL_MSO_TYPES=0",
            root / "libreoffice-install.log",
            cancel_event,
            "LibreOffice couldn't be installed. If Windows asked for permission, please allow it.",
        )
        exe = libreoffice.soffice_path()
        if exe is None or not _works(exe, cancel_event):
            raise InstallError("LibreOffice was installed but doesn't start on this computer.")
        progress(1.0, f"LibreOffice {version} installed.")
        return exe
    finally:
        msi.unlink(missing_ok=True)


def _msiexec(args: str, log_file: Path, cancel_event: threading.Event, message: str) -> None:
    # msiexec does its own command-line parsing, so build the string ourselves.
    cmd = f'msiexec {args} /L*v "{log_file}"'
    process.run(cmd, cancel_event=cancel_event, timeout=3600, error_message=message)
    # The verbose log is ~30 MB for LibreOffice; keep it only when msiexec failed (to debug).
    log_file.unlink(missing_ok=True)


def _works(soffice: str, cancel_event: threading.Event) -> bool:
    """Convert a tiny text file to PDF with this soffice to prove it really runs."""
    from umc.tools import libreoffice

    with tempfile.TemporaryDirectory(prefix="umc-lo-test-") as tmp:
        src = Path(tmp) / "test.txt"
        src.write_text("Universal Media Converter test\n", encoding="utf-8")
        try:
            libreoffice.convert(
                src,
                "pdf",
                Path(tmp) / "test.pdf",
                cancel_event=cancel_event,
                export_filter="writer_pdf_Export",
                soffice=soffice,
                timeout=300,
            )
        except ConversionCancelled:
            raise
        except ConversionError as exc:
            log.warning("LibreOffice test conversion failed: %s %s", exc, exc.details)
            return False
    return True


# --------------------------------------------------------------------------- Ghostscript

GS_LATEST = "https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest"
GS_PUBLISHER = "Artifex Software"
GS_SPACE_BYTES = 300_000_000


def pick_ghostscript_asset(release: dict) -> tuple[str, str, str | None]:
    """``(version, url, sha256_or_None)`` of the 64-bit Windows installer (gsNNNNNw64.exe)."""
    version = str(release.get("tag_name", "")).removeprefix("gs")
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if re.fullmatch(r"gs\d+w64\.exe", name):
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return version, asset["browser_download_url"], sha
    raise InstallError(
        "Couldn't find a Windows download of Ghostscript.", details=json.dumps(release)[:500]
    )


def install_ghostscript(progress: Progress, cancel_event: threading.Event) -> str:
    """Silent NSIS install into the private tools folder. Windows may ask for permission once."""
    from umc.tools import ghostscript

    root = tools_dir()
    _check_space(root, GS_SPACE_BYTES, "Ghostscript")
    progress(0.0, "Finding the latest Ghostscript…")
    with _get(GS_LATEST) as resp:
        version, url, sha = pick_ghostscript_asset(json.load(resp))
    downloads = root / "downloads"
    downloads.mkdir(exist_ok=True)
    exe = downloads / url.rsplit("/", 1)[1]
    target = root / "ghostscript"
    try:
        download(
            url,
            exe,
            label=f"Ghostscript {version}",
            expected_sha256=sha,
            progress=lambda f, m: progress(0.7 * f, m),
            cancel_event=cancel_event,
        )
        progress(0.72, "Checking the installer's signature…")
        verify_authenticode(exe, GS_PUBLISHER)
        progress(0.75, "Installing Ghostscript — please allow it if Windows asks…")
        shutil.rmtree(target, ignore_errors=True)
        # NSIS: /S = silent, /D= must be last and unquoted.
        args = f"/S /D={target}"
        try:
            process.run(
                f'"{exe}" {args}',
                cancel_event=cancel_event,
                timeout=900,
                error_message="Ghostscript couldn't be installed.",
            )
        except OSError as exc:  # 740 = the installer needs admin rights: ask Windows to elevate
            if getattr(exc, "winerror", None) != 740:
                raise
            ps = f"Start-Process -FilePath '{exe}' -ArgumentList '{args}' -Verb RunAs -Wait"
            process.run(
                ["powershell", "-NoProfile", "-Command", ps],
                cancel_event=cancel_event,
                timeout=900,
                error_message="Ghostscript couldn't be installed. "
                "If Windows asked for permission, please allow it.",
            )
        found = ghostscript.gs_path()
        if found is None:
            raise InstallError("Ghostscript was installed but gswin64c.exe wasn't found.")
        progress(1.0, f"Ghostscript {version} installed.")
        return found
    finally:
        exe.unlink(missing_ok=True)


# --------------------------------------------------------------------------- Calibre

# code.calibre-ebook.com serves an incomplete TLS chain (Python can't verify it), so the
# version comes from the GitHub releases API; the MSI from the release if attached, else from
# download.calibre-ebook.com. Either way its Authenticode signature is checked.
CALIBRE_LATEST = "https://api.github.com/repos/kovidgoyal/calibre/releases/latest"
CALIBRE_MSI = "https://download.calibre-ebook.com/{v}/calibre-64bit-{v}.msi"
CALIBRE_PUBLISHER = "Kovid Goyal"
CALIBRE_SPACE_BYTES = 1_200_000_000


def pick_calibre_asset(release: dict) -> tuple[str, str, str | None]:
    """``(version, msi_url, sha256_or_None)`` from a GitHub release of kovidgoyal/calibre."""
    version = str(release.get("tag_name", "")).lstrip("v")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        raise InstallError(
            "Couldn't find the latest Calibre version.", details=json.dumps(release)[:300]
        )
    for asset in release.get("assets", []):
        if asset.get("name") == f"calibre-64bit-{version}.msi":
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return version, asset["browser_download_url"], sha
    return version, CALIBRE_MSI.format(v=version), None


def latest_calibre() -> tuple[str, str, str | None]:
    with _get(CALIBRE_LATEST) as resp:
        return pick_calibre_asset(json.load(resp))


def install_calibre(progress: Progress, cancel_event: threading.Event) -> str:
    """Unpack the signed Calibre MSI privately (``msiexec /a``: no admin), then test it."""
    from umc.core.paths import find_in_tools
    from umc.tools import calibre

    root = tools_dir()
    _check_space(root, CALIBRE_SPACE_BYTES, "Calibre")
    progress(0.0, "Finding the latest Calibre…")
    version, url = latest_calibre()
    downloads = root / "downloads"
    downloads.mkdir(exist_ok=True)
    msi = downloads / url.rsplit("/", 1)[1]
    target = root / "calibre"
    try:
        download(
            url,
            msi,
            label=f"Calibre {version}",
            progress=lambda f, m: progress(0.7 * f, m),
            cancel_event=cancel_event,
        )
        progress(0.71, "Checking the installer's signature…")
        _require_msi(msi)
        verify_authenticode(msi, CALIBRE_PUBLISHER)
        progress(0.73, "Installing Calibre (this takes a minute)…")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        _msiexec(
            f'/a "{msi}" /qn TARGETDIR="{target}"',
            root / "calibre-unpack.log",
            cancel_event,
            "Unpacking Calibre failed.",
        )
        exe = find_in_tools("calibre", "ebook-convert.exe")
        if exe is None:
            raise InstallError("Calibre was unpacked but ebook-convert.exe wasn't found.")
        progress(0.95, "Testing Calibre…")
        with tempfile.TemporaryDirectory(prefix="umc-calibre-test-") as tmp:
            src = Path(tmp) / "test.txt"
            src.write_text("Universal Media Converter test\n", encoding="utf-8")
            calibre.convert(src, Path(tmp) / "test.epub", cancel_event=cancel_event, exe=exe)
        progress(1.0, f"Calibre {version} installed.")
        return exe
    finally:
        msi.unlink(missing_ok=True)


# --------------------------------------------------------------------------- LibreDWG

LIBREDWG_LATEST = "https://api.github.com/repos/LibreDWG/libredwg/releases/latest"


def pick_libredwg_asset(release: dict) -> tuple[str, str, str | None]:
    version = str(release.get("tag_name", "")).lstrip("v")
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if re.fullmatch(r"libredwg-[\d.]+-win64\.zip", name):
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return version, asset["browser_download_url"], sha
    raise InstallError(
        "Couldn't find a Windows download of LibreDWG.", details=json.dumps(release)[:500]
    )


def install_libredwg(progress: Progress, cancel_event: threading.Event) -> str:
    from umc.core.paths import find_in_tools

    target = tools_dir() / "libredwg"
    _check_space(tools_dir(), 200_000_000, "LibreDWG")
    progress(0.0, "Finding the latest LibreDWG…")
    with _get(LIBREDWG_LATEST) as resp:
        version, url, sha = pick_libredwg_asset(json.load(resp))
    with tempfile.TemporaryDirectory(prefix="umc-libredwg-", dir=tools_dir()) as tmp:
        archive = Path(tmp) / "libredwg.zip"
        download(
            url,
            archive,
            label=f"LibreDWG {version}",
            expected_sha256=sha,
            progress=lambda f, m: progress(0.9 * f, m),
            cancel_event=cancel_event,
        )
        progress(0.92, "Unpacking LibreDWG…")
        staging = Path(tmp) / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            _safe_extract(zf, staging)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(staging), target)
    exe = find_in_tools("libredwg", "dwg2dxf.exe")
    if exe is None:
        raise InstallError("LibreDWG was downloaded but dwg2dxf.exe wasn't found in it.")
    progress(1.0, f"LibreDWG {version} installed.")
    return exe


INSTALLERS: dict[str, Callable[[Progress, threading.Event], str]] = {
    "LibreDWG": install_libredwg,
    "Calibre": install_calibre,
    "Ghostscript": install_ghostscript,
    "LibreOffice": install_libreoffice,
    "Pandoc": install_pandoc,
}
