"""Quick text edits in the viewer (no Qt): a plain text file opens editable right where it's
shown; Save writes it back exactly as it was stored — same encoding, byte-order mark and line
endings (``codeedit.textfile``), so only the typed change differs. Bigger work goes to the
code editor (the viewer's Edit button).

Files over ``EDIT_LIMIT`` stay read-only (the viewer shows only their beginning)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.codeedit import textfile

EDIT_LIMIT = 5_000_000  # bytes: bigger text files are shown read-only


class SaveProblem(Exception):
    """Plain-language reason a save didn't happen (shown as it is)."""


@dataclass
class Opened:
    path: Path
    file: textfile.TextFile
    stamp: tuple[int, int]  # (size, mtime_ns) when read: was it changed by someone else?


def stamp_of(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def open_text(path: Path) -> Opened | None:
    """The file ready for editing, or None when it's too big or not text."""
    path = Path(path)
    try:
        if path.stat().st_size > EDIT_LIMIT:
            return None
        data = path.read_bytes()
        file = textfile.decode(data)
    except (OSError, ValueError):
        return None
    return Opened(path, file, stamp_of(path))


def changed_on_disk(opened: Opened) -> bool:
    try:
        return stamp_of(opened.path) != opened.stamp
    except OSError:
        return True


def save(opened: Opened, text: str) -> Opened:
    """Write ``text`` back in the file's own encoding / line endings (temp file + replace, so
    a failed write never leaves half a file). Returns the new state to keep editing from."""
    try:
        data = textfile.encode(opened.file, text)
    except ValueError:
        enc = opened.file.encoding
        raise SaveProblem(
            f"Some of the text can't be stored in this file's character set ({enc}). Use Edit "
            "to open it in the code editor, which can save it as UTF-8."
        ) from None
    path = opened.path
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with contextlib.suppress(OSError):
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise SaveProblem(f"The file couldn't be saved: {exc.strerror or exc}.") from exc
    return Opened(path, textfile.decode(data), stamp_of(path))
