"""Text: typed in an on-canvas box (GUI), kept on its own layer that stays editable.

A text layer remembers how it was made (``TextSpec`` on ``Layer.text``), so clicking it with the
Text tool — or "Edit text…" in the layer menu — opens the text again; the layer is re-drawn from
the spec. Styles: alignment, an outline and a background box in the back colour, a drop shadow,
and a watermark (the text repeated diagonally across the whole picture, faint).
``web/src/editor/tools/text.ts`` is the website's port.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from PIL import Image, ImageDraw, ImageFont

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, LayerListCommand
from umc.editor.layer import Layer
from umc.editor.tools.base import Tool, ToolContext

PADDING = 2
ALIGNS = [("left", "Left"), ("center", "Centre"), ("right", "Right")]
WATERMARK_OPACITY = 0.3
WATERMARK_ANGLE = 30  # degrees, rising to the right


@dataclass
class TextOptions:
    size: int = opt(48, "Size", kind="slider", min=6, max=500, unit="px")
    bold: bool = opt(False, "Bold")
    italic: bool = opt(False, "Italic")
    align: str = opt("left", "Align", choices=ALIGNS)
    outline: int = opt(0, "Outline (back colour)", kind="slider", min=0, max=40, unit="px")
    shadow: bool = opt(False, "Drop shadow")
    box: bool = opt(False, "Box behind (back colour)")
    watermark: bool = opt(False, "Watermark (repeat across the picture)")


@dataclass(frozen=True)
class TextSpec:
    """Everything needed to draw a text layer again."""

    text: str
    font: str = "DejaVu Sans"
    size: int = 48
    bold: bool = False
    italic: bool = False
    color: tuple = (0, 0, 0, 255)
    align: str = "left"
    outline: int = 0
    outline_color: tuple = (255, 255, 255, 255)
    shadow: bool = False
    box: bool = False
    box_color: tuple = (255, 255, 255, 255)
    watermark: bool = False
    # where the clicked point lies inside the drawn image (outline / shadow / box extend
    # up and to the left of it), so re-drawing keeps the text in place
    ox: int = 0
    oy: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("color", "outline_color", "box_color"):
            d[k] = list(d[k])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TextSpec:
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: (tuple(v) if k.endswith("color") else v) for k, v in d.items() if k in known}
        return cls(**clean)


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
    text: str,
    family: str,
    size: int,
    bold: bool,
    italic: bool,
    color: tuple,
    align: str = "left",
    outline: int = 0,
    outline_color: tuple = (255, 255, 255, 255),
) -> Image.Image:
    """The text tightly cropped (2 px padding), optionally with an outline."""
    font = _font(family, size, bold, italic)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    kw = {"font": font, "align": align, "stroke_width": outline}
    bx0, by0, bx1, by1 = probe.multiline_textbbox((0, 0), text, **kw)
    # aligned lines can give fractional boxes: round outwards to whole pixels
    x0, y0, x1, y1 = math.floor(bx0), math.floor(by0), math.ceil(bx1), math.ceil(by1)
    img = Image.new("RGBA", (max(1, x1 - x0) + 2 * PADDING, max(1, y1 - y0) + 2 * PADDING))
    ImageDraw.Draw(img).multiline_text(
        (PADDING - x0, PADDING - y0), text, fill=color, stroke_fill=outline_color, **kw
    )
    return img


def render_spec(spec: TextSpec, canvas: tuple[int, int]) -> tuple[Image.Image, int, int]:
    """(image, ox, oy) for ``spec``: the clicked point sits at (ox, oy) inside the image.

    A watermark is canvas-sized and placed at the canvas origin (ox = oy = 0 is ignored).
    """
    from umc.editor.filters import Blur

    glyphs = render_text(
        spec.text,
        spec.font,
        spec.size,
        spec.bold,
        spec.italic,
        spec.color,
        spec.align,
        spec.outline,
        spec.outline_color,
    )
    if spec.watermark:
        return _watermark(glyphs, canvas), 0, 0
    pad = round(spec.size * 0.3) if spec.box else 0
    shadow = max(2, round(spec.size * 0.06)) if spec.shadow else 0
    w, h = glyphs.width + 2 * pad + 3 * shadow, glyphs.height + 2 * pad + 3 * shadow
    out = Image.new("RGBA", (w, h))
    gx, gy = pad + shadow, pad + shadow
    if spec.box:
        ImageDraw.Draw(out).rounded_rectangle(
            (
                shadow,
                shadow,
                shadow + glyphs.width + 2 * pad - 1,
                shadow + glyphs.height + 2 * pad - 1,
            ),
            radius=max(2, pad // 2),
            fill=spec.box_color,
        )
    if spec.shadow:
        shade = Image.new("RGBA", out.size)
        alpha = Image.new("L", out.size)
        alpha.paste(glyphs.getchannel("A"), (gx + shadow, gy + shadow))
        shade.putalpha(alpha.point(lambda v: v * 6 // 10))
        shade = Blur(shadow)(shade)
        out = (
            Image.alpha_composite(shade, out) if not spec.box else Image.alpha_composite(out, shade)
        )
    out.alpha_composite(glyphs, (gx, gy))
    return out, gx, gy


def _watermark(glyphs: Image.Image, canvas: tuple[int, int]) -> Image.Image:
    """``glyphs`` tilted and repeated across a canvas-sized image."""
    tile = glyphs.rotate(WATERMARK_ANGLE, expand=True, resample=Image.Resampling.BICUBIC)
    w, h = canvas
    out = Image.new("RGBA", (w, h))
    step_x, step_y = tile.width + glyphs.height * 2, tile.height + glyphs.height
    for row, y in enumerate(range(-tile.height // 2, h, max(1, step_y))):
        shift = (step_x // 2) * (row % 2)
        for x in range(-tile.width // 2 + shift, w, max(1, step_x)):
            out.paste(tile, (x, y), tile)  # tiles never overlap; works off the edges too
    return out


def _auto_name(spec: TextSpec) -> str:
    return spec.text.strip().splitlines()[0][:30]


def _text_layer(doc: Document, spec: TextSpec, x: float, y: float) -> Layer:
    img, ox, oy = render_spec(spec, (doc.width, doc.height))
    spec = replace(spec, ox=ox, oy=oy)
    name = _auto_name(spec)
    if spec.watermark:
        return Layer(name, img, 0, 0, WATERMARK_OPACITY, text=spec)
    return Layer(name, img, x=math.floor(x) - ox, y=math.floor(y) - oy, text=spec)


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
    **style,
) -> Command | None:
    """A new text layer above the active one; ``style`` = TextSpec fields (align, outline…)."""
    if not text.strip():
        return None
    spec = TextSpec(text, family, size, bold, italic, tuple(color), **style)
    layer = _text_layer(doc, spec, x, y)
    before, active_before, selected_before = list(doc.layers), doc.active, set(doc.selected)
    doc.layers.insert(doc.active + 1, layer)
    doc.active += 1
    doc.selected = set()
    return LayerListCommand(
        "Text", before, list(doc.layers), active_before, doc.active, selected_before, set()
    )


def anchor_of(layer: Layer) -> tuple[int, int]:
    """Where a text layer's text was placed (its clicked point), for editing it again."""
    spec = layer.text
    return (layer.x + spec.ox, layer.y + spec.oy) if spec is not None else (layer.x, layer.y)


def edit_text_layer(doc: Document, index: int, spec: TextSpec) -> Command | None:
    """Draw text layer ``index`` again from ``spec`` (empty text deletes the layer)."""
    from umc.editor import ops

    old = doc.layers[index]
    if not spec.text.strip():
        return ops.delete_layers(doc, [index])
    x, y = anchor_of(old)
    new = _text_layer(doc, spec, x, y)
    renamed = old.text is not None and old.name != _auto_name(old.text)
    keep_mask = old.mask is not None and old.mask.size == new.image.size
    new = replace(
        new,
        name=old.name if renamed else new.name,
        opacity=old.opacity if not spec.watermark else new.opacity,
        visible=old.visible,
        mask=old.mask if keep_mask else None,
    )
    return ops._replace(doc, index, "Edit text", new)


def painted_over(doc: Document, index: int) -> bool:
    """True when text layer ``index`` was changed with other tools since it was drawn (editing
    the text draws it again, so those changes would go)."""
    from PIL import ImageChops

    layer = doc.layers[index]
    if layer.text is None:
        return False
    img, ox, oy = render_spec(layer.text, (doc.width, doc.height))
    if img.size != layer.image.size:
        return True
    return ImageChops.difference(img, layer.image).getbbox() is not None


def text_layer_at(doc: Document, x: float, y: float) -> int | None:
    """The topmost visible text layer under (x, y), or None."""
    for i in range(len(doc.layers) - 1, -1, -1):
        la = doc.layers[i]
        editable = la.text is not None and la.visible and not la.text.watermark
        if editable and la.bounds.contains(int(x), int(y)):
            return i
    return None


class TextTool(Tool):
    """Click to place text (or click a text layer to edit it); the GUI shows the edit box."""

    id, label, shortcut = "text", "Text", "T"
    options_cls = TextOptions
    cursor = "text"

    def __init__(self) -> None:
        self.anchor: tuple[float, float] | None = None

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self.anchor = (x, y)
        return None
