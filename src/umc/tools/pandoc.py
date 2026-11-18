"""Pandoc conversions for text-like formats (md, html, epub, docx, odt, rst, …).

Like LibreOffice, Pandoc is detected rather than bundled; missing → "Install it for me" dialog.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Sequence
from functools import cache
from pathlib import Path

from umc.core.errors import ToolMissingError
from umc.core.paths import find_in_tools
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
    private = find_in_tools("pandoc", "pandoc.exe" if process.IS_WINDOWS else "pandoc")
    return private or process.find_executable(["pandoc"], _EXTRA_PATHS)


def is_available() -> bool:
    return pandoc_path() is not None


@cache
def version(exe: str) -> tuple[int, ...] | None:
    """``exe``'s version (``pandoc 2.17.1.1`` → (2, 17, 1, 1)); None when it can't be read."""
    try:
        out = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=process._creationflags(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"pandoc(?:\.exe)? (\d+(?:\.\d+)*)", out)
    return tuple(int(n) for n in m.group(1).split(".")) if m else None


def embed_option(exe: str) -> str:
    """Pictures / styles inside the HTML: ``--embed-resources`` is Pandoc 2.19+; older ones
    (Debian 12 ships 2.17) call it ``--self-contained``."""
    v = version(exe)
    return "--self-contained" if v is not None and v < (2, 19) else "--embed-resources"


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
        cmd.append(embed_option(exe))
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
