"""Headless LibreOffice conversions.

LibreOffice is not bundled (it's large). If it's missing we raise :class:`ToolMissingError`,
which the GUI turns into a "Download LibreOffice / check again" dialog.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from umc.core.errors import ConversionError, ToolMissingError
from umc.tools import process

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
    return process.find_executable(["soffice", "libreoffice"], _EXTRA_PATHS)


def is_available() -> bool:
    return soffice_path() is not None


def require() -> str:
    path = soffice_path()
    if path is None:
        raise ToolMissingError("LibreOffice", DOWNLOAD_URL, "convert office documents")
    return path


def convert(
    src: Path,
    out_ext: str,
    dest: Path,
    *,
    cancel_event: threading.Event,
    export_filter: str | None = None,
    import_filter: str | None = None,
    timeout: float = 600,
) -> Path:
    """Convert ``src`` to ``dest`` (whose extension is ``out_ext``)."""
    soffice = require()
    with tempfile.TemporaryDirectory(prefix="umc-lo-") as tmp:
        tmp_path = Path(tmp)
        profile = tmp_path / "profile"
        outdir = tmp_path / "out"
        outdir.mkdir()
        convert_to = f"{out_ext}:{export_filter}" if export_filter else out_ext
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
        with _lock:
            process.run(
                cmd,
                cancel_event=cancel_event,
                timeout=timeout,
                error_message=f"LibreOffice couldn't convert “{src.name}”.",
            )
        produced = list(outdir.iterdir())
        if not produced:
            raise ConversionError(
                f"LibreOffice couldn't convert “{src.name}” to {out_ext.upper()}."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced[0]), dest)
    return dest
