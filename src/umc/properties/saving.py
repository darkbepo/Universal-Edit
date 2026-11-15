"""Writing changed details back to disk (photos and music share it).

The original is replaced only once the new file is complete (a temp file next to it, then
``os.replace``), and keeps its permissions and modified date; a link is followed, so the file it
points to changes and the link stays. "Save as copy" writes ``name (1).ext`` next to it."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

TEMP_PREFIX = ".umc-saving-"  # the new file, next to the original, until it replaces it


def save_bytes(
    path: Path,
    change: Callable[[bytes], bytes],
    as_copy: bool,
    error: type[Exception],
) -> Path:
    """Write ``change(original bytes)`` into ``path`` (or a copy). ``change`` raises ``error``
    with a plain-language reason; a file that can't be replaced raises ``error`` too."""
    path = path.resolve()
    out = change(path.read_bytes())
    return save_with(path, lambda dest: dest.write_bytes(out), as_copy, error)


def save_with(
    path: Path,
    write: Callable[[Path], object],
    as_copy: bool,
    error: type[Exception],
) -> Path:
    """``write(dest)`` makes the new file at ``dest``: a copy next to ``path``, or a temp file
    that then replaces ``path``. If ``write`` raises, nothing is left behind."""
    from umc.core.job import unique_path

    path = path.resolve()
    if as_copy:
        stem = path.name[: -len(path.suffix)] if path.suffix else path.name
        dest = unique_path(path.parent, stem, path.suffix.lstrip("."))
        try:
            write(dest)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return dest
    st = path.stat()
    tmp = path.with_name(f"{TEMP_PREFIX}{path.name}")
    try:
        write(tmp)
        shutil.copymode(path, tmp)  # a private file stays private
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise error(
            f"“{path.name}” couldn't be changed — it may be open in another program, or the "
            "folder is read-only. Close it there and try again, or save a copy."
        ) from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
