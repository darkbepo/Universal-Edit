"""Per-user folders the app writes to."""

from __future__ import annotations

import contextlib
import functools
import os
import sys
from pathlib import Path

APP_DIR_NAME = "UniversalEdit"
OLD_APP_DIR_NAME = "UniversalMediaConverter"  # the app's name before it became Universal Edit

# What the move from the old name did, logged by the app once logging is set up (on Windows the
# log file lives in the folder being moved, so nothing can be logged while moving it).
MIGRATION_NOTES: list[str] = []


@functools.cache
def migrated_dir(base: Path) -> Path:
    """``base/UniversalEdit``, after moving what ``base/UniversalMediaConverter`` (the old name)
    still holds: settings, installed tools such as LibreOffice (~1 GB), logs.

    Worked out once per process. The whole folder is renamed when possible; if the new folder
    already exists (another copy starting at the same moment, or something else created it) the
    old folder's entries that the new one lacks are moved one by one. Anything in use stays behind
    and is moved on a later start (``tools_dir`` keeps finding tools left in the old folder)."""
    new, old = base / APP_DIR_NAME, base / OLD_APP_DIR_NAME
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
            MIGRATION_NOTES.append(f"moved {old} to {new}")
        except OSError:  # in use, or another copy just moved it
            pass
    if old.is_dir():
        new.mkdir(parents=True, exist_ok=True)
        for child in sorted(old.iterdir()):
            target = new / child.name
            if target.exists():
                continue
            try:
                child.rename(target)
                MIGRATION_NOTES.append(f"moved {child} to {target}")
            except OSError as e:
                MIGRATION_NOTES.append(f"could not move {child} yet ({e}); trying again next start")
        with contextlib.suppress(OSError):
            old.rmdir()  # only when empty
    new.mkdir(parents=True, exist_ok=True)
    return new


def _data_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\UniversalEdit, ~/Library/Application Support/…, ~/.local/share/…"""
    return migrated_dir(_data_base())


def tools_dir() -> Path:
    """Where tools installed by the app itself (LibreOffice, Pandoc) live."""
    override = os.environ.get("UMC_TOOLS_DIR")  # used by tests
    if override:
        path = Path(override)
    else:
        path = app_data_dir() / "tools"
        left = _data_base() / OLD_APP_DIR_NAME / "tools"
        if not path.exists() and left.is_dir():  # couldn't be moved yet (a tool was running)
            return left
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_in_tools(subdir: str, exe_name: str) -> str | None:
    """Locate ``exe_name`` anywhere below ``tools_dir()/subdir`` (layouts vary by version)."""
    root = tools_dir() / subdir
    if not root.is_dir():
        return None
    for candidate in sorted(root.rglob(exe_name)):
        if candidate.is_file():
            return str(candidate)
    return None
