"""Per-user folders the app writes to."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "UniversalMediaConverter"


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\UniversalMediaConverter, ~/Library/Application Support/…, ~/.local/share/…"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


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
