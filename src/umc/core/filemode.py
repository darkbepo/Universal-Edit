"""Permissions for files saved through a temp file + ``os.replace``.

``tempfile.mkstemp`` makes a file only its owner can read (0600). Swapped in place of the
user's file, it would lock out everyone else (another user, a media server, a web server), so
``match_mode`` gives the temp file the target's permissions, or, for a new file, the ones any
program would give it (0666 minus the user's umask). Windows has only a read-only flag, which
the temp file never has, so nothing is done there (copying it would make the temp file
impossible to clean up after a failed save)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def current_umask(folder: str | os.PathLike | None = None) -> int:
    """The process's umask, read without changing it (os.umask would race other threads):
    from /proc on Linux, else from a probe file the kernel creates in ``folder``."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Umask:"):
                return int(line.split()[1], 8)
    if folder is not None:
        probe = Path(folder) / f".umc-umask-{os.getpid()}-{id(object())}"
        with contextlib.suppress(OSError):
            fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            try:
                return 0o666 & ~os.fstat(fd).st_mode & 0o777
            finally:
                os.close(fd)
                probe.unlink(missing_ok=True)
    return 0o077  # unknown: stay as private as mkstemp made it


def match_mode(tmp: str | os.PathLike, target: str | os.PathLike) -> None:
    """Give ``tmp`` the permissions ``target`` has (or a new file would get). Only the
    read / write / run bits: setuid / setgid are never carried onto new content."""
    if os.name == "nt":
        return
    try:
        mode = Path(target).stat().st_mode & 0o777
    except OSError:
        mode = 0o666 & ~current_umask(Path(tmp).parent)
    with contextlib.suppress(OSError):
        os.chmod(tmp, mode)
