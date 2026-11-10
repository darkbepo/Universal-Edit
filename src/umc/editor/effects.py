"""Anything that changes the active layer's pixels inside the selection with a live preview:
adjustments, filters and looks.

An effect is a callable ``effect(image, scale=1.0) -> image`` with an ``identity`` flag
(nothing would change). ``scale`` < 1 means the image was shrunk for a quick preview, so
effects measured in pixels (blur radius…) shrink with it.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Protocol

from PIL import Image

from umc.editor.document import Document
from umc.editor.history import Command, PatchCommand
from umc.editor.tools.base import clip_rect

PREVIEW_PIXELS = 2_000_000  # live previews of bigger areas are worked out on a smaller copy


class Effect(Protocol):
    @property
    def identity(self) -> bool: ...

    def __call__(self, image: Image.Image, scale: float = 1.0) -> Image.Image: ...


def margin(effect: Effect) -> int:
    """How many pixels around the area an effect looks at (blur radius…; 0 for colour changes).

    Effects that need neighbours define ``margin()``; they get that much extra of the layer
    around the selection, so a blur at the selection's edge mixes with what lies beyond it.
    """
    fn = getattr(effect, "margin", None)
    return int(fn()) if fn is not None else 0


@contextmanager
def _scope(doc: Document, effect: Effect):
    """Effects with ``outside_selection`` (background blur) work on the inverted, softened
    selection; the real selection is put back afterwards."""
    soften = getattr(effect, "outside_selection", None)
    if soften is None:
        yield True
        return
    if doc.selection is None:  # nothing selected = no subject: nothing to blur around
        yield False
        return
    from umc.editor import selection as sel

    saved = (doc.selection, doc.selection_mask)
    try:
        sel.invert(doc)
        if soften > 0:
            sel.feather(doc, soften)
        yield True
    finally:
        doc.selection, doc.selection_mask = saved


def region_image(doc: Document) -> Image.Image | None:
    """The part of the active layer an effect changes (inside the selection), or None."""
    layer = doc.active_layer
    region = clip_rect(doc, layer)
    return None if region.empty else layer.image.crop(region.box)


def _widened(layer_image: Image.Image, region, m: int):
    from umc.editor.geometry import Rect

    full = Rect(0, 0, layer_image.width, layer_image.height)
    return Rect(region.x0 - m, region.y0 - m, region.x1 + m, region.y1 + m).intersect(full)


def _run(layer_image: Image.Image, region, effect: Effect, max_pixels: int | None = None):
    """``effect`` on ``region`` of the layer, using up to ``margin`` extra pixels around it.

    With ``max_pixels`` (previews) a bigger area is worked out on a reduced copy and scaled back.
    """
    m = margin(effect)
    wide = _widened(layer_image, region, m) if m > 0 else region
    part = layer_image.crop(wide.box)
    if max_pixels is not None and region.width * region.height > max_pixels:
        factor = math.ceil(math.sqrt(region.width * region.height / max_pixels))
        small = part.reduce(factor)  # box average by a whole number: fast and clean
        done = effect(small, 1 / factor).resize(part.size, Image.Resampling.BILINEAR)
        if m == 0:  # colour changes never touch alpha: keep the layer's edges sharp
            done.putalpha(part.getchannel("A"))
    else:
        done = effect(part, 1.0)
    return done if wide is region else done.crop(region.offset(-wide.x0, -wide.y0).box)


def preview(doc: Document, effect: Effect, max_pixels: int = PREVIEW_PIXELS) -> Image.Image:
    """The active layer as it would look with ``effect`` (for the live preview).

    An area over ``max_pixels`` is worked out on a reduced copy and scaled back up, so dragging
    a slider stays smooth on a 24 MP photo (exact at the usual fit-to-window zoom; OK applies
    the full-resolution result).
    """
    layer = doc.active_layer
    img = layer.image.copy()
    with _scope(doc, effect) as ok:
        region = clip_rect(doc, layer)
        if not ok or region.empty or effect.identity:
            return img
        img.paste(_run(layer.image, region, effect, max_pixels), (region.x0, region.y0))
        _masked(doc, img, layer, region)
    return img


def _masked(doc: Document, img: Image.Image, layer, region) -> None:
    """Mix the changed ``region`` of ``img`` with the layer's original by the selection."""
    from umc.editor.layer import Layer
    from umc.editor.tools.base import restrict

    if doc.selection_mask is None:
        return
    restrict(doc, Layer(layer.name, img, layer.x, layer.y), region, layer.image.crop(region.box))


def apply_to_document(doc: Document, effect: Effect, label: str) -> Command | None:
    """Apply ``effect`` to the active layer inside the selection — one undo step."""
    if effect.identity:
        return None
    with _scope(doc, effect) as ok:
        return _apply(doc, effect, label) if ok else None


def _apply(doc: Document, effect: Effect, label: str) -> Command | None:
    layer = doc.active_layer
    region = clip_rect(doc, layer)
    if region.empty:
        return None
    before = layer.image.crop(region.box)
    layer.image.paste(_run(layer.image, region, effect), (region.x0, region.y0))
    from umc.editor.tools.base import restrict

    restrict(doc, layer, region, before)  # shaped / soft selections
    return PatchCommand.from_region(label, doc, doc.active, region, before)
