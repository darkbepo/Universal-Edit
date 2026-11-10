"""Brush and eraser: round dabs along the mouse path."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.tools.base import Tool, ToolContext, blend_over, clip_rect, paste_array


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


class _StrokeTool(Tool):
    erase = False
    cursor = "brush"

    def __init__(self) -> None:
        self._layer_index: int | None = None

    # ------------------------------------------------------------ gesture
    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        layer = doc.active_layer
        self._layer_index = doc.active
        self._before = layer.image.copy()
        self._base = np.asarray(self._before, dtype=np.uint8)
        self._cov = np.zeros(self._base.shape[:2], dtype=np.float32)
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
        self._last = (
            lx + (x - lx) * steps * spacing / dist,
            ly + (y - ly) * steps * spacing / dist,
        )
        return changed

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._layer_index is None:
            return None
        self.move(doc, x, y, ctx)
        idx, self._layer_index = self._layer_index, None
        dirty = self._dirty
        self._base = self._cov = None
        if dirty is None:
            return None
        return PatchCommand.capture(self.label, doc, idx, dirty, self._before)

    def cancel(self, doc: Document) -> Rect | None:
        if self._layer_index is None or self._dirty is None:
            self._layer_index = None
            return None
        layer = doc.layers[self._layer_index]
        layer.image.paste(self._before.crop(self._dirty.box), (self._dirty.x0, self._dirty.y0))
        self._layer_index = None
        return self._dirty.offset(layer.x, layer.y)

    # ------------------------------------------------------------ pixels
    def _dab(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        layer = doc.layers[self._layer_index]
        cx, cy = x - layer.x, y - layer.y
        radius = max(0.5, ctx.options.size / 2)
        region = Rect.around(cx, cy, radius).intersect(self._clip)
        if region.empty:
            return None
        ys, xs = np.mgrid[region.y0 : region.y1, region.x0 : region.x1].astype(np.float32)
        d = np.hypot(xs + 0.5 - cx, ys + 0.5 - cy)
        soft = max(1.0, radius * (1 - ctx.options.hardness / 100))
        mask = np.clip((radius - d) / soft, 0.0, 1.0) * (ctx.options.opacity / 100)
        sl = (slice(region.y0, region.y1), slice(region.x0, region.x1))
        np.maximum(self._cov[sl], mask, out=self._cov[sl])
        base = self._base[sl]
        cov = self._cov[sl]
        if self.erase:
            out = base.copy()
            out[..., 3] = np.clip(base[..., 3] * (1.0 - cov) + 0.5, 0, 255).astype(np.uint8)
        else:
            out = blend_over(base, self._color, cov)
        paste_array(layer.image, out, region)
        self._dirty = region if self._dirty is None else self._dirty.union(region)
        return region.offset(layer.x, layer.y)


class BrushTool(_StrokeTool):
    id, label, shortcut = "brush", "Brush", "B"
    options_cls = BrushOptions


class EraserTool(_StrokeTool):
    id, label, shortcut = "eraser", "Eraser", "E"
    options_cls = EraserOptions
    erase = True
