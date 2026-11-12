"""A new, empty picture for the image editor (started from the editor tiles, without a file).

Same presets and backgrounds as ``web/src/editor/blank.ts``."""

from __future__ import annotations

from PIL import Image

from umc.editor.document import Document, check_size
from umc.editor.layer import Layer

# (label, width, height); the first is the default
PRESETS: list[tuple[str, int, int]] = [
    ("Full HD (1920 × 1080)", 1920, 1080),
    ("Square (1080 × 1080)", 1080, 1080),
    ("Phone story (1080 × 1920)", 1080, 1920),
    ("HD (1280 × 720)", 1280, 720),
    ("4K (3840 × 2160)", 3840, 2160),
    ("A4 print, 300 dpi (2480 × 3508)", 2480, 3508),
]

# id → (label, RGBA fill)
BACKGROUNDS: dict[str, tuple[str, tuple[int, int, int, int]]] = {
    "white": ("White", (255, 255, 255, 255)),
    "transparent": ("Transparent", (0, 0, 0, 0)),
    "black": ("Black", (0, 0, 0, 255)),
}


def blank_document(width: int, height: int, background: str = "white") -> Document:
    """One "Background" layer of ``width`` × ``height`` filled with ``background``."""
    check_size(width, height)
    fill = BACKGROUNDS[background][1]
    return Document(width, height, [Layer("Background", Image.new("RGBA", (width, height), fill))])
