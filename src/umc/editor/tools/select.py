"""Rectangle selection, crop rectangle, and selection commands."""

from __future__ import annotations

import math

from PIL import Image

from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand, SelectionCommand
from umc.editor.tools.base import Tool, ToolContext, clip_rect


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


class SelectTool(_DragRectTool):
    id, label, shortcut = "select", "Rectangle select", "M"

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._start = (x, y)
        self._before = doc.selection
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._start is not None:
            doc.selection = self._rect(doc, x, y)
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._start is None:
            return None
        doc.selection = self._rect(doc, x, y)
        self._start = None
        if doc.selection == self._before:
            return None
        return SelectionCommand(
            self._before, doc.selection, "Select" if doc.selection else "Deselect"
        )

    def cancel(self, doc: Document) -> Rect | None:
        if self._start is not None:
            doc.selection = self._before
            self._start = None
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


def select_all(doc: Document) -> Command | None:
    before = doc.selection
    doc.selection = doc.bounds
    return (
        None if before == doc.selection else SelectionCommand(before, doc.selection, "Select all")
    )


def deselect(doc: Document) -> Command | None:
    if doc.selection is None:
        return None
    before, doc.selection = doc.selection, None
    return SelectionCommand(before, None, "Deselect")


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
    return PatchCommand.from_region("Clear selection", doc, doc.active, region, before)
