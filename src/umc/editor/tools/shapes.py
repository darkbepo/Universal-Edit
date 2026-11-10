"""Line, rectangle and ellipse: previewed while dragging, drawn on release."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.tools.base import Tool, ToolContext, clip_rect, restrict

SUPERSAMPLE = 4


def supersample(area: int) -> int:
    """Anti-aliasing factor: 4× for normal shapes, less for huge ones (memory: area × s² × 4)."""
    if area <= 1_000_000:
        return SUPERSAMPLE
    return 2 if area <= 4_000_000 else 1


@dataclass
class LineOptions:
    width: int = opt(4, "Width", kind="slider", min=1, max=200, unit="px")
    opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%")


@dataclass
class ShapeOptions:
    width: int = opt(4, "Outline width", kind="slider", min=1, max=200, unit="px")
    mode: str = opt(
        "outline",
        "Style",
        choices=[("outline", "Outline"), ("filled", "Filled"), ("both", "Outline + fill")],
    )
    opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%")


def _with_opacity(img: Image.Image, opacity: int) -> Image.Image:
    if opacity >= 100:
        return img
    a = img.getchannel("A").point(lambda v: v * opacity // 100)
    img.putalpha(a)
    return img


class _ShapeTool(Tool):
    def __init__(self) -> None:
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self._overlay: tuple[Image.Image, int, int] | None = None

    # subclasses: the shape's bounding box (canvas, before stroke padding) and drawing
    def _points(self, ctx: ToolContext) -> tuple[float, float, float, float]:
        (x0, y0), (x1, y1) = self._start, self._end
        if ctx.shift:
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + math.copysign(side, (x1 - x0) or 1)
            y1 = y0 + math.copysign(side, (y1 - y0) or 1)
        return x0, y0, x1, y1

    def _draw(self, draw: ImageDraw.ImageDraw, pts, ctx: ToolContext, s: int) -> None:
        raise NotImplementedError

    def _pad(self, ctx: ToolContext) -> int:
        """How far the drawing reaches beyond its points (canvas px)."""
        return ctx.options.width + 2

    def _render(self, ctx: ToolContext) -> tuple[Image.Image, int, int] | None:
        pts = self._points(ctx)
        pad = self._pad(ctx)
        box = Rect.from_points(*pts)
        box = Rect(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad)
        bw, bh = self._canvas
        box = box.intersect(Rect(-pad, -pad, bw + pad, bh + pad))  # nothing off-canvas
        if box.empty:
            return None
        s = supersample(box.width * box.height)
        big = Image.new("RGBa", (box.width * s, box.height * s))
        local = [
            (pts[0] - box.x0) * s,
            (pts[1] - box.y0) * s,
            (pts[2] - box.x0) * s,
            (pts[3] - box.y0) * s,
        ]
        draw = ImageDraw.Draw(big)
        self._draw(draw, local, ctx, s)
        small = big.reduce(s).convert("RGBA")
        return _with_opacity(small, ctx.options.opacity), box.x0, box.y0

    @staticmethod
    def _premul(color) -> tuple[int, int, int, int]:
        a = color[3]
        return (color[0] * a // 255, color[1] * a // 255, color[2] * a // 255, a)

    # ------------------------------------------------------------ gesture
    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._canvas = (doc.width, doc.height)
        self._start = self._end = (x, y)
        self._overlay = None
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._start is None:
            return None
        old = self._overlay_rect()
        self._end = (x, y)
        self._overlay = self._render(ctx)
        new = self._overlay_rect()
        if new is None:
            return old
        return new.union(old) if old is not None else new

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._start is None:
            return None
        self._end = (x, y)
        moved = math.hypot(x - self._start[0], y - self._start[1]) >= 1
        overlay = self._render(ctx) if moved else None
        self._start = self._end = None
        self._overlay = None
        if overlay is None:
            return None
        img, ox, oy = overlay
        layer = doc.active_layer
        target = Rect(ox, oy, ox + img.width, oy + img.height).offset(-layer.x, -layer.y)
        region = target.intersect(clip_rect(doc, layer))
        if region.empty:
            return None
        before = layer.image.crop(region.box)
        part = layer.image.crop(region.box)
        src = img.crop(region.offset(-target.x0, -target.y0).box)
        part.alpha_composite(src)
        layer.image.paste(part, (region.x0, region.y0))
        restrict(doc, layer, region, before)  # shaped / soft selections
        return PatchCommand.from_region(self.label, doc, doc.active, region, before)

    def cancel(self, doc: Document) -> Rect | None:
        old = self._overlay_rect()
        self._start = self._end = None
        self._overlay = None
        return old

    def preview(self, doc: Document) -> tuple[Image.Image, int, int] | None:
        return self._overlay

    def _overlay_rect(self) -> Rect | None:
        if self._overlay is None:
            return None
        img, x, y = self._overlay
        return Rect(x, y, x + img.width, y + img.height)


class LineTool(_ShapeTool):
    id, label, shortcut = "line", "Line", "L"
    options_cls = LineOptions

    def _points(self, ctx: ToolContext):
        (x0, y0), (x1, y1) = self._start, self._end
        if ctx.shift:
            length = math.hypot(x1 - x0, y1 - y0)
            angle = round(math.atan2(y1 - y0, x1 - x0) / (math.pi / 4)) * (math.pi / 4)
            x1, y1 = x0 + length * math.cos(angle), y0 + length * math.sin(angle)
        return x0, y0, x1, y1

    def _draw(self, draw, pts, ctx, s):
        w = max(1, ctx.options.width * s)
        color = self._premul(ctx.color)
        draw.line(pts, fill=color, width=w)
        r = w / 2  # round caps
        for cx, cy in (pts[:2], pts[2:]):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


class RectTool(_ShapeTool):
    id, label, shortcut = "rect", "Rectangle", "U"
    options_cls = ShapeOptions
    method = "rectangle"

    def _draw(self, draw, pts, ctx, s):
        x0, y0, x1, y1 = pts
        xy = [min(x0, x1), min(y0, y1), max(x0, x1) - 1, max(y0, y1) - 1]
        if xy[2] < xy[0] or xy[3] < xy[1]:
            return
        mode = ctx.options.mode
        fill = self._premul(ctx.secondary) if mode in ("filled", "both") else None
        outline = self._premul(ctx.primary) if mode in ("outline", "both") else None
        width = max(1, ctx.options.width * s) if outline else 0
        getattr(draw, self.method)(xy, fill=fill, outline=outline, width=width)


class EllipseTool(RectTool):
    id, label, shortcut = "ellipse", "Ellipse", "U"
    method = "ellipse"


@dataclass
class ArrowOptions:
    width: int = opt(6, "Width", kind="slider", min=1, max=200, unit="px")
    heads: str = opt("end", "Arrowheads", choices=[("end", "At the end"), ("both", "Both ends")])
    opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%")


def arrow_head(x0: float, y0: float, x1: float, y1: float, width: float):
    """(tip polygon, where the line stops) for an arrowhead at (x1, y1) pointing away from
    (x0, y0). Shared with web/src/editor/tools/shapes.ts."""
    length = max(4 * width, 14)
    half = max(2.2 * width, 7)
    d = math.hypot(x1 - x0, y1 - y0) or 1
    ux, uy = (x1 - x0) / d, (y1 - y0) / d
    length = min(length, d * 0.6)
    bx, by = x1 - ux * length, y1 - uy * length
    return [(x1, y1), (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half)], (bx, by)


class ArrowTool(LineTool):
    """A line with an arrowhead at the end (or both ends) — for pointing things out."""

    id, label, shortcut = "arrow", "Arrow", "L"
    options_cls = ArrowOptions

    def _pad(self, ctx: ToolContext) -> int:
        return math.ceil(max(2.2 * ctx.options.width, 7)) + ctx.options.width + 2

    def _draw(self, draw, pts, ctx, s):
        w = max(1, ctx.options.width * s)
        color = self._premul(ctx.color)
        x0, y0, x1, y1 = pts
        tip, (ex, ey) = arrow_head(x0, y0, x1, y1, w)
        draw.polygon(tip, fill=color)
        sx, sy = x0, y0
        if ctx.options.heads == "both":
            tip0, (sx, sy) = arrow_head(x1, y1, x0, y0, w)
            draw.polygon(tip0, fill=color)
        draw.line([sx, sy, ex, ey], fill=color, width=w)
        if ctx.options.heads != "both":  # round tail
            r = w / 2
            draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=color)
