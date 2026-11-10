"""Gradient tool: drag from where the gradient starts to where it ends.

It paints colour (front → back, or front → see-through) on the layer, shapes the layer's mask
(shows at the start, hides at the end), or makes a soft selection that fades out — so an
adjustment afterwards only brightens the sky, say. Shift keeps the direction to 45° steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.tools.base import Tool, ToolContext, clip_rect, restrict

SHAPES = {"linear": "Straight", "radial": "Round"}
COLOURS = {"front_back": "Front → back colour", "front_clear": "Front colour → see-through"}
TARGETS = {"colour": "Colour on the layer", "mask": "Layer mask", "selection": "Soft selection"}


@dataclass
class GradientOptions:
    shape: str = opt("linear", "Shape", choices=list(SHAPES.items()))
    target: str = opt("colour", "Paints", choices=list(TARGETS.items()))
    colours: str = opt(
        "front_back",
        "Colours",
        choices=list(COLOURS.items()),
        show_if=lambda v: v.get("target") == "colour",
    )


def gradient_t(
    rect: Rect, start: tuple[float, float], end: tuple[float, float], shape: str
) -> np.ndarray:
    """0 at ``start`` … 1 at ``end`` for every pixel centre of ``rect`` (canvas coordinates)."""
    ys, xs = np.mgrid[rect.y0 : rect.y1, rect.x0 : rect.x1].astype(np.float32)
    xs += 0.5
    ys += 0.5
    sx, sy = start
    dx, dy = end[0] - sx, end[1] - sy
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        return np.zeros(xs.shape, dtype=np.float32)
    if shape == "radial":
        t = np.hypot(xs - sx, ys - sy) / math.sqrt(length2)
    else:
        t = ((xs - sx) * dx + (ys - sy) * dy) / length2
    return np.clip(t, 0.0, 1.0)


class GradientTool(Tool):
    id, label, shortcut = "gradient", "Gradient", "G"
    options_cls = GradientOptions

    def __init__(self) -> None:
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self.outline = None

    def _point(self, x: float, y: float, ctx: ToolContext) -> tuple[float, float]:
        if not ctx.shift or self._start is None:
            return (x, y)
        sx, sy = self._start
        ang = round(math.atan2(y - sy, x - sx) / (math.pi / 4)) * (math.pi / 4)
        r = math.hypot(x - sx, y - sy)
        return (sx + r * math.cos(ang), sy + r * math.sin(ang))

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._start = self._end = (x, y)
        self.outline = ("poly", [self._start, self._end])
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._start is not None:
            self._end = self._point(x, y, ctx)
            self.outline = ("poly", [self._start, self._end])
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._start is None:
            return None
        start, end = self._start, self._point(x, y, ctx)
        self._start = self._end = None
        self.outline = None
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 1:
            return None
        o = ctx.options
        if o.target == "selection":
            from umc.editor.selection import combine

            t = gradient_t(doc.bounds, start, end, o.shape)
            shape = Image.fromarray(np.floor((1 - t) * 255 + 0.5).astype(np.uint8), "L")
            return combine(doc, shape, "replace", "Gradient selection")
        layer = doc.active_layer
        if o.target == "mask":
            from umc.editor import ops

            t = gradient_t(layer.bounds, start, end, o.shape)
            mask = Image.fromarray(np.floor((1 - t) * 255 + 0.5).astype(np.uint8), "L")
            return ops._replace(doc, doc.active, "Gradient mask", replace(layer, mask=mask))
        region = clip_rect(doc, layer)
        if region.empty:
            return None
        t = gradient_t(region.offset(layer.x, layer.y), start, end, o.shape)[..., None]
        c0 = np.array(ctx.primary, dtype=np.float32)
        c1 = np.array(ctx.secondary, dtype=np.float32)
        if o.colours == "front_clear":
            c1 = np.array([*ctx.primary[:3], 0], dtype=np.float32)
        a0, a1 = c0[3] / 255, c1[3] / 255
        alpha = a0 + (a1 - a0) * t
        rgb = (c0[:3] * a0 + (c1[:3] * a1 - c0[:3] * a0) * t) / np.where(alpha > 0, alpha, 1)
        over = np.concatenate([rgb, alpha * 255], axis=-1)
        overlay = Image.fromarray(np.clip(np.floor(over + 0.5), 0, 255).astype(np.uint8), "RGBA")
        before = layer.image.crop(region.box)
        part = before.copy()
        part.alpha_composite(overlay)
        layer.image.paste(part, (region.x0, region.y0))
        restrict(doc, layer, region, before)
        return PatchCommand.from_region(self.label, doc, doc.active, region, before)

    def cancel(self, doc: Document) -> Rect | None:
        self._start = self._end = None
        self.outline = None
        return None
