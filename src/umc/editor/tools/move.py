"""Move tool: drag the selected layers (Shift keeps the move horizontal or vertical)."""

from __future__ import annotations

from dataclasses import replace

from umc.editor.document import Document
from umc.editor.geometry import EMPTY, Rect
from umc.editor.history import Command, LayerListCommand
from umc.editor.layer import Layer
from umc.editor.tools.base import Tool, ToolContext


def _bounds(layers: list[Layer], indices: list[int]) -> Rect:
    r = EMPTY
    for i in indices:
        r = layers[i].bounds if r.empty else r.union(layers[i].bounds)
    return r


class MoveTool(Tool):
    id, label, shortcut = "move", "Move", "V"
    cursor = "move"

    def __init__(self) -> None:
        self._start: tuple[float, float] | None = None
        self._before: list[Layer] = []
        self._indices: list[int] = []
        self._shown = EMPTY  # where the moved layers are drawn now (canvas coordinates)

    def _offset(self, x: float, y: float, ctx: ToolContext) -> tuple[int, int]:
        dx, dy = round(x - self._start[0]), round(y - self._start[1])
        if ctx.shift:  # keep to one axis
            if abs(dx) >= abs(dy):
                dy = 0
            else:
                dx = 0
        return dx, dy

    def _apply(self, doc: Document, dx: int, dy: int) -> Rect:
        layers = list(self._before)
        for i in self._indices:
            layers[i] = replace(self._before[i], x=self._before[i].x + dx, y=self._before[i].y + dy)
        doc.layers = layers
        now = _bounds(layers, self._indices)
        dirty = now if self._shown.empty else self._shown.union(now)
        self._shown = now
        return dirty

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        self._start = (x, y)
        self._before = list(doc.layers)
        self._indices = doc.selected_indices
        self._shown = _bounds(self._before, self._indices)
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        if self._start is None:
            return None
        return self._apply(doc, *self._offset(x, y, ctx))

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        if self._start is None:
            return None
        dx, dy = self._offset(x, y, ctx)
        self._apply(doc, dx, dy)
        self._start = None
        if (dx, dy) == (0, 0):
            doc.layers = self._before
            return None
        selected = set(doc.selected)
        return LayerListCommand(
            "Move", self._before, list(doc.layers), doc.active, doc.active, selected, selected
        )

    def cancel(self, doc: Document) -> Rect | None:
        if self._start is None:
            return None
        self._start = None
        dirty = self._apply(doc, 0, 0)
        doc.layers = self._before
        return dirty
