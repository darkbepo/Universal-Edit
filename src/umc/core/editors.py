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
    EditorInfo("video", "Video editor", "Trim, join and mix clips", "movie", True),
    EditorInfo("audio", "Audio editor", "Cut, fade and clean up sound", "music", True),
    EditorInfo("document", "Document editor", "Write and format documents", "document", True),
    EditorInfo("code", "Code editor", "Quick edits to code and text files", "code", True),
    EditorInfo("model", "3D viewer", "Look at and measure 3D models", "cube", True),
    EditorInfo("vector", "Vector editor", "Edit shapes and paths in SVG drawings", "vector", True),
    EditorInfo("font", "Font editor", "Change letters, spacing and font names", "font", False),
    EditorInfo("subtitle", "Subtitle editor", "Fix subtitle text and timing", "subtitle", True),
    EditorInfo("data", "Data editor", "Edit CSV, JSON, XML and spreadsheets", "table", True),
)

COMING_SOON = "Coming soon"

# Editors the website doesn't have yet: its tile stays "Coming soon" (available false there).
WEB_LATER: frozenset[str] = frozenset()

# The data editor (a table): Edit opens these in it first …
DATA_FIRST: frozenset[str] = frozenset({"csv", "tsv", "xlsx"})
# … and offers it under Edit's ▾ for these (the code editor stays first: they're often configs).
DATA_TOO: frozenset[str] = frozenset({"json", "yaml", "xml"})

# Start-screen tile rows (owner, Nov 2026): as many tiles per row as fit, spread evenly over the
# rows so no tile is left alone, each row centred; the tiles stretch to fill the width.
MIN_TILE_W = 260
TILE_GAP = 10


def hub_rows(
    count: int, width: float, min_tile: int = MIN_TILE_W, gap: int = TILE_GAP
) -> list[int]:
    """How many tiles go in each row for ``count`` tiles in ``width`` px (same in editors.ts)."""
    if count <= 0:
        return []
    fit = max(1, int((width + gap) // (min_tile + gap)))
    rows = -(-count // fit)
    base, extra = divmod(count, rows)
    return [base + 1] * extra + [base] * (rows - extra)


def hub_tile_width(rows: list[int], width: float, gap: int = TILE_GAP) -> float:
    """Every tile's width: the longest row fills ``width``."""
    most = max(rows, default=1)
    return max(1.0, (width - gap * (most - 1)) / most)


# The vector editor opens these (Edit offers the code editor too, under ▾).
VECTOR_EXTS: frozenset[str] = frozenset({"svg", "svgz"})
