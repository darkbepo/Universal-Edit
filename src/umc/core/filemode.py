"""Permissions for files saved through a temp file + ``os.replace``.

``tempfile.mkstemp`` makes a file only its owner can read (0600). Swapped in place of the
user's file, it would lock out everyone else (another user, a media server, a web server), so
``match_mode`` gives the temp file the target's permissions, or, for a new file, the ones any
program would give it (0666 minus the user's umask)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

DEFAULT_UMASK = 0o022


def current_umask() -> int:
    """The process's umask, read without changing it (os.umask would race other threads)."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Umask:"):
                return int(line.split()[1], 8)
    return DEFAULT_UMASK


def match_mode(tmp: str | os.PathLike, target: str | os.PathLike) -> None:
    """Give ``tmp`` the permissions ``target`` has (or a new file would get)."""
    try:
        mode = Path(target).stat().st_mode & 0o7777
    except OSError:
        if os.name == "nt":
            return  # Windows: no owner-only bits to undo
        mode = 0o666 & ~current_umask()
    with contextlib.suppress(OSError):
        os.chmod(tmp, mode)
