"""The vector editor's canvas: the drawing (Qt's SVG renderer), the page, and what the tools
draw on top — selection boxes and handles, the rubber band, path points, shapes being drawn.

The canvas never changes the document itself: a finished gesture is reported through
``edited`` (new document, undo label, merge key) and the page pushes it onto the history.
While a gesture runs, ``preview`` holds the document as it would be, and is what's drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from umc.gui import theme
from umc.gui.touch import PinchZoom
from umc.vectoredit import doc as vdoc
from umc.vectoredit import pathdata, shapes, xmlsrc
from umc.vectoredit.doc import Path, VectorDoc, VectorError
from umc.vectoredit.geom import Box, Matrix

TOOLS = ("select", "node", "rect", "ellipse", "line", "pen", "text")
HANDLE = 4.0  # half size of a resize handle (screen px)
GRAB = 6.0  # how near (screen px) a press must be to a handle or point
DRAG_START = 3.0  # screen px before a press becomes a drag
MIN_ZOOM, MAX_ZOOM = 0.02, 64.0
# Resize handles: (name, x factor, y factor) of the box; the opposite corner stays put.
HANDLES = (
    ("nw", 0, 0), ("n", 0.5, 0), ("ne", 1, 0), ("e", 1, 0.5),
    ("se", 1, 1), ("s", 0.5, 1), ("sw", 0, 1), ("w", 0, 0.5),
)  # fmt: skip


@dataclass
class _Gesture:
    kind: str  # "move", "resize", "band", "draw", "point", "pan"
    start: QPointF  # screen position of the press
    doc_start: tuple[float, float]  # the press in document units
    base: VectorDoc  # the document when it began
    handle: str = ""
    box: Box | None = None  # selection box at the start (resize)
    point: tuple[int, str] | None = None  # (segment, slot) being dragged (point)
    moved: bool = False


class VectorCanvas(QWidget):
    edited = Signal(object, str, object)  # new document, undo label, merge key (or None)
    selection_changed = Signal()
    view_changed = Signal()
    tool_changed = Signal(str)
    message = Signal(str)
    text_requested = Signal(float, float)  # the Text tool was clicked here (document units)
    text_edit_requested = Signal(object)  # double-click on a text element (its path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VectorCanvas")
        self.setAccessibleName("Drawing")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 160)
        self.doc: VectorDoc | None = None
        self.preview: VectorDoc | None = None
        self.selection: list[Path] = []
        self.tool = "select"
        self.style_for_new: list[tuple[str, str]] = [("fill", "#4a90d9"), ("stroke", "#1d3c5e")]
        self.scale = 1.0
        self.origin = QPointF(0, 0)  # where document (0, 0) is on screen
        self.node: tuple[int, str] | None = None  # the chosen path point (Node tool)
        self.pen_nodes: list[tuple[float, float, float, float]] = []  # x, y, handle dx, dy
        self.pen_hover: tuple[float, float] | None = None
        self._gesture: _Gesture | None = None
        self._band: QRectF | None = None
        self._draw_box: Box | None = None
        self._draw_end: tuple[float, float] = (0.0, 0.0)
        self._image: QImage | None = None
        self._image_key: tuple | None = None
        self._renderer_ok = True
        self._fitted = False
        self.pinch = PinchZoom(self, self.zoom_by, self.pan_view, self.cancel_gesture)

    def pan_view(self, delta: QPointF) -> None:
        """Move the drawing by ``delta`` screen px (two-finger pan)."""
        self.origin += delta
        self._image = None
        self.view_changed.emit()
        self.update()

    # ------------------------------------------------------------------ document / view
    def set_document(self, doc: VectorDoc, fit: bool = False) -> None:
        self.doc = doc
        self.preview = None
        self.selection = [p for p in self.selection if self._exists(doc, p)]
        if self.node is not None and not self._node_valid():
            self.node = None
        if fit or not self._fitted:
            self.fit()
        self._image = None
        self.update()

    def set_selection(self, paths: list[Path]) -> None:
        doc = self.shown()
        paths = [tuple(p) for p in paths if doc is not None and self._exists(doc, tuple(p))]
        if paths != self.selection:
            self.selection = paths
            self.node = None
            self.selection_changed.emit()
        self.update()

    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            raise ValueError(tool)
        self.finish_pen()
        self.tool = tool
        self.node = None
        self.setCursor(
            Qt.CursorShape.ArrowCursor if tool in ("select", "node") else Qt.CursorShape.CrossCursor
        )
        self.tool_changed.emit(tool)
        self.update()

    def shown(self) -> VectorDoc | None:
        return self.preview if self.preview is not None else self.doc

    def _node_valid(self) -> bool:
        """The chosen point still exists in the chosen path."""
        if self.node is None or len(self.selection) != 1 or self.doc is None:
            return False
        el = vdoc.get(self.doc, self.selection[0])
        if shapes.kind(el) != "path":
            return False
        pts = pathdata.points(pathdata.parse(el.get("d") or ""))
        return any((i, slot) == self.node for i, slot, _x, _y in pts)

    @staticmethod
    def _exists(doc: VectorDoc, path: Path) -> bool:
        try:
            vdoc.get(doc, path)
        except IndexError:
            return False
        return bool(path)

    def view_box(self) -> Box:
        if self.doc is None:
            return Box(0, 0, 100, 100)
        return vdoc.canvas(self.doc)[2]

    def fit(self) -> None:
        """The whole page in view, with a margin."""
        view = self.view_box()
        w, h = max(self.width(), 50), max(self.height(), 50)
        s = min((w - 48) / max(view.width, 1e-9), (h - 48) / max(view.height, 1e-9))
        self.scale = min(max(s, MIN_ZOOM), MAX_ZOOM)
        self.origin = QPointF(
            (w - view.width * self.scale) / 2 - view.x0 * self.scale,
            (h - view.height * self.scale) / 2 - view.y0 * self.scale,
        )
        self._fitted = self.width() > 50
        self._image = None
        self.view_changed.emit()
        self.update()

    def set_zoom(self, scale: float, anchor: QPointF | None = None) -> None:
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        x, y = self.to_doc(anchor)
        self.scale = min(max(scale, MIN_ZOOM), MAX_ZOOM)
        self.origin = QPointF(anchor.x() - x * self.scale, anchor.y() - y * self.scale)
        self._image = None
        self.view_changed.emit()
        self.update()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        self.set_zoom(self.scale * factor, anchor)

    def to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(self.origin.x() + x * self.scale, self.origin.y() + y * self.scale)

    def to_doc(self, p: QPointF) -> tuple[float, float]:
        return (p.x() - self.origin.x()) / self.scale, (p.y() - self.origin.y()) / self.scale

    def step(self) -> float:
        """Drags land on multiples of this (1 at 100 %, 0.1 zoomed in, 10 zoomed out), so the
        file gets tidy numbers instead of what a screen pixel happens to be."""
        return 10.0 ** math.floor(math.log10(max(1.0 / self.scale, 1e-3)) + 1e-9)

    def snapped(self, v: float) -> float:
        s = self.step()
        return round(round(v / s) * s, 6)

    def screen_rect(self, b: Box) -> QRectF:
        a, c = self.to_screen(b.x0, b.y0), self.to_screen(b.x1, b.y1)
        return QRectF(a, c).normalized()

    def selection_box(self) -> Box | None:
        doc = self.shown()
        return None if doc is None else vdoc.selection_bounds(doc, self.selection)

    def element_matrix(self, path: Path) -> Matrix:
        """The element's own coordinates → document units."""
        doc = self.shown()
        assert doc is not None
        return vdoc.parent_matrix(doc, path) @ shapes.own_transform(vdoc.get(doc, path))

    # ------------------------------------------------------------------ painting
    def resizeEvent(self, e) -> None:  # noqa: N802
        if not self._fitted:
            self.fit()
        self._image = None
        super().resizeEvent(e)

    def _render(self) -> QImage:
        doc = self.shown()
        ratio = self.devicePixelRatioF()
        size = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        key = (id(doc), size.width(), size.height(), self.scale, self.origin.x(), self.origin.y())
        if self._image is not None and self._image_key == key:
            return self._image
        image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        if doc is not None:
            view = vdoc.canvas(doc)[2]
            renderer = QSvgRenderer(QByteArray(vdoc.write(doc).encode("utf-8")))
            self._renderer_ok = renderer.isValid()
            if self._renderer_ok:
                renderer.setViewBox(QRectF(view.x0, view.y0, view.width, view.height))
                renderer.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
                p = QPainter(image)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                renderer.render(p, self.screen_rect(view))
                p.end()
        self._image, self._image_key = image, key
        return image

    def paintEvent(self, _e) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["bg"]))
        if self.doc is None:
            return
        page = self.screen_rect(self.view_box())
        p.fillRect(page.translated(3, 3), QColor(0, 0, 0, 40))
        p.fillRect(page, QColor("#ffffff"))
        p.drawImage(QPointF(0, 0), self._render())
        p.setPen(QPen(QColor(pal["border"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(page)
        if not self._renderer_ok:
            p.setPen(QColor(pal["muted"]))
            p.drawText(page, Qt.AlignmentFlag.AlignCenter, "This drawing can't be shown here")
        self._paint_overlay(p, QColor(pal["accent"]))

    def _paint_overlay(self, p: QPainter, accent: QColor) -> None:
        doc = self.shown()
        assert doc is not None
        thin = QPen(accent, 1, Qt.PenStyle.DashLine)
        for path in self.selection:
            b = vdoc.bounds(doc, path)
            if b is not None:
                p.setPen(thin)
                p.drawRect(self.screen_rect(b))
        box = self.selection_box()
        if self.tool == "select" and box is not None and self._gesture is None:
            p.setPen(QPen(accent, 1))
            p.setBrush(QColor("#ffffff"))
            for _name, cx, cy in self.handle_points(box):
                p.drawRect(QRectF(cx - HANDLE, cy - HANDLE, 2 * HANDLE, 2 * HANDLE))
        if self._band is not None:
            fill = QColor(accent)
            fill.setAlpha(40)
            p.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            p.setBrush(fill)
            p.drawRect(self._band)
        if self._draw_box is not None:
            p.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = self.screen_rect(self._draw_box)
            if self.tool == "ellipse":
                p.drawEllipse(r)
            elif self.tool == "line":
                g = self._gesture
                a = self.to_screen(*g.doc_start) if g else r.topLeft()
                b2 = self.to_screen(*self._draw_end) if g else r.bottomRight()
                p.drawLine(a, b2)
            else:
                p.drawRect(r)
        if self.tool == "node":
            self._paint_points(p, accent)
        if self.tool == "pen" and self.pen_nodes:
            p.setPen(QPen(accent, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._pen_qpath(with_hover=True))
            for x, y, _hx, _hy in self.pen_nodes:
                c = self.to_screen(x, y)
                p.drawRect(QRectF(c.x() - 3, c.y() - 3, 6, 6))

    def handle_points(self, box: Box) -> list[tuple[str, float, float]]:
        r = self.screen_rect(box)
        return [(n, r.left() + fx * r.width(), r.top() + fy * r.height()) for n, fx, fy in HANDLES]

    def node_points(self) -> list[tuple[int, str, QPointF]]:
        """The chosen path's points on screen (Node tool), or nothing."""
        doc = self.shown()
        if self.tool != "node" or len(self.selection) != 1 or doc is None:
            return []
        el = vdoc.get(doc, self.selection[0])
        if shapes.kind(el) != "path":
            return []
        m = self.element_matrix(self.selection[0])
        out = []
        for i, slot, x, y in pathdata.points(pathdata.parse(el.get("d") or "")):
            out.append((i, slot, self.to_screen(*m.apply(x, y))))
        return out

    def _paint_points(self, p: QPainter, accent: QColor) -> None:
        pts = self.node_points()
        ends = {i: q for i, slot, q in pts if slot == "end"}
        p.setPen(QPen(accent, 1))
        for i, slot, q in pts:
            if slot == "end":
                continue
            # a handle hangs off its node: c1 off the node before, c2 off its own end
            owner = ends.get(i - 1) if slot == "c1" else ends.get(i)
            if owner is not None:
                p.drawLine(owner, q)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(q, 3, 3)
        for i, _slot, q in [t for t in pts if t[1] == "end"]:
            chosen = self.node == (i, "end")
            p.setBrush(accent if chosen else QColor("#ffffff"))
            p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))

    # ------------------------------------------------------------------ mouse
    def wheelEvent(self, e) -> None:  # noqa: N802
        """Ctrl+wheel zooms at the pointer; the wheel scrolls (Shift: sideways)."""
        delta = e.angleDelta()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 ** (delta.y() / 120), e.position())
            return
        dx, dy = delta.x(), delta.y()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = dy, 0
        self.origin += QPointF(dx / 2, dy / 2)
        self._image = None
        self.view_changed.emit()
        self.update()

    def _hit_handle(self, pos: QPointF) -> str | None:
        box = self.selection_box()
        if self.tool != "select" or box is None:
            return None
        for name, x, y in self.handle_points(box):
            if abs(pos.x() - x) <= GRAB and abs(pos.y() - y) <= GRAB:
                return name
        return None

    def _hit_point(self, pos: QPointF) -> tuple[int, str] | None:
        best, best_d = None, GRAB
        for i, slot, q in self.node_points():
            d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
            if d <= best_d:
                best, best_d = (i, slot), d
        return best

    def hit(self, pos: QPointF) -> Path | None:
        doc = self.shown()
        if doc is None:
            return None
        x, y = self.to_doc(pos)
        return vdoc.hit(doc, x, y, pad=2 / self.scale)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.doc is None:
            return
        self.setFocus()
        pos = e.position()
        start = self.to_doc(pos)
        if e.button() == Qt.MouseButton.MiddleButton:
            self._gesture = _Gesture("pan", pos, start, self.doc)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        g = _Gesture("", pos, start, self.doc)
        if self.tool == "select":
            handle = self._hit_handle(pos)
            if handle:
                g.kind, g.handle, g.box = "resize", handle, self.selection_box()
            else:
                hit = self.hit(pos)
                if hit is None:
                    if not shift:
                        self.set_selection([])
                    g.kind = "band"
                else:
                    if shift:
                        sel = [s for s in self.selection if s != hit]
                        self.set_selection(sel if hit in self.selection else [*self.selection, hit])
                    elif hit not in self.selection:
                        self.set_selection([hit])
                    g.kind = "move" if self.selection else ""
        elif self.tool == "node":
            point = self._hit_point(pos)
            if point is not None:
                self.node = point
                g.kind, g.point = "point", point
            else:
                hit = self.hit(pos)
                self.set_selection([hit] if hit is not None else [])
                self.node = None
        elif self.tool in ("rect", "ellipse", "line"):
            start = (self.snapped(start[0]), self.snapped(start[1]))
            g.doc_start = start
            g.kind = "draw"
            self._draw_end = start
        elif self.tool == "pen":
            g.kind = "pen"
            if self._closes_pen(pos):
                self.finish_pen(close=True)
                return
            self.pen_nodes.append((self.snapped(start[0]), self.snapped(start[1]), 0.0, 0.0))
        elif self.tool == "text":
            self.text_requested.emit(*start)
            return
        self._gesture = g
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        pos = e.position()
        g = self._gesture
        if self.tool == "pen":
            self.pen_hover = self.to_doc(pos)
        if g is None:
            self._hover_cursor(pos)
            self.update()
            return
        if not g.moved and math.hypot(pos.x() - g.start.x(), pos.y() - g.start.y()) < DRAG_START:
            return
        g.moved = True
        x, y = (self.snapped(v) for v in self.to_doc(pos))
        dx = self.snapped(x - self.snapped(g.doc_start[0]))
        dy = self.snapped(y - self.snapped(g.doc_start[1]))
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        try:
            if g.kind == "pan":
                self.origin += pos - g.start
                g.start = pos
                self._image = None
                self.view_changed.emit()
            elif g.kind == "move":
                if shift:  # along one direction only
                    dx, dy = (dx, 0.0) if abs(dx) >= abs(dy) else (0.0, dy)
                self.preview = vdoc.move(g.base, self.selection, dx, dy)
            elif g.kind == "resize" and g.box is not None:
                box = self._resized(g.box, g.handle, x, y, shift)
                if box is not None:
                    self.preview = vdoc.resize(g.base, self.selection, box)
            elif g.kind == "band":
                self._band = QRectF(g.start, pos).normalized()
            elif g.kind == "draw":
                self._draw_end = (x, y)
                self._draw_box = self._drawn_box(g.doc_start, (x, y), shift)
            elif g.kind == "point" and g.point is not None:
                self.preview = self._moved_point(g, x, y)
            elif g.kind == "pen" and self.pen_nodes:
                nx, ny, _hx, _hy = self.pen_nodes[-1]
                self.pen_nodes[-1] = (nx, ny, x - nx, y - ny)
        except VectorError:
            pass
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        g, self._gesture = self._gesture, None
        if g is None:
            return
        preview, self.preview = self.preview, None
        if g.kind == "band" and self._band is not None and self.doc is not None:
            a = self.to_doc(self._band.topLeft())
            b = self.to_doc(self._band.bottomRight())
            found = vdoc.in_box(self.doc, Box(a[0], a[1], b[0], b[1]))
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            keep = [s for s in self.selection if s not in found] if shift else []
            self.set_selection([*keep, *found])
        elif g.kind == "draw" and self._draw_box is not None:
            self._finish_draw(g)
        elif g.moved and preview is not None:
            label = {"move": "Move", "resize": "Resize", "point": "Move point"}.get(g.kind, "Edit")
            self.edited.emit(preview, label, None)
        self._band = None
        self._draw_box = None
        self._image = None
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.doc is None or e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        if self.tool == "pen":
            self.finish_pen()
            return
        hit = self.hit(pos)
        if self.tool == "select" and hit is not None:
            kind = shapes.kind(vdoc.get(self.doc, hit))
            if kind == "text":
                self.text_edit_requested.emit(hit)
            elif kind in ("path", *shapes.GEOMETRY):
                self.set_selection([hit])
                self.set_tool("node")
            return
        if self.tool == "node":
            self._double_click_node(pos, hit)

    def _double_click_node(self, pos: QPointF, hit: Path | None) -> None:
        """On a simple shape: make it a path. On a path's line: add a point there."""
        assert self.doc is not None
        if hit is not None and hit not in self.selection:
            self.set_selection([hit])
        if len(self.selection) != 1:
            return
        path = self.selection[0]
        el = vdoc.get(self.doc, path)
        if shapes.kind(el) in shapes.GEOMETRY:
            self.edited.emit(vdoc.to_path(self.doc, [path]), "Turn into path", None)
            return
        if shapes.kind(el) != "path":
            return
        found = self._nearest_segment(path, pos)
        if found is not None:
            segs = pathdata.parse(el.get("d") or "")
            self.edited.emit(
                vdoc.set_path(self.doc, path, pathdata.split(segs, *found)), "Add point", None
            )

    def _nearest_segment(self, path: Path, pos: QPointF) -> tuple[int, float] | None:
        """(segment, t) of the path's line nearest to ``pos`` (within a few pixels)."""
        assert self.doc is not None
        el = vdoc.get(self.doc, path)
        segs = pathdata.parse(el.get("d") or "")
        m = self.element_matrix(path)
        best, best_d = None, GRAB * 2
        x = y = sx = sy = 0.0
        for i, s in enumerate(segs):
            if s[0] not in ("M", "Z"):
                for k in range(1, 32):
                    t = k / 32
                    px, py = _point_on(s, x, y, t)
                    q = self.to_screen(*m.apply(px, py))
                    d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                    if d < best_d:
                        best, best_d = (i, t), d
            x, y, sx, sy = pathdata._advance(s, x, y, sx, sy)
        return best

    # ------------------------------------------------------------------ gesture helpers
    def _hover_cursor(self, pos: QPointF) -> None:
        if self.tool != "select":
            return
        handle = self._hit_handle(pos)
        if handle in ("nw", "se"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in ("ne", "sw"):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in ("n", "s"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif handle in ("e", "w"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.hit(pos) is not None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    @staticmethod
    def _resized(box: Box, handle: str, x: float, y: float, keep_ratio: bool) -> Box | None:
        x0, y0, x1, y1 = box.x0, box.y0, box.x1, box.y1
        if "w" in handle:
            x0 = x
        if "e" in handle:
            x1 = x
        if "n" in handle:
            y0 = y
        if "s" in handle:
            y1 = y
        if keep_ratio and box.width > 0 and box.height > 0 and len(handle) == 2:
            s = max(abs(x1 - x0) / box.width, abs(y1 - y0) / box.height)
            w, h = box.width * s, box.height * s
            x0, x1 = (x1 - w, x1) if "w" in handle else (x0, x0 + w)
            y0, y1 = (y1 - h, y1) if "n" in handle else (y0, y0 + h)
        new = Box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if box.width == 0:
            new = Box(box.x0, new.y0, box.x1, new.y1)
        if box.height == 0:
            new = Box(new.x0, box.y0, new.x1, box.y1)
        if new.width <= 0 and box.width > 0 or new.height <= 0 and box.height > 0:
            return None
        return new

    def _drawn_box(self, a: tuple[float, float], b: tuple[float, float], square: bool) -> Box:
        x0, y0 = a
        x1, y1 = b
        if square:
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + math.copysign(side, x1 - x0 or 1)
            y1 = y0 + math.copysign(side, y1 - y0 or 1)
        if self.tool == "line":
            return Box(x0, y0, x1, y1)  # direction kept: the line goes from the press
        return Box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _finish_draw(self, g: _Gesture) -> None:
        assert self.doc is not None and self._draw_box is not None
        box = self._draw_box
        big = max(abs(box.x1 - box.x0), abs(box.y1 - box.y0)) * self.scale
        if not g.moved or big < 2:
            return
        style = list(self.style_for_new)
        if self.tool == "line":
            style = [(k, v) for k, v in style if k != "fill"]
            if not any(k == "stroke" for k, _ in style):
                style.append(("stroke", "#000000"))
        el = vdoc.new_shape(self.tool, box, style, vdoc.ids(self.doc))
        doc, path = vdoc.add(self.doc, el)
        self.selection = [path]
        self.edited.emit(
            doc, {"rect": "Rectangle", "ellipse": "Ellipse", "line": "Line"}[self.tool], None
        )
        self.selection_changed.emit()

    def _moved_point(self, g: _Gesture, x: float, y: float) -> VectorDoc:
        assert g.point is not None and self.selection
        path = self.selection[0]
        el = vdoc.get(g.base, path)
        local = (vdoc.parent_matrix(g.base, path) @ shapes.own_transform(el)).inverse()
        lx, ly = local.apply(x, y)
        segs = pathdata.parse(el.get("d") or "")
        return vdoc.set_path(
            g.base, path, pathdata.move_point(segs, g.point[0], g.point[1], lx, ly)
        )

    # ------------------------------------------------------------------ pen
    def _pen_segs(self, close: bool = False, hover: tuple[float, float] | None = None) -> list:
        nodes = list(self.pen_nodes)
        if hover is not None:
            nodes.append((*hover, 0.0, 0.0))
        if not nodes:
            return []
        segs: list = [("M", nodes[0][0], nodes[0][1])]
        pairs = list(zip(nodes, nodes[1:], strict=False))
        if close and len(nodes) > 2:
            pairs.append((nodes[-1], nodes[0]))
        for (ax, ay, ahx, ahy), (bx, by, bhx, bhy) in pairs:
            if ahx == ahy == bhx == bhy == 0:
                segs.append(("L", bx, by))
            else:
                segs.append(("C", ax + ahx, ay + ahy, bx - bhx, by - bhy, bx, by))
        if close:
            segs.append(("Z",))
        return segs

    def _pen_qpath(self, with_hover: bool) -> QPainterPath:
        path = QPainterPath()
        segs = self._pen_segs(
            hover=self.pen_hover if with_hover and self._gesture is None else None
        )
        for s in segs:
            if s[0] == "M":
                path.moveTo(self.to_screen(s[1], s[2]))
            elif s[0] == "L":
                path.lineTo(self.to_screen(s[1], s[2]))
            elif s[0] == "C":
                path.cubicTo(
                    self.to_screen(s[1], s[2]),
                    self.to_screen(s[3], s[4]),
                    self.to_screen(s[5], s[6]),
                )
        return path

    def _closes_pen(self, pos: QPointF) -> bool:
        if len(self.pen_nodes) < 3:
            return False
        first = self.to_screen(self.pen_nodes[0][0], self.pen_nodes[0][1])
        return math.hypot(first.x() - pos.x(), first.y() - pos.y()) <= GRAB

    def finish_pen(self, close: bool = False) -> None:
        """End the path being drawn with the Pen tool (Enter, double-click, another tool)."""
        # a double-click to finish may also have clicked on the last spot: drop repeated points
        nodes = [
            n for i, n in enumerate(self.pen_nodes) if i == 0 or n[:2] != self.pen_nodes[i - 1][:2]
        ]
        self.pen_nodes = []
        self.pen_hover = None
        if self.doc is None or len(nodes) < 2:
            self.update()
            return
        self.pen_nodes = nodes
        segs = self._pen_segs(close=close)
        self.pen_nodes = []
        style = (
            list(self.style_for_new)
            if close
            else [(k, v) for k, v in self.style_for_new if k != "fill"]
        )
        if not close:
            style = [("fill", "none"), *style]
            if not any(k == "stroke" for k, _ in style):
                style.append(("stroke", "#000000"))
        taken = vdoc.ids(self.doc)
        attrs = [("id", vdoc.unique_id(taken, "path")), ("d", pathdata.write(segs))]
        attrs.append(("style", shapes.write_style(style)))
        doc, path = vdoc.add(self.doc, xmlsrc.element("path", attrs))
        self.selection = [path]
        self.edited.emit(doc, "Pen", None)
        self.selection_changed.emit()
        self.update()

    def cancel_gesture(self) -> bool:
        """Esc: drop a drag or a pen path being drawn. True if there was one."""
        busy = self._gesture is not None or bool(self.pen_nodes)
        self._gesture = None
        self.preview = None
        self.pen_nodes = []
        self._band = None
        self._draw_box = None
        self._image = None
        self.update()
        return busy


def _point_on(s: tuple, x0: float, y0: float, t: float) -> tuple[float, float]:
    """A point on segment ``s`` (starting at x0, y0) at ``t``; arcs are taken as straight."""
    if s[0] == "C":
        u = 1 - t
        return (
            u**3 * x0 + 3 * u * u * t * s[1] + 3 * u * t * t * s[3] + t**3 * s[5],
            u**3 * y0 + 3 * u * u * t * s[2] + 3 * u * t * t * s[4] + t**3 * s[6],
        )
    if s[0] == "Q":
        u = 1 - t
        return (
            u * u * x0 + 2 * u * t * s[1] + t * t * s[3],
            u * u * y0 + 2 * u * t * s[2] + t * t * s[4],
        )
    return x0 + (s[-2] - x0) * t, y0 + (s[-1] - y0) * t
