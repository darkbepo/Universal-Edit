"""LibreDWG (GPL-3, run as a separate program) to read AutoCAD DWG: ``dwg2dxf``.

Detected rather than bundled: private tools folder → PATH. Missing → the "Install it for me"
dialog (the official win64 zip from the LibreDWG GitHub release, SHA-256 checked).
"""

from __future__ import annotations

import threading
from pathlib import Path

from umc.core.errors import ConversionError, ToolMissingError
from umc.core.paths import find_in_tools
from umc.tools import process

DOWNLOAD_URL = "https://github.com/LibreDWG/libredwg/releases"
_EXE = "dwg2dxf.exe" if process.IS_WINDOWS else "dwg2dxf"


def dwg2dxf_path() -> str | None:
    return find_in_tools("libredwg", _EXE) or process.find_executable(["dwg2dxf"])


def is_available() -> bool:
    return dwg2dxf_path() is not None


def dwg_to_dxf(
    src: Path, dest: Path, *, cancel_event: threading.Event, exe: str | None = None
) -> Path:
    exe = exe or dwg2dxf_path()
    if exe is None:
        raise ToolMissingError("LibreDWG", DOWNLOAD_URL, "open DWG drawings")
    # -y: overwrite; -o: output file. LibreDWG writes a DXF of the same DWG version.
    process.run(
        [exe, "-y", "-o", str(dest), str(src)],
        cancel_event=cancel_event,
        timeout=600,
        error_message=f"Couldn't read “{src.name}”. Is it a valid DWG drawing?",
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise ConversionError(f"Couldn't read “{src.name}” (LibreDWG produced no drawing).")
    return dest
