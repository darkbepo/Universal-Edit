"""Calibre's ``ebook-convert`` (GPL-3, run as a separate program) for Kindle formats: MOBI, AZW3.

Detected rather than bundled (like LibreOffice): private tools folder → PATH → standard install
locations. Missing → the "Install it for me" dialog (tools/installer.py).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from umc.core.errors import ToolMissingError
from umc.core.paths import find_in_tools
from umc.tools import process

DOWNLOAD_URL = "https://calibre-ebook.com/download"
_EXE = "ebook-convert.exe" if process.IS_WINDOWS else "ebook-convert"
_EXTRA = [
    os.path.join(
        os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Calibre2", "ebook-convert.exe"
    ),
    "/Applications/calibre.app/Contents/MacOS/ebook-convert",
]


def ebook_convert_path() -> str | None:
    return find_in_tools("calibre", _EXE) or process.find_executable(["ebook-convert"], _EXTRA)


def is_available() -> bool:
    return ebook_convert_path() is not None


def convert(
    src: Path,
    dest: Path,
    *,
    cancel_event: threading.Event,
    title: str = "",
    authors: str = "",
    exe: str | None = None,
) -> Path:
    exe = exe or ebook_convert_path()
    if exe is None:
        raise ToolMissingError(
            "Calibre", DOWNLOAD_URL, f"save e-books as {dest.suffix[1:].upper()}"
        )
    cmd = [exe, str(src), str(dest)]
    if title.strip():
        cmd += ["--title", title.strip()]
    if authors.strip():
        cmd += ["--authors", authors.strip()]
    env_note = "Calibre couldn't convert this e-book."
    process.run(cmd, cancel_event=cancel_event, timeout=1800, error_message=env_note)
    return dest
