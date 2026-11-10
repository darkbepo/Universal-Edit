"""Selection tools (rectangle, ellipse, lasso, polygon lasso, magic wand), crop rectangle, and
clearing the selected area. The selection maths is in ``umc.editor.selection``."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from umc.core.options import opt
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand
from umc.editor.selection import (
    combine,
    contains,
    deselect,
    ellipse_shape,
    mode_for,
    move_selection,
    polygon_shape,
    rect_shape,
    select_all,
    wand_shape,
)
from umc.editor.tools.base import Tool, ToolContext, clip_rect, restrict


class _SelectionMover:
    """Dragging inside the selection (no Shift / Alt) moves its outline, not the pixels.

    Selection tools call ``grab`` on press; while ``active`` they hand move / release over. The
    drag shows the moved outline at once; release makes it one undo step (trimmed to the canvas).
    A click without moving reports ``moved = False`` so the tool can do its usual click.
    """

    def __init__(self) -> None:
        self.active = False
        self.moved = False
        self._start = (0.0, 0.0)
        self._base: tuple | None = None

    def grab(self, doc: Document, x: float, y: float, ctx: ToolContext) -> bool:
        if ctx.shift or ctx.alt or not contains(doc, x, y):
            return False
        self.active, self.moved = True, False
        self._start = (x, y)
        self._base = (doc.selection, doc.selection_mask)
        return True

    def _offset(self, x: float, y: float) -> tuple[int, int]:
        return round(x - self._start[0]), round(y - self._start[1])

    def drag(self, doc: Document, x: float, y: float) -> None:
        dx, dy = self._offset(x, y)
        if (dx, dy) != (0, 0):
            self.moved = True
        rect, mask = self._base
        doc.selection, doc.selection_mask = rect.offset(dx, dy), mask  # preview (not trimmed)

    def drop(self, doc: Document, x: float, y: float) -> Command | None:
        self.drag(doc, x, y)
        self.active = False
        return move_selection(doc, *self._offset(x, y), base=self._base)

    def cancel(self, doc: Document) -> None:
        if self.active:
            doc.selection, doc.selection_mask = self._base
        self.active = False


class _DragRectTool(Tool):
    def __init__(self) -> None:
        self._start: tuple[float, float] | None = None

    def _rect(self, doc: Document, x: float, y: float) -> Rect | None:
        sx, sy = self._start
        if math.hypot(x - sx, y - sy) < 1:
            return None
        # snap to the nearest pixel edge (the mouse is usually between two pixels)
        r = Rect.from_points(round(sx), round(sy), round(x), round(y)).intersect(doc.bounds)
        return None if r.empty else r


class _ShapeSelectTool(_DragRectTool):
    """Drag a shape; Shift adds to the selection, Alt subtracts, both intersect.

    While dragging, ``outline`` is what the canvas draws: ("rect" | "ellipse", Rect) or
    ("poly", points). The selection itself only changes on release (one undo step).
    """

    shape = "rect"

    def __init__(self) -> None:
        super().__init__()
        self.outline = None
        self._mode = "replace"
        self.mover = _SelectionMover()

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self.outline = None
        if self.mover.grab(doc, x, y, ctx):
            return None
        self._start = (x, y)
        self._mode = mode_for(ctx.shift, ctx.alt)
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self.mover.active:
            self.mover.drag(doc, x, y)
        elif self._start is not None:
            r = self._rect(doc, x, y)
            self.outline = None if r is None else (self.shape, r)
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self.mover.active:
            cmd = self.mover.drop(doc, x, y)
            return cmd if self.mover.moved else deselect(doc)  # a click inside: deselect
        if self._start is None:
            return None
        r = self._rect(doc, x, y)
        self._start, self.outline = None, None
        if r is None:  # a click: deselect (replace mode) or nothing
            return deselect(doc) if self._mode == "replace" else None
        shape = rect_shape(doc, r) if self.shape == "rect" else ellipse_shape(doc, r)
        return combine(doc, shape, self._mode)

    def cancel(self, doc: Document) -> Rect | None:
        self.mover.cancel(doc)
        self._start, self.outline = None, None
        return None


class SelectTool(_ShapeSelectTool):
    id, label, shortcut = "select", "Rectangle select", "M"


class EllipseSelectTool(_ShapeSelectTool):
    id, label, shortcut = "ellipse_select", "Ellipse select", "M"
    shape = "ellipse"


class LassoTool(Tool):
    """Freehand: hold the button and draw around the area."""

    id, label, shortcut = "lasso", "Lasso", "Q"

    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self.outline = None
        self._mode = "replace"
        self._down = False
        self.mover = _SelectionMover()

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self.mover.grab(doc, x, y, ctx):
            return None
        self._down = True
        self._mode = mode_for(ctx.shift, ctx.alt)
        self.points = [(x, y)]
        self.outline = ("poly", self.points)
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self.mover.active:
            self.mover.drag(doc, x, y)
        elif self._down and math.hypot(x - self.points[-1][0], y - self.points[-1][1]) >= 1:
            self.points.append((x, y))
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self.mover.active:
            cmd = self.mover.drop(doc, x, y)
            return cmd if self.mover.moved else deselect(doc)
        if not self._down:
            return None
        self.move(doc, x, y, ctx)
        pts, self.points, self.outline, self._down = self.points, [], None, False
        if len(pts) < 3:
            return deselect(doc) if self._mode == "replace" else None
        return combine(doc, polygon_shape(doc, pts), self._mode)

    def cancel(self, doc: Document) -> Rect | None:
        self.mover.cancel(doc)
        self.points, self.outline, self._down = [], None, False
        return None


class PolygonLassoTool(Tool):
    """Click corner after corner; click the first corner again, double-click or press Enter to
    finish; Esc cancels. The mode (Shift / Alt) is taken from the first click."""

    id, label, shortcut = "polygon_lasso", "Polygon lasso", "Q"
    CLOSE = 8  # screen pixels: how close to the first corner counts as closing

    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self.outline = None
        self._mode = "replace"
        self.mover = _SelectionMover()

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if not self.points:
            if self.mover.grab(doc, x, y, ctx):  # only before the first corner
                return None
            self._mode = mode_for(ctx.shift, ctx.alt)
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self.mover.active:
            self.mover.drag(doc, x, y)
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self.mover.active:
            cmd = self.mover.drop(doc, x, y)
            if self.mover.moved:
                return cmd
            self._mode = "replace"  # a click inside: start a polygon there, as usual
        if len(self.points) >= 3:
            fx, fy = self.points[0]
            if math.hypot(x - fx, y - fy) * max(ctx.zoom, 1e-6) <= self.CLOSE:
                return self.finish(doc)
        if not self.points or self.points[-1] != (x, y):
            self.points.append((x, y))
        self.outline = ("poly", self.points)
        return None

    def finish(self, doc: Document) -> Command | None:
        """Close the polygon and select it (Enter / double-click)."""
        pts, self.points, self.outline = self.points, [], None
        if len(pts) < 3:
            return None
        return combine(doc, polygon_shape(doc, pts), self._mode)

    def cancel(self, doc: Document) -> Rect | None:
        self.mover.cancel(doc)
        self.points, self.outline = [], None
        return None


@dataclass
class WandOptions:
    tolerance: int = opt(32, "Tolerance", min=0, max=255, kind="slider")
    contiguous: bool = opt(True, "Only touching areas")
    sample_all: bool = opt(False, "Sample all layers")


class MagicWandTool(Tool):
    """Click a colour: select it where it touches (or everywhere, without "Only touching")."""

    id, label, shortcut = "wand", "Magic wand", "W"
    options_cls = WandOptions

    def __init__(self) -> None:
        self._mode = "replace"
        self._down = False
        self.mover = _SelectionMover()

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._mode = mode_for(ctx.shift, ctx.alt)
        self._down = True
        self.mover.grab(doc, x, y, ctx)  # a drag inside moves it; a click picks as usual
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self.mover.active:
            self.mover.drag(doc, x, y)
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if not self._down:
            return None
        self._down = False
        if self.mover.active:
            cmd = self.mover.drop(doc, x, y)
            if self.mover.moved:
                return cmd
        o = ctx.options
        shape = wand_shape(doc, x, y, o.tolerance, o.contiguous, o.sample_all)
        return combine(doc, shape, self._mode, "Magic wand")

    def cancel(self, doc: Document) -> Rect | None:
        self.mover.cancel(doc)
        self._down = False
        return None


class CropTool(_DragRectTool):
    """Drag a rectangle; the page applies it with ``ops.crop`` on Enter."""

    id, label, shortcut = "crop", "Crop", "C"

    def __init__(self) -> None:
        super().__init__()
        self.pending: Rect | None = None

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._start = (x, y)
        self.pending = None
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._start is not None:
            self.pending = self._rect(doc, x, y)
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        self.move(doc, x, y, ctx)
        self._start = None
        return None

    def cancel(self, doc: Document) -> Rect | None:
        self._start = None
        self.pending = None
        return None


def clear_selection(doc: Document) -> Command | None:
    """Make the selected part of the active layer transparent."""
    if doc.selection is None:
        return None
    layer = doc.active_layer
    region = clip_rect(doc, layer)
    if region.empty:
        return None
    before = layer.image.crop(region.box)
    layer.image.paste(Image.new("RGBA", (region.width, region.height)), (region.x0, region.y0))
    restrict(doc, layer, region, before)  # shaped / soft selections clear only that much
    return PatchCommand.from_region("Clear selection", doc, doc.active, region, before)


__all__ = ["clear_selection", "deselect", "select_all"]  # re-exported for the page / tests
