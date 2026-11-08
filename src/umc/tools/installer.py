"""One-click installation of LibreOffice and Pandoc (Windows).

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
import subprocess
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


def verify_authenticode(path: Path, publisher: str) -> None:
    """Require a valid Windows code signature whose signer name contains ``publisher``."""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:UMC_SIGNED_FILE; "
        "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            env={**_environ(), "UMC_SIGNED_FILE": str(path)},
            creationflags=process._creationflags(),
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError("Couldn't check the installer's signature.", details=repr(exc)) from exc
    status = out[0].strip() if out else ""
    subject = out[1] if len(out) > 1 else ""
    if status != "Valid" or publisher not in subject:
        raise InstallError(
            "The downloaded installer isn't correctly signed, so it wasn't installed.",
            details=f"status={status!r} subject={subject!r}",
        )


def _environ() -> dict[str, str]:
    import os

    return dict(os.environ)


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


INSTALLERS: dict[str, Callable[[Progress, threading.Event], str]] = {
    "LibreOffice": install_libreoffice,
    "Pandoc": install_pandoc,
}
