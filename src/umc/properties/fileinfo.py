"""A file's name and its "modified" date (desktop only; Properties step 6).

``clean_name`` checks a typed name (a missing extension is added back; another extension is
refused — that would only relabel the file, Convert changes its type). ``parse_modified`` reads a
typed local date. ``apply`` renames the file (never over another one) and sets the date (the
access time is kept)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from umc.properties.video import DATE_RE, DATE_TEXT, parse_date

MAX_NAME = 255
BAD_CHARS = '<>:"/\\|?*'
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}  # fmt: skip
FIRST_YEAR, LAST_YEAR = 1980, 2099


class FileError(Exception):
    """A plain-language reason the name or date can't be used."""


def suffix_of(name: str) -> str:
    """ ".jpg" of "photo.jpg"; "" for "notes" or ".gitignore"."""
    stem, dot, ext = name.rpartition(".")
    return f".{ext}" if dot and stem and ext and " " not in ext else ""


def clean_name(text: str, old: Path) -> str:
    """The new file name for ``old`` (unchanged = ``old.name``); raises FileError."""
    name = text.strip()
    if not name:
        raise FileError("Type a name for the file.")
    if bad := sorted({c for c in name if c in BAD_CHARS or ord(c) < 32}):
        shown = " ".join("a line break" if ord(c) < 32 else c for c in bad)
        raise FileError(f"A file name can't contain {shown}.")
    if name in (".", "..") or name.endswith("."):
        raise FileError("A file name can't end with a dot.")
    old_ext = suffix_of(old.name)
    new_ext = suffix_of(name)
    if not new_ext and old_ext:
        name += old_ext  # "holiday" → "holiday.jpg"
    elif new_ext.lower() != old_ext.lower():
        keep = old_ext or "no extension"
        raise FileError(
            f"Keep the file's extension ({keep}): changing it doesn't change what's inside. "
            "Use Convert to make another type."
        )
    if name.split(".")[0].lower().rstrip() in RESERVED:
        raise FileError(f"Windows doesn't allow a file called “{name}”.")
    if len(name) > MAX_NAME:
        raise FileError(f"The name is too long (at most {MAX_NAME} characters).")
    return name


def shown_modified(path: Path) -> str:
    """The modified date as the card shows it (this computer's local time)."""
    return f"{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M:%S}"


def parse_modified(text: str) -> datetime:
    """A typed local date → a naive local datetime; raises FileError."""
    parts = parse_date(text) if DATE_RE.fullmatch(text.strip()) else None
    if parts is None:
        raise FileError(DATE_TEXT)
    if not FIRST_YEAR <= parts[0] <= LAST_YEAR:
        raise FileError(f"Pick a date between {FIRST_YEAR} and {LAST_YEAR}.")
    return datetime(*parts)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def apply(path: Path, name: str | None = None, modified: datetime | None = None) -> Path:
    """Rename ``path`` to ``name`` (None = keep) and/or set its modified date; the new path.
    Never replaces another file; a file another program holds open is reported plainly."""
    target = path
    try:
        if name is not None and name != path.name:
            target = path.with_name(name)
            # a different spelling of the same name on Windows / macOS is the file itself
            if (target.exists() or target.is_symlink()) and not _same_file(path, target):
                raise FileError(f"There's already a file called “{name}” in that folder.")
            os.rename(path, target)
        if modified is not None:
            st = target.stat()
            os.utime(target, ns=(st.st_atime_ns, int(modified.timestamp() * 1e9)))
    except FileError:
        raise
    except PermissionError as exc:
        raise FileError(
            f"“{path.name}” couldn't be changed — it may be open in another program, "
            "or the folder doesn't allow changes."
        ) from exc
    except FileNotFoundError as exc:
        raise FileError(f"“{path.name}” isn't there any more.") from exc
    except (OSError, OverflowError, ValueError) as exc:
        raise FileError(f"“{path.name}” couldn't be changed.") from exc
    return target
