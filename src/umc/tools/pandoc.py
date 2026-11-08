"""Pandoc conversions for text-like formats (md, html, epub, docx, odt, rst, …).

Like LibreOffice, Pandoc is detected rather than bundled; missing → download prompt.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from umc.core.errors import ToolMissingError
from umc.tools import process

DOWNLOAD_URL = "https://pandoc.org/installing.html"

_EXTRA_PATHS = [
    r"%LOCALAPPDATA%\Pandoc\pandoc.exe",
    r"%ProgramFiles%\Pandoc\pandoc.exe",
    "/opt/homebrew/bin/pandoc",
    "/usr/local/bin/pandoc",
]

# canonical extension → pandoc format name
READERS = {
    "md": "markdown",
    "html": "html",
    "epub": "epub",
    "docx": "docx",
    "odt": "odt",
    "rst": "rst",
    "tex": "latex",
    "org": "org",
    "txt": "markdown",
    "fb2": "fb2",
}
WRITERS = {
    "md": "gfm",
    "html": "html",
    "epub": "epub3",
    "docx": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "txt": "plain",
    "rst": "rst",
    "tex": "latex",
    "org": "org",
}
_STANDALONE = {"html", "rtf", "tex", "epub", "docx", "odt"}


def pandoc_path() -> str | None:
    return process.find_executable(["pandoc"], _EXTRA_PATHS)


def is_available() -> bool:
    return pandoc_path() is not None


def convert(
    src: Path,
    src_ext: str,
    dest: Path,
    dst_ext: str,
    *,
    cancel_event: threading.Event,
    toc: bool = False,
    extra: Sequence[str] = (),
) -> Path:
    src, dest = src.resolve(), dest.resolve()
    exe = pandoc_path()
    if exe is None:
        raise ToolMissingError("Pandoc", DOWNLOAD_URL, f"convert to {dst_ext.upper()}")
    cmd = [exe, "-f", READERS[src_ext], "-t", WRITERS[dst_ext], "-o", str(dest), str(src)]
    if dst_ext in _STANDALONE:
        cmd.append("--standalone")
    if dst_ext == "html":
        cmd.append("--embed-resources")
        cmd += ["--metadata", f"pagetitle={src.stem}"]
    if dst_ext == "epub":
        cmd += ["--metadata", f"title={src.stem}"]
    if dst_ext in ("md", "rst", "org", "tex"):
        cmd.append(f"--extract-media={dest.parent / (dest.stem + '_media')}")
    if toc:
        cmd.append("--toc")
    cmd += list(extra)
    process.run(
        cmd,
        cancel_event=cancel_event,
        cwd=src.parent,
        error_message=f"Pandoc couldn't convert “{src.name}”.",
    )
    return dest
