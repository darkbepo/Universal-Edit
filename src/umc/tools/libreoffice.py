"""Headless LibreOffice conversions.

LibreOffice is not bundled (it's large). If it's missing we raise :class:`ToolMissingError`,
which the GUI turns into an "Install it for me" dialog (see :mod:`umc.tools.installer`).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from umc.core.errors import ConversionCancelled, ConversionError, ToolMissingError
from umc.core.paths import app_data_dir, find_in_tools
from umc.tools import process

log = logging.getLogger(__name__)

DOWNLOAD_URL = "https://www.libreoffice.org/download/download/"

_EXTRA_PATHS = [
    r"%ProgramFiles%\LibreOffice\program\soffice.exe",
    r"%ProgramFiles(x86)%\LibreOffice\program\soffice.exe",
    r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
]

# LibreOffice cannot run two conversions with the same profile at once.
_lock = threading.Lock()


def soffice_path() -> str | None:
    # A private copy installed by the app wins, then bundled/PATH/standard install folders.
    private = find_in_tools("libreoffice", "soffice.exe" if process.IS_WINDOWS else "soffice")
    return private or process.find_executable(["soffice", "libreoffice"], _EXTRA_PATHS)


def is_available() -> bool:
    return soffice_path() is not None


def require() -> str:
    path = soffice_path()
    if path is None:
        raise ToolMissingError("LibreOffice", DOWNLOAD_URL, "convert office documents")
    return path


def kept_profile_dir() -> Path:
    """A LibreOffice profile reused between conversions.

    A fresh profile per run means LibreOffice sets itself up from scratch every time: measured
    27 s per document with a fresh profile against 11 s with a kept one.
    """
    return app_data_dir() / "libreoffice-profile"


class _KeptProfile:
    """Claim the kept profile for one run, across app windows (LibreOffice can't share one)."""

    STALE_AFTER = 3600  # a claim older than this was left by a crash

    def __init__(self) -> None:
        self.dir = kept_profile_dir()
        self.claim = self.dir.parent / "libreoffice-profile.in-use"
        self.held = False

    def __enter__(self) -> Path | None:
        for _ in range(2):
            try:
                fd = os.open(self.claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.held = True
                return self.dir
            except FileExistsError:
                try:
                    if time.time() - self.claim.stat().st_mtime < self.STALE_AFTER:
                        return None  # in use by another window: use a temporary profile
                    self.claim.unlink()  # stale: take it over
                except OSError:
                    return None
            except OSError:
                return None
        return None

    def __exit__(self, *exc) -> None:
        if self.held:
            with contextlib.suppress(OSError):
                self.claim.unlink()


def convert(
    src: Path,
    out_ext: str,
    dest: Path,
    *,
    cancel_event: threading.Event,
    export_filter: str | None = None,
    import_filter: str | None = None,
    timeout: float = 600,
    soffice: str | None = None,
) -> Path:
    """Convert ``src`` to ``dest`` (whose extension is ``out_ext``)."""
    soffice = soffice or require()
    with tempfile.TemporaryDirectory(prefix="umc-lo-") as tmp:
        tmp_path = Path(tmp)
        outdir = tmp_path / "out"
        outdir.mkdir()
        convert_to = f"{out_ext}:{export_filter}" if export_filter else out_ext

        def run(profile: Path) -> None:
            cmd = [
                soffice,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--invisible",
                "--norestore",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
            ]
            if import_filter:
                cmd.append(f"--infilter={import_filter}")
            cmd += ["--convert-to", convert_to, "--outdir", str(outdir), str(src)]
            process.run(
                cmd,
                cancel_event=cancel_event,
                timeout=timeout,
                error_message=f"LibreOffice couldn't convert “{src.name}”.",
            )

        with _lock, _KeptProfile() as kept:
            if kept is None:
                run(tmp_path / "profile")
            else:
                try:
                    run(kept)
                except ConversionCancelled:
                    raise
                except ConversionError:
                    produced_now = list(outdir.iterdir())
                    if produced_now:
                        raise
                    # A damaged kept profile would break every conversion: reset it and retry
                    # once with a throwaway profile (the next run builds a fresh kept one).
                    log.warning(
                        "LibreOffice failed with the kept profile; retrying with a fresh one"
                    )
                    shutil.rmtree(kept, ignore_errors=True)
                    run(tmp_path / "profile")
        produced = list(outdir.iterdir())
        if not produced:
            raise ConversionError(
                f"LibreOffice couldn't convert “{src.name}” to {out_ext.upper()}."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced[0]), dest)
    return dest
