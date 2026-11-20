"""Browsing folders in the viewer (no Qt): what a folder holds, grouped by kind of file, the
Back / Forward history, Up, and what the address line understands.

A *location* is a folder (``Path``) or ``COMPUTER`` — on Windows the list of drives that Up
reaches from a drive's root (``C:\\``). On Linux / macOS Up stops at ``/``."""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

from umc.viewer import files as vf


class _Computer:
    """The Windows drive list (a location without a folder)."""

    def __repr__(self) -> str:
        return "COMPUTER"


COMPUTER = _Computer()
Location = Path | _Computer
COMPUTER_NAME = "This PC"


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    icon: str


FOLDERS = Category("folders", "Folders", "folder")
# The side list's groups, top to bottom (an empty group is hidden).
CATEGORIES: tuple[Category, ...] = (
    FOLDERS,
    Category("pictures", "Pictures", "photo"),
    Category("videos", "Videos", "movie"),
    Category("music", "Music", "music"),
    Category("documents", "Documents", "document"),
    Category("models", "3D models", "cube"),
    Category("text", "Text & code", "code"),
    Category("archives", "Archives", "archive"),
)
_CATEGORY_OF_KIND = {
    vf.IMAGE: "pictures",
    vf.VIDEO: "videos",
    vf.AUDIO: "music",
    vf.PAGES: "documents",
    vf.CONVERT: "documents",
    vf.MODEL: "models",
    vf.TEXT: "text",
    vf.ARCHIVE: "archives",
}


def category_of(path: Path) -> str | None:
    """The group a file goes in (None = the viewer can't show it)."""
    return _CATEGORY_OF_KIND.get(vf.kind_of_path(path))


@dataclass
class Listing:
    """What one location holds: sub-folders and viewable files, each in Explorer's order."""

    location: Location
    folders: list[Path] = field(default_factory=list)
    groups: dict[str, list[Path]] = field(default_factory=dict)  # category id → files

    def files(self) -> list[Path]:
        """Every file, in the order the side list shows them (group by group)."""
        return [p for c in CATEGORIES for p in self.groups.get(c.id, [])]

    def shown_categories(self) -> list[Category]:
        out = []
        for c in CATEGORIES:
            if (c is FOLDERS and self.folders) or self.groups.get(c.id):
                out.append(c)
        return out


def drives() -> list[Path]:
    """Windows: the drives that exist (``C:\\`` …). Elsewhere: ``/``."""
    if sys.platform != "win32":
        return [Path("/")]
    if hasattr(os, "listdrives"):  # Python 3.12+
        try:
            return [Path(d) for d in os.listdrives()]
        except OSError:
            pass
    return [Path(f"{c}:\\") for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def list_location(location: Location) -> Listing:
    """The sub-folders and viewable files of ``location`` (hidden ones skipped)."""
    listing = Listing(location)
    if location is COMPUTER:
        listing.folders = drives()
        return listing
    try:
        entries = list(Path(location).iterdir())
    except OSError:
        return listing
    for p in entries:
        if p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                listing.folders.append(p)
                continue
            if not p.is_file():
                continue
        except OSError:
            continue
        cat = category_of(p)
        if cat is not None:
            listing.groups.setdefault(cat, []).append(p)
    listing.folders.sort(key=lambda p: vf.natural_key(p.name))
    for paths in listing.groups.values():
        paths.sort(key=lambda p: vf.natural_key(p.name))
    return listing


def parent_of(location: Location) -> Location | None:
    """Where Up goes: the parent folder; a Windows drive root → the drive list; else None."""
    if location is COMPUTER:
        return None
    path = Path(location)
    if path.parent != path:
        return path.parent
    return COMPUTER if sys.platform == "win32" else None


def display(location: Location) -> str:
    """What the address line shows."""
    return COMPUTER_NAME if location is COMPUTER else str(location)


def resolve_address(text: str, current: Location) -> tuple[Location, Path | None] | None:
    """What the user typed in the address line → (folder to show, file to select there).

    Accepts quotes around the path, ``~`` and environment variables (``%USERPROFILE%``,
    ``$HOME``), a path relative to the folder shown, a file (its folder is shown and the file
    picked) and "This PC". None = nothing there."""
    text = text.strip().strip('"').strip("'").strip()
    if not text:
        return None
    if text.lower() == COMPUTER_NAME.lower() and sys.platform == "win32":
        return COMPUTER, None
    expanded = os.path.expandvars(os.path.expanduser(text))
    path = Path(expanded)
    if not path.is_absolute():
        if current is COMPUTER:
            return None
        path = Path(current) / path
    try:
        path = path.resolve()
        if path.is_dir():
            return path, None
        if path.is_file():
            return path.parent, path
    except OSError:
        return None
    return None


class History:
    """Back / Forward like a web browser: visiting a new place clears Forward."""

    def __init__(self) -> None:
        self._back: list[Location] = []
        self._forward: list[Location] = []
        self.current: Location | None = None

    def visit(self, location: Location) -> None:
        if location == self.current:
            return
        if self.current is not None:
            self._back.append(self.current)
        self._forward.clear()
        self.current = location

    def can_back(self) -> bool:
        return bool(self._back)

    def can_forward(self) -> bool:
        return bool(self._forward)

    def back(self) -> Location | None:
        if not self._back:
            return None
        if self.current is not None:
            self._forward.append(self.current)
        self.current = self._back.pop()
        return self.current

    def forward(self) -> Location | None:
        if not self._forward:
            return None
        if self.current is not None:
            self._back.append(self.current)
        self.current = self._forward.pop()
        return self.current


def step_index(order: list[Path], current: Path | None, delta: int, skip: set[Path]) -> int:
    """The index in ``order`` that ← / → (``delta``) lands on from ``current``, passing over
    files in ``skip`` (folded groups). Stays put at either end; -1 when nothing is left."""
    allowed = [i for i, p in enumerate(order) if p not in skip or p == current]
    if not allowed:
        return -1
    if current not in order:
        return allowed[0]
    here = allowed.index(order.index(current))
    return allowed[max(0, min(len(allowed) - 1, here + delta))]
