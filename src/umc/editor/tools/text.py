"""Text: typed in an on-canvas box (GUI), committed as pixels on a new layer."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, LayerListCommand
from umc.editor.layer import Layer
from umc.editor.tools.base import Tool, ToolContext

PADDING = 2


@dataclass
class TextOptions:
    size: int = opt(48, "Size", kind="slider", min=6, max=500, unit="px")
    bold: bool = opt(False, "Bold")
    italic: bool = opt(False, "Italic")


def _font(family: str, size: int, bold: bool, italic: bool):
    from umc.editor.fonts import find_font

    path = find_font(family, bold, italic)
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    return ImageFont.load_default(size)


def render_text(
    text: str, family: str, size: int, bold: bool, italic: bool, color: tuple
) -> Image.Image:
    font = _font(family, size, bold, italic)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.multiline_textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (max(1, x1 - x0) + 2 * PADDING, max(1, y1 - y0) + 2 * PADDING))
    ImageDraw.Draw(img).multiline_text((PADDING - x0, PADDING - y0), text, font=font, fill=color)
    return img


def add_text_layer(
    doc: Document,
    text: str,
    x: float,
    y: float,
    family: str,
    size: int,
    bold: bool,
    italic: bool,
    color: tuple,
) -> Command | None:
    if not text.strip():
        return None
    img = render_text(text, family, size, bold, italic, color)
    name = text.strip().splitlines()[0][:30]
    before, active_before = list(doc.layers), doc.active
    doc.layers.insert(doc.active + 1, Layer(name, img, x=int(x), y=int(y)))
    doc.active += 1
    return LayerListCommand("Text", before, list(doc.layers), active_before, doc.active)


class TextTool(Tool):
    """Click to place text; the GUI shows an edit box at ``anchor`` and calls add_text_layer."""

    id, label, shortcut = "text", "Text", "T"
    options_cls = TextOptions
    cursor = "text"

    def __init__(self) -> None:
        self.anchor: tuple[float, float] | None = None

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self.anchor = (x, y)
        return None
