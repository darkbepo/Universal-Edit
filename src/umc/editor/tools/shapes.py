"""Line, arrow, rectangle and ellipse: previewed while dragging; each becomes its own layer
that remembers its geometry and style (``ShapeSpec`` on ``Layer.shape``), so its corners / ends
can be dragged again until the layer is rasterised, merged or transformed.
``web/src/editor/tools/shapes.ts`` is the website's port.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from PIL import Image, ImageDraw

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command
from umc.editor.layer import Layer
from umc.editor.tools.base import Tool, ToolContext

HANDLE = 8  # screen pixels: how close to a corner / end counts as grabbing it

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
        bw, bh = self._canvas
        return self.draw_points(self._points(ctx), ctx, (bw, bh))

    def draw_points(
        self,
        pts,
        ctx: ToolContext,
        canvas: tuple[int, int] | None = None,
        limit: Rect | None = None,
    ) -> tuple[Image.Image, int, int] | None:
        """The shape through ``pts`` (canvas coordinates): (image, x, y). With ``canvas`` the
        preview is limited to it (plus the stroke), with ``limit`` to that area (a reshape's
        live preview: the pasteboard); a layer keeps the whole shape."""
        pad = self._pad(ctx)
        box = Rect.from_points(*pts)
        box = Rect(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad)
        if canvas is not None:
            bw, bh = canvas
            box = box.intersect(Rect(-pad, -pad, bw + pad, bh + pad))  # nothing off-canvas
        if limit is not None:
            box = box.intersect(limit)
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
        self._overlay = None
        self._grab = None
        hit = self._handle_at(doc, x, y, ctx.zoom)
        if hit is not None:
            layer = doc.active_layer
            if shape_painted_over(layer):
                self.notice = "This shape was painted on, so it's pixels now (its handles are off)."
            else:
                hx, hy = handle_points(layer)[hit]
                # reshape it instead of drawing a new one; the handle keeps its offset to the
                # pointer, so a click without a drag changes nothing
                self._grab = (hit, layer, doc.active, (hx - x, hy - y))
                self._grab_at = (x, y)
                return None
        self._start = self._end = (x, y)
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._grab is not None:
            return self._reshape(doc, x, y, ctx)
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
        if self._grab is not None:
            hit, old, index, (ox, oy) = self._grab
            self._grab = None
            doc.layers = list(doc.layers)
            doc.layers[index] = old  # the live preview goes; the undoable change comes
            pts = self._reshaped(old, hit, x + ox, y + oy)
            if (x, y) == self._grab_at or pts == canvas_points(old):
                return None  # a click on the handle: nothing changes
            new = shape_layer(old.name, replace(old.shape, points=pts), old)
            if new is None:
                return None
            from umc.editor import ops

            return ops._replace(doc, index, "Edit shape", new)
        if self._start is None:
            return None
        self._end = (x, y)
        moved = math.hypot(x - self._start[0], y - self._start[1]) >= 1
        pts = self._points(ctx)
        self._start = self._end = None
        self._overlay = None
        if not moved:
            return None
        spec = spec_from(self.id, pts, ctx)
        from umc.editor import ops

        layer = shape_layer(ops.unique_name(doc, self.label), spec)
        if layer is None or layer.image.getbbox() is None:
            return None  # e.g. a rectangle dragged flat: nothing to show
        return ops.add_layers(doc, [layer], self.label)

    def cancel(self, doc: Document) -> Rect | None:
        old = self._overlay_rect()
        if self._grab is not None:
            layer, index = self._grab[1], self._grab[2]
            self._grab = None
            dirty = doc.layers[index].bounds.union(layer.bounds)
            doc.layers = list(doc.layers)
            doc.layers[index] = layer
            return dirty
        self._start = self._end = None
        self._overlay = None
        return old

    # ------------------------------------------------------------ handles
    def handles(self, doc: Document) -> list[tuple[float, float]]:
        """The corners / ends of the active layer's shape (canvas coordinates), drawn by the
        canvas while this tool is chosen; dragging one reshapes the layer."""
        layer = doc.active_layer if doc.layers else None
        if layer is None or layer.shape is None or not layer.visible:
            return []
        return handle_points(layer)

    def _handle_at(self, doc: Document, x: float, y: float, zoom: float) -> int | None:
        r = HANDLE / max(zoom, 1e-6)
        for i, (hx, hy) in enumerate(self.handles(doc)):
            if abs(hx - x) <= r and abs(hy - y) <= r:
                return i
        return None

    @staticmethod
    def _reshaped(old: Layer, hit: int, x: float, y: float) -> tuple:
        """``old``'s canvas points with handle ``hit`` dragged to (x, y)."""
        pts = list(canvas_points(old))
        if old.shape.kind in ("line", "arrow"):
            pts[2 * hit : 2 * hit + 2] = [x, y]
        else:  # corners 0 1 2 3 = top-left, top-right, bottom-right, bottom-left of (x0 y0 x1 y1)
            xi = 0 if hit in (0, 3) else 2
            yi = 1 if hit in (0, 1) else 3
            pts[xi], pts[yi] = x, y
        return tuple(pts)

    def _reshape(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        hit, old, index, (ox, oy) = self._grab
        pts = self._reshaped(old, hit, x + ox, y + oy)
        # the live preview only draws what the view can show (a handle dragged far away on a
        # big picture would otherwise draw a huge image on every move)
        new = shape_layer(old.name, replace(old.shape, points=pts), old, doc.pasteboard())
        if new is None:
            return None
        cur = doc.layers[index]
        doc.layers = list(doc.layers)
        doc.layers[index] = new
        return cur.bounds.union(new.bounds)

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


# ------------------------------------------------------------------ shape layers


@dataclass(frozen=True)
class ShapeSpec:
    """Everything needed to draw a shape layer again. ``points`` (x0, y0, x1, y1) are relative
    to the layer's x / y while it is stored on a layer, canvas coordinates in between."""

    kind: str  # "line" | "arrow" | "rect" | "ellipse"
    points: tuple
    color: tuple = (0, 0, 0, 255)  # line / outline
    fill: tuple = (255, 255, 255, 255)
    width: int = 4
    mode: str = "outline"  # rect / ellipse: "outline" | "filled" | "both"
    heads: str = "end"  # arrow: "end" | "both"
    opacity: int = 100

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("points", "color", "fill"):
            d[k] = list(d[k])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ShapeSpec:
        known = set(cls.__dataclass_fields__)
        clean = {k: (tuple(v) if k in ("points", "color", "fill") else v) for k, v in d.items()}
        spec = cls(**{k: v for k, v in clean.items() if k in known})
        if spec.kind not in ("line", "arrow", "rect", "ellipse") or len(spec.points) != 4:
            raise ValueError("not a shape this version can draw")
        return spec


_KINDS: dict[str, type] = {}


def _tool_for(kind: str):
    if not _KINDS:
        _KINDS.update(line=LineTool, arrow=ArrowTool, rect=RectTool, ellipse=EllipseTool)
    return _KINDS[kind]()


def spec_from(kind: str, pts, ctx: ToolContext) -> ShapeSpec:
    """The shape a tool just drew (canvas points) with its settings and colours."""
    o = ctx.options
    line = kind in ("line", "arrow")
    return ShapeSpec(
        kind,
        tuple(float(v) for v in pts),
        tuple(ctx.color if line else ctx.primary),
        tuple(ctx.secondary),
        int(o.width),
        getattr(o, "mode", "outline"),
        getattr(o, "heads", "end"),
        int(o.opacity),
    )


def _context(spec: ShapeSpec) -> ToolContext:
    tool = _tool_for(spec.kind)
    fields = {"width": spec.width, "opacity": spec.opacity}
    names = set(tool.options_cls.__dataclass_fields__)
    if "mode" in names:
        fields["mode"] = spec.mode
    if "heads" in names:
        fields["heads"] = spec.heads
    return ToolContext(spec.color, spec.fill, tool.options_cls(**fields))


def render_shape(spec: ShapeSpec, limit: Rect | None = None) -> tuple[Image.Image, int, int] | None:
    """(image, x, y) of a shape whose points are in canvas coordinates."""
    return _tool_for(spec.kind).draw_points(spec.points, _context(spec), limit=limit)


def replaced_mask(old: Layer, x: int, y: int, size: tuple[int, int]) -> Image.Image | None:
    """``old``'s mask for the same layer redrawn at (x, y) with ``size``: it stays on the same
    picture pixels; areas it didn't cover yet show (white)."""
    if old.mask is None:
        return None
    mask = Image.new("L", size, 255)
    mask.paste(old.mask, (old.x - x, old.y - y))
    return mask


def shape_layer(
    name: str, spec: ShapeSpec, old: Layer | None = None, limit: Rect | None = None
) -> Layer | None:
    """A layer holding the shape (``spec`` in canvas coordinates). Redrawing ``old`` keeps what
    the user set on it: visibility, opacity, effects and its mask (on the same picture pixels).
    ``limit`` draws only that part (live previews)."""
    drawn = render_shape(spec, limit)
    if drawn is None:
        return None
    img, x, y = drawn
    p = spec.points
    local = replace(spec, points=(p[0] - x, p[1] - y, p[2] - x, p[3] - y))
    layer = Layer(name, img, x=x, y=y, shape=local)
    if old is not None:
        layer = replace(
            layer,
            opacity=old.opacity,
            visible=old.visible,
            mask=replaced_mask(old, x, y, img.size),
            fx=old.fx,
        )
    return layer


def canvas_points(layer: Layer) -> tuple[float, float, float, float]:
    p = layer.shape.points
    return (p[0] + layer.x, p[1] + layer.y, p[2] + layer.x, p[3] + layer.y)


def handle_points(layer: Layer) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = canvas_points(layer)
    if layer.shape.kind in ("line", "arrow"):
        return [(x0, y0), (x1, y1)]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def shape_painted_over(layer: Layer) -> bool:
    """True when a shape layer was changed with other tools since it was drawn (reshaping it
    draws it again, so those changes would go)."""
    if layer.shape is None:
        return False
    drawn = render_shape(replace(layer.shape, points=canvas_points(layer)))
    if drawn is None:
        return True
    img, _x, _y = drawn
    if img.size != layer.image.size:
        return True
    from PIL import ImageChops

    return ImageChops.difference(img, layer.image).getbbox() is not None
