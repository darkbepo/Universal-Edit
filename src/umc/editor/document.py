"""The editor document: a canvas size plus a stack of layers (index 0 = bottom)."""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageChops

from umc.core.errors import ConversionError
from umc.editor.geometry import Rect
from umc.editor.layer import Layer

MAX_SIDE = 16_000
MAX_PIXELS = 250_000_000
TOO_LARGE = "This image is too large to edit (max 16 000 px per side)."


def check_size(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise ConversionError("The image size must be at least 1 × 1 pixel.")
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ConversionError(TOO_LARGE)


def with_opacity(img: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return img
    r, g, b, a = img.split()
    a = a.point(lambda v: round(v * max(0.0, opacity)))
    return Image.merge("RGBA", (r, g, b, a))


@dataclass(eq=False)
class Document:
    width: int
    height: int
    layers: list[Layer]
    active: int = 0
    selection: Rect | None = None  # the rectangle around what is selected (canvas coordinates)
    # How much of each pixel in that rectangle is selected ("L", the rectangle's size); None =
    # all of it. See selection.py.
    selection_mask: Image.Image | None = None
    dirty: bool = field(default=False)
    # Layers selected in the layers list (several with Ctrl/Shift+click). The active layer is
    # always one of them: it's the one tools paint on. Not an undo step by itself.
    selected: set[int] = field(default_factory=set)

    @classmethod
    def new(cls, width: int, height: int) -> Document:
        check_size(width, height)
        return cls(width, height, [Layer("Background", Image.new("RGBA", (width, height)))])

    @property
    def active_layer(self) -> Layer:
        return self.layers[self.active]

    @property
    def selected_indices(self) -> list[int]:
        """Selected layer indices, bottom first; always includes the active layer."""
        n = len(self.layers)
        return sorted({i for i in self.selected if 0 <= i < n} | {self.active})

    @property
    def bounds(self) -> Rect:
        return Rect(0, 0, self.width, self.height)

    def pasteboard(self, step: int = 1) -> Rect:
        """The area the editor shows: the canvas plus the parts of visible layers lying around it
        (the "pasteboard" — shown, but never saved into the picture). Reaches at most half the
        canvas's longer side past each edge; with ``step`` > 1 it is rounded outwards to steps, so
        dragging a layer around doesn't change it on every move."""
        out = self.bounds
        m = max(self.width, self.height) // 2
        limit = Rect(-m, -m, self.width + m, self.height + m)
        for la in self.layers:
            if la.visible and la.opacity > 0:
                out = out.union(la.bounds.intersect(limit))
        if step > 1 and out != self.bounds:

            def up(v: int) -> int:  # an overhang rounded up to whole steps
                return -(-v // step) * step if v > 0 else 0

            out = Rect(
                -up(-out.x0),
                -up(-out.y0),
                self.width + up(out.x1 - self.width),
                self.height + up(out.y1 - self.height),
            ).intersect(limit)
        return out

    def composite(
        self, rect: Rect | None = None, *, override: dict | None = None, clip: bool = True
    ) -> Image.Image:
        """Flatten the visible layers (normal blending, layer opacity).

        ``override`` maps a layer index to a replacement image (used for live previews).
        ``clip=False`` lets ``rect`` reach past the canvas (the pasteboard).
        """
        if rect is None:
            rect = self.bounds
        elif clip:
            rect = rect.intersect(self.bounds)
        out = Image.new("RGBA", (max(rect.width, 0), max(rect.height, 0)))
        if rect.empty:
            return out
        for i, layer in enumerate(self.layers):
            if not layer.visible or layer.opacity <= 0:
                continue
            image = (override or {}).get(i, layer.image)
            lb = Rect(layer.x, layer.y, layer.x + image.width, layer.y + image.height)
            part = lb.intersect(rect)
            if part.empty:
                continue
            box = part.offset(-layer.x, -layer.y).box
            src = image.crop(box)
            if layer.mask is not None and layer.mask.size == image.size:
                src.putalpha(ImageChops.multiply(src.getchannel("A"), layer.mask.crop(box)))
            src = with_opacity(src, layer.opacity)
            out.alpha_composite(src, (part.x0 - rect.x0, part.y0 - rect.y0))
        return out
