"""How a missing external program is installed on Linux (no Qt).

Windows gets the one-click installer (:mod:`umc.tools.installer`). On Debian / Ubuntu the
distribution's own packages are the right way: the tool dialog shows the ``sudo apt install``
command to copy. Other distributions (or a tool apt doesn't carry) get the download link.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Tool name (as raised in ToolMissingError) → Debian / Ubuntu packages that provide it.
# LibreDWG isn't packaged by Ubuntu, so it has no entry (the dialog offers the download).
APT_PACKAGES: dict[str, tuple[str, ...]] = {
    "LibreOffice": ("libreoffice",),
    "Pandoc": ("pandoc",),
    "Ghostscript": ("ghostscript",),
    "Calibre": ("calibre",),
    "FFmpeg": ("ffmpeg",),
    "FFmpeg (ffprobe)": ("ffmpeg",),
}


def has_apt() -> bool:
    """This is a Debian-style system (apt is there to install packages with)."""
    return (
        sys.platform.startswith("linux")
        and shutil.which("apt") is not None
        and Path("/etc/debian_version").exists()
    )


def apt_command(tool: str) -> str | None:
    """The command that installs ``tool`` with apt, or None (no package for it)."""
    packages = APT_PACKAGES.get(tool)
    return f"sudo apt install {' '.join(packages)}" if packages else None


def install_command(tool: str) -> str | None:
    """The command to show for ``tool`` on this system, or None (offer the download)."""
    return apt_command(tool) if has_apt() else None
