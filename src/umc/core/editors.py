"""The editors shown as tiles on the empty start screen (same list in web/src/core/editors.ts).

``available`` False = shown greyed out as "Coming soon"."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorInfo:
    id: str
    title: str
    blurb: str
    icon: str  # Tabler icon name (gui/icon_data.py, web/src/ui/icon_data.ts)
    available: bool


EDITORS: tuple[EditorInfo, ...] = (
    EditorInfo("image", "Image editor", "Paint, layers, retouch and effects", "photo", True),
    EditorInfo("model", "3D viewer", "Look at and measure 3D models", "cube", True),
    EditorInfo("video", "Video editor", "Trim, join and mix clips", "movie", True),
    EditorInfo("audio", "Audio editor", "Cut, fade and clean up sound", "music", True),
    EditorInfo("document", "Document editor", "Write and format documents", "document", True),
    EditorInfo("code", "Code editor", "Quick edits to code and text files", "code", True),
)

COMING_SOON = "Coming soon"

# Editors the website doesn't have yet: its tile stays "Coming soon" (available false there).
WEB_LATER: frozenset[str] = frozenset({"video"})
