"""Fill bucket (scanline flood fill on numpy arrays) and colour picker."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.tools.base import (
    RGBA,
    Tool,
    ToolContext,
    blend_over,
    clip_rect,
    paste_array,
    restrict,
)


@dataclass
class FillOptions:
    tolerance: int = opt(32, "Tolerance", kind="slider", min=0, max=255)
    sample_all: bool = opt(False, "Sample all layers")


@dataclass
class PickerOptions:
    sample_all: bool = opt(True, "Sample all layers")


def similar(src: np.ndarray, seed: np.ndarray, tolerance: int) -> np.ndarray:
    """Pixels of ``src`` (uint8 HxWx4) whose channels all differ from ``seed`` by ≤ tolerance."""
    if tolerance <= 0:
        flat = np.ascontiguousarray(src).view(np.uint32)[..., 0]
        return flat == np.ascontiguousarray(seed).view(np.uint32)[0]
    out = np.ones(src.shape[:2], dtype=bool)
    for c in range(4):
        ch, v = src[..., c], seed[c]
        out &= (np.maximum(ch, v) - np.minimum(ch, v)) <= tolerance
    return out


def paint_mask(base: np.ndarray, color, mask: np.ndarray) -> np.ndarray:
    """``base`` with ``color`` painted (source-over) where ``mask`` is True."""
    out = base.copy()
    if color[3] == 255:
        out[mask] = color
    elif mask.any():
        px = out[mask][None, ...]
        out[mask] = blend_over(px, color, np.ones(px.shape[:2], dtype=np.float32))[0]
    return out


def flood_mask(candidates: np.ndarray, sx: int, sy: int) -> np.ndarray:
    """Pixels 4-connected to (sx, sy) inside the boolean ``candidates`` array."""
    h, w = candidates.shape
    filled = np.zeros_like(candidates)
    if not candidates[sy, sx]:
        return filled
    stack = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if filled[y, x] or not candidates[y, x]:
            continue
        row = candidates[y] & ~filled[y]
        left = np.flatnonzero(~row[:x])
        x0 = int(left[-1]) + 1 if left.size else 0
        right = np.flatnonzero(~row[x:])
        x1 = x + int(right[0]) if right.size else w
        filled[y, x0:x1] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                seg = candidates[ny, x0:x1] & ~filled[ny, x0:x1]
                if not seg.any():
                    continue
                edges = np.diff(np.concatenate(([0], seg.view(np.int8), [0])))
                for s in np.flatnonzero(edges == 1):
                    stack.append((ny, x0 + int(s)))
    return filled


class FillTool(Tool):
    id, label, shortcut = "fill", "Fill", "G"
    options_cls = FillOptions
    cursor = "cross"

    def __init__(self) -> None:
        self._pending: tuple[int, Rect, object] | None = None

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._pending = None
        layer = doc.active_layer
        clip = clip_rect(doc, layer)
        lx, ly = int(x) - layer.x, int(y) - layer.y
        if clip.empty or not clip.contains(lx, ly):
            return None
        if ctx.options.sample_all:
            src = np.asarray(doc.composite(clip.offset(layer.x, layer.y)), dtype=np.uint8)
        else:
            whole = clip == Rect(0, 0, *layer.image.size)
            src = np.asarray(layer.image if whole else layer.image.crop(clip.box), dtype=np.uint8)
        sx, sy = lx - clip.x0, ly - clip.y0
        mask = flood_mask(similar(src, src[sy, sx], ctx.options.tolerance), sx, sy)
        rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        if rows.size == 0:
            return None
        box = Rect(int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
        region = box.offset(clip.x0, clip.y0)
        before = layer.image.crop(region.box)
        base = np.asarray(before, dtype=np.uint8)
        sub = mask[box.y0 : box.y1, box.x0 : box.x1]
        paste_array(layer.image, paint_mask(base, ctx.color, sub), region)
        restrict(doc, layer, region, before)  # shaped / soft selections
        self._pending = (doc.active, region, before)
        return region.offset(layer.x, layer.y)

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._pending is None:
            return None
        idx, region, before = self._pending
        self._pending = None
        return PatchCommand.from_region(self.label, doc, idx, region, before)

    def cancel(self, doc: Document) -> Rect | None:
        """The fill is applied on press; an interrupted gesture must put the pixels back."""
        if self._pending is None:
            return None
        idx, region, before = self._pending
        self._pending = None
        layer = doc.layers[idx]
        layer.image.paste(before, (region.x0, region.y0))
        return region.offset(layer.x, layer.y)


class PickerTool(Tool):
    id, label, shortcut = "picker", "Colour picker", "I"
    options_cls = PickerOptions
    cursor = "pick"

    def __init__(self) -> None:
        self.picked: RGBA | None = None
        self.picked_button = "left"

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self.picked = self._sample(doc, int(x), int(y), ctx.options.sample_all)
        self.picked_button = ctx.button
        return None

    move = press

    @staticmethod
    def _sample(doc: Document, x: int, y: int, sample_all: bool) -> RGBA | None:
        if not doc.bounds.contains(x, y):
            return None
        if sample_all:
            return tuple(doc.composite(Rect(x, y, x + 1, y + 1)).getpixel((0, 0)))
        layer = doc.active_layer
        if not layer.bounds.contains(x, y):
            return None
        return tuple(layer.image.getpixel((x - layer.x, y - layer.y)))
