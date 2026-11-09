"""Ghostscript (AGPL, like this app) for EPS / PostScript: render to PDF, and write EPS / PS.

Detected rather than bundled (like LibreOffice and Pandoc): private tools folder → PATH →
``C:\\Program Files\\gs\\gs*\\bin``. Missing → the "Install it for me" dialog.
"""

from __future__ import annotations

import glob
import os
import threading
from pathlib import Path

from umc.core.errors import ToolMissingError
from umc.core.paths import find_in_tools
from umc.tools import process

DOWNLOAD_URL = "https://ghostscript.com/releases/gsdnld.html"
_EXE = "gswin64c.exe" if process.IS_WINDOWS else "gs"


def gs_path() -> str | None:
    private = find_in_tools("ghostscript", _EXE)
    if private:
        return private
    found = process.find_executable(["gswin64c", "gswin32c", "gs"])
    if found:
        return found
    if process.IS_WINDOWS:
        for root in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", ""),
        ):
            hits = sorted(
                glob.glob(os.path.join(root, "gs", "gs*", "bin", "gswin64c.exe")), reverse=True
            )
            if hits:
                return hits[0]
    return None


def is_available() -> bool:
    return gs_path() is not None


def run(
    args: list[str], *, cancel_event: threading.Event, error_message: str, purpose: str
) -> None:
    exe = gs_path()
    if exe is None:
        raise ToolMissingError("Ghostscript", DOWNLOAD_URL, purpose)
    # -dSAFER: PostScript is a programming language; files must never touch disk or run commands.
    cmd = [exe, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", *args]
    process.run(cmd, cancel_event=cancel_event, timeout=600, error_message=error_message)


def to_pdf(src: Path, dest: Path, *, eps: bool, cancel_event: threading.Event) -> Path:
    """EPS (cropped to its bounding box) or PS (all pages) → PDF."""
    args = ["-sDEVICE=pdfwrite", "-dAutoRotatePages=/None"]
    if eps:
        args.append("-dEPSCrop")
    run(
        [*args, "-o", str(dest), str(src)],
        cancel_event=cancel_event,
        error_message=f"Couldn't read “{src.name}”. Is it a valid PostScript file?",
        purpose="open EPS / PostScript files",
    )
    return dest


def from_pdf(src: Path, dest: Path, fmt: str, *, cancel_event: threading.Event) -> Path:
    """PDF → EPS (first page) or PS (all pages), keeping vectors."""
    device = "eps2write" if fmt == "eps" else "ps2write"
    args = [f"-sDEVICE={device}"] + (["-dFirstPage=1", "-dLastPage=1"] if fmt == "eps" else [])
    run(
        [*args, "-o", str(dest), str(src)],
        cancel_event=cancel_event,
        error_message=f"Couldn't write {fmt.upper()}.",
        purpose=f"save as {fmt.upper()}",
    )
    return dest
