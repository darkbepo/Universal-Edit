"""Brush and eraser: round dabs along the mouse path."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.layer import Layer
from umc.editor.tools.base import (
    Tool,
    ToolContext,
    blend_over,
    clip_rect,
    paste_array,
    selection_weight,
)


@dataclass
class BrushOptions:
    size: int = opt(12, "Size", kind="slider", min=1, max=500, unit="px")
    hardness: int = opt(80, "Hardness", kind="slider", min=0, max=100, unit="%")
    opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%")


@dataclass
class EraserOptions:
    size: int = opt(24, "Size", kind="slider", min=1, max=500, unit="px")
    hardness: int = opt(80, "Hardness", kind="slider", min=0, max=100, unit="%")
    opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%")


TILE = 128


class _StrokeTool(Tool):
    """Round dabs along the path. The original pixels are remembered per 128 px tile, only
    where the stroke goes, so a stroke never copies the whole layer."""

    erase = False
    cursor = "brush"

    def __init__(self) -> None:
        self._layer_index: int | None = None

    # ------------------------------------------------------------ gesture
    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._layer_index = doc.active
        layer = self._target(doc)
        self._tiles: dict[tuple[int, int], tuple[Rect, np.ndarray, np.ndarray]] = {}
        self._clip = clip_rect(doc, layer)
        self._dirty: Rect | None = None
        self._last = (x, y)
        self._color = ctx.color
        return self._dab(doc, x, y, ctx)

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._layer_index is None:
            return None
        lx, ly = self._last
        dist = math.hypot(x - lx, y - ly)
        spacing = max(1.0, ctx.options.size / 4)
        if dist < spacing:
            return None
        steps = int(dist // spacing)
        changed = None
        for i in range(1, steps + 1):
            t = i * spacing / dist
            r = self._dab(doc, lx + (x - lx) * t, ly + (y - ly) * t, ctx)
            changed = r if changed is None else changed.union(r)
        t = steps * spacing / dist
        self._last = (lx + (x - lx) * t, ly + (y - ly) * t)
        return changed

    def _original(self, layer, rect: Rect):
        """The pixels of ``rect`` as they were before the stroke."""
        img = layer.image.crop(rect.box)
        for trect, base, _cov in self._tiles.values():
            part = trect.intersect(rect)
            if part.empty:
                continue
            sub = base[
                part.y0 - trect.y0 : part.y1 - trect.y0, part.x0 - trect.x0 : part.x1 - trect.x0
            ]
            paste_array(img, np.ascontiguousarray(sub), part.offset(-rect.x0, -rect.y0))
        return img

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._layer_index is None:
            return None
        self.move(doc, x, y, ctx)
        idx, self._layer_index = self._layer_index, None
        dirty = self._dirty
        if dirty is None:
            self._tiles = {}
            return None
        before = self._original(self._target(doc, idx), dirty)
        self._tiles = {}
        return PatchCommand.from_region(self.label, doc, idx, dirty, before)

    def cancel(self, doc: Document) -> Rect | None:
        if self._layer_index is None or self._dirty is None:
            self._layer_index = None
            return None
        layer = self._target(doc)
        layer.image.paste(self._original(layer, self._dirty), (self._dirty.x0, self._dirty.y0))
        self._layer_index = None
        self._tiles = {}
        return self._dirty.offset(layer.x, layer.y)

    # ------------------------------------------------------------ pixels
    def _tile(self, layer, tx: int, ty: int):
        key = (tx, ty)
        if key not in self._tiles:
            w, h = layer.image.size
            trect = Rect(tx * TILE, ty * TILE, min(w, (tx + 1) * TILE), min(h, (ty + 1) * TILE))
            base = np.array(layer.image.crop(trect.box), dtype=np.uint8)
            self._tiles[key] = (trect, base, np.zeros(base.shape[:2], dtype=np.float32))
        return self._tiles[key]

    def _target(self, doc: Document, index: int | None = None):
        """The layer the stroke paints on (the mask brush paints a stand-in for the mask)."""
        return doc.layers[self._layer_index if index is None else index]

    def _dab(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        layer = self._target(doc)
        cx, cy = x - layer.x, y - layer.y
        radius = max(0.5, ctx.options.size / 2)
        region = Rect.around(cx, cy, radius).intersect(self._clip)
        if region.empty:
            return None
        soft = max(1.0, radius * (1 - ctx.options.hardness / 100))
        strength = ctx.options.opacity / 100
        for ty in range(region.y0 // TILE, (region.y1 - 1) // TILE + 1):
            for tx in range(region.x0 // TILE, (region.x1 - 1) // TILE + 1):
                trect, base_t, cov_t = self._tile(layer, tx, ty)
                part = trect.intersect(region)
                if part.empty:
                    continue
                ys, xs = np.mgrid[part.y0 : part.y1, part.x0 : part.x1].astype(np.float32)
                d = np.hypot(xs + 0.5 - cx, ys + 0.5 - cy)
                mask = np.clip((radius - d) / soft, 0.0, 1.0) * strength
                sel = selection_weight(doc, layer, part)
                if sel is not None:
                    mask *= sel  # soft / shaped selections paint partly
                sl = (
                    slice(part.y0 - trect.y0, part.y1 - trect.y0),
                    slice(part.x0 - trect.x0, part.x1 - trect.x0),
                )
                np.maximum(cov_t[sl], mask, out=cov_t[sl])
                base, cov = base_t[sl], cov_t[sl]
                if self.erase:
                    out = base.copy()
                    out[..., 3] = np.clip(base[..., 3] * (1.0 - cov) + 0.5, 0, 255).astype(np.uint8)
                else:
                    out = blend_over(base, self._color, cov)
                paste_array(layer.image, np.ascontiguousarray(out), part)
        self._dirty = region if self._dirty is None else self._dirty.union(region)
        return region.offset(layer.x, layer.y)


class BrushTool(_StrokeTool):
    id, label, shortcut = "brush", "Brush", "B"
    options_cls = BrushOptions


class EraserTool(_StrokeTool):
    id, label, shortcut = "eraser", "Eraser", "E"
    options_cls = EraserOptions
    erase = True


@dataclass
class HighlighterOptions:
    size: int = opt(24, "Size", kind="slider", min=2, max=300, unit="px")
    hardness: int = opt(90, "Hardness", kind="slider", min=0, max=100, unit="%")
    opacity: int = opt(40, "Strength", kind="slider", min=5, max=100, unit="%")


class HighlighterTool(_StrokeTool):
    """A broad see-through marker: crossing your own stroke doesn't darken it."""

    id, label, shortcut = "highlighter", "Highlighter", "H"
    options_cls = HighlighterOptions


class MaskBrushTool(_StrokeTool):
    """Paints the active layer's mask: left button hides (black), right button shows (white).

    It paints an RGBA stand-in of the mask with the normal brush and copies its grey back into
    the mask after every dab; undo is a ``MaskPatchCommand``.
    """

    id, label, shortcut = "mask_brush", "Mask brush", "K"
    options_cls = BrushOptions

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        layer = doc.active_layer
        if layer.mask is None:
            self._layer_index = None
            return None
        grey = layer.mask
        self._proxy = Layer(
            layer.name,
            Image.merge("RGBA", (grey, grey, grey, Image.new("L", grey.size, 255))),
            layer.x,
            layer.y,
        )
        self._mask_before = grey.copy()
        black = (0, 0, 0, 255) if ctx.button == "left" else (255, 255, 255, 255)
        ctx = ToolContext(black, black, ctx.options, ctx.shift, "left", ctx.font_family)
        return super().press(doc, x, y, ctx)

    def _target(self, doc: Document, index: int | None = None):
        return self._proxy

    def _dab(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        r = super()._dab(doc, x, y, ctx)
        if r is not None:
            layer = doc.layers[self._layer_index]
            part = r.offset(-layer.x, -layer.y)
            layer.mask.paste(self._proxy.image.getchannel("R").crop(part.box), (part.x0, part.y0))
        return r

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._layer_index is None:
            return None
        idx = self._layer_index
        self.move(doc, x, y, ctx)
        self._layer_index = None
        dirty, self._tiles = self._dirty, {}
        if dirty is None:
            return None
        from umc.editor.history import MaskPatchCommand

        mask = doc.layers[idx].mask
        return MaskPatchCommand(
            self.label, idx, dirty, self._mask_before.crop(dirty.box), mask.crop(dirty.box)
        )

    def cancel(self, doc: Document) -> Rect | None:
        if self._layer_index is None or self._dirty is None:
            self._layer_index = None
            return None
        layer = doc.layers[self._layer_index]
        layer.mask.paste(self._mask_before.crop(self._dirty.box), (self._dirty.x0, self._dirty.y0))
        self._layer_index, self._tiles = None, {}
        return self._dirty.offset(layer.x, layer.y)
