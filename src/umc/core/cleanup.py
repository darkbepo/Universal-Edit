"""Remove files the app left behind, so it never slowly fills the disk.

Normal runs clean up after themselves (temporary folders are context managers, downloaded
installers are deleted). This sweep catches what is left when the app was closed or crashed in
the middle of a conversion or an install, and old installer logs from earlier versions.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Old enough that no conversion or install still running in another window can be using it.
TEMP_MAX_AGE = 2 * 24 * 3600
# msiexec logs are only kept after a failed install, to debug it; a week is plenty.
LOG_MAX_AGE = 7 * 24 * 3600


def _newest_mtime(path: Path) -> float:
    newest = path.lstat().st_mtime
    if path.is_dir() and not path.is_symlink():
        for p in path.rglob("*"):
            with contextlib.suppress(OSError):
                newest = max(newest, p.lstat().st_mtime)
    return newest


def _size(path: Path) -> int:
    if path.is_dir() and not path.is_symlink():
        return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file())
    return path.lstat().st_size


def _remove(path: Path) -> int:
    try:
        size = _size(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return size
    except OSError as exc:
        log.info("couldn't remove leftover %s: %s", path, exc)
        return 0


def sweep_leftovers(
    temp_dir: Path | None = None, tools: Path | None = None, now: float | None = None
) -> int:
    """Delete stale ``umc-*`` temp folders, old installer logs and unfinished downloads.

    Returns the number of bytes freed.
    """
    now = time.time() if now is None else now
    temp_dir = Path(tempfile.gettempdir()) if temp_dir is None else temp_dir
    candidates: list[tuple[Path, float]] = []
    if temp_dir.is_dir():
        candidates += [(p, TEMP_MAX_AGE) for p in temp_dir.glob("umc-*")]
    if tools is not None and tools.is_dir():
        candidates += [(p, LOG_MAX_AGE) for p in tools.glob("*-unpack.log")]
        candidates += [(p, LOG_MAX_AGE) for p in tools.glob("*-install.log")]
        downloads = tools / "downloads"
        if downloads.is_dir():
            candidates += [(p, TEMP_MAX_AGE) for p in downloads.iterdir()]
    freed = 0
    for path, max_age in candidates:
        try:
            if now - _newest_mtime(path) < max_age:
                continue
        except OSError:
            continue
        freed += _remove(path)
    if freed:
        log.info("removed %.1f MB of leftover temporary files", freed / 1e6)
    return freed
