"""Copy, cut and paste (no Qt): what goes on the clipboard and how it comes back as a layer.

A copy remembers where it came from on the canvas, so pasting it back inside the editor puts it
in the same place. An image from another program has no position and is centred where the user
is looking.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from umc.editor import ops
from umc.editor.document import Document, check_size
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.layer import Layer
from umc.editor.tools.base import restrict


@dataclass
class Clip:
    image: Image.Image  # RGBA
    x: int | None = None  # canvas position it was copied from (None = from another program)
    y: int | None = None


def copy(doc: Document, merged: bool = False) -> Clip | None:
    """The selected part of the active layer (or the whole layer without a selection).

    ``merged``: what you see — all visible layers flattened — inside the selection or canvas.
    """
    if merged:
        region = doc.bounds if doc.selection is None else doc.selection.intersect(doc.bounds)
        if region.empty:
            return None
        return Clip(_shaped(doc, doc.composite(region), region), region.x0, region.y0)
    layer = doc.active_layer
    region = layer.bounds if doc.selection is None else layer.bounds.intersect(doc.selection)
    if region.empty:
        return None
    image = layer.pixels(region.offset(-layer.x, -layer.y).box)  # with its layer mask applied
    return Clip(_shaped(doc, image, region), region.x0, region.y0)


def _shaped(doc: Document, image: Image.Image, region: Rect) -> Image.Image:
    """``image`` (of canvas ``region``) with the selection's shape taken into its alpha."""
    from umc.editor.selection import weight

    w = weight(doc, region)
    if w is None:
        return image
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32)
    arr[..., 3] *= w
    return Image.fromarray(np.floor(arr + 0.5).astype(np.uint8), "RGBA")


def cut(doc: Document) -> tuple[Clip | None, Command | None]:
    """Copy, then make that part of the active layer transparent (one undo step)."""
    clip = copy(doc)
    if clip is None:
        return None, None
    layer = doc.active_layer
    region = Rect(clip.x, clip.y, clip.x + clip.image.width, clip.y + clip.image.height)
    region = region.offset(-layer.x, -layer.y)
    before = layer.image.crop(region.box)
    layer.image.paste(Image.new("RGBA", (region.width, region.height)), (region.x0, region.y0))
    restrict(doc, layer, region, before)  # shaped / soft selections clear only that much
    return clip, PatchCommand.from_region("Cut", doc, doc.active, region, before)


def paste(doc: Document, clip: Clip, center: tuple[float, float] | None = None) -> Command:
    """``clip`` as a new layer above the active one, selected so it can be moved straight away.

    It goes back where it was copied from; a clip without a position is centred on ``center``
    (canvas coordinates; default: the middle of the canvas).
    """
    image = clip.image if clip.image.mode == "RGBA" else clip.image.convert("RGBA")
    check_size(image.width, image.height)
    if clip.x is not None and clip.y is not None:
        x, y = clip.x, clip.y
    else:
        cx, cy = center if center is not None else (doc.width / 2, doc.height / 2)
        x, y = round(cx - image.width / 2), round(cy - image.height / 2)
    layer = Layer(ops.unique_name(doc, "Pasted"), image, x=x, y=y)
    return ops.add_layers(doc, [layer], "Paste")
