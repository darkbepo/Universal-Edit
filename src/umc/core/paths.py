"""Per-user folders the app writes to."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

APP_DIR_NAME = "UniversalEdit"
OLD_APP_DIR_NAME = "UniversalMediaConverter"  # the app's name before it became Universal Edit

log = logging.getLogger(__name__)


def migrated_dir(base: Path) -> Path:
    """``base/UniversalEdit``, moving ``base/UniversalMediaConverter`` there first if only the old
    folder exists (settings, installed tools such as LibreOffice, logs). If the old folder can't be
    moved (a file in it is in use), it keeps being used until a later start manages the move."""
    new, old = base / APP_DIR_NAME, base / OLD_APP_DIR_NAME
    if not new.exists() and old.is_dir():
        try:
            old.rename(new)
        except OSError:
            log.warning("could not move %s to %s; using the old folder for now", old, new)
            return old
    new.mkdir(parents=True, exist_ok=True)
    return new


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\UniversalEdit, ~/Library/Application Support/…, ~/.local/share/…"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return migrated_dir(base)


def tools_dir() -> Path:
    """Where tools installed by the app itself (LibreOffice, Pandoc) live."""
    override = os.environ.get("UMC_TOOLS_DIR")  # used by tests
    path = Path(override) if override else app_data_dir() / "tools"
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
