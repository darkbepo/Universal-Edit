"""The editor canvas: zoom / pan, mouse → tool, a cached composite repainted by dirty rect."""

from __future__ import annotations

import math
from collections.abc import Callable

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from umc.editor import layerfx
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.selection import contains as selection_contains
from umc.editor.selection import nudge_selection
from umc.editor.selection import outline as selection_outline
from umc.editor.tools.base import Tool, ToolContext
from umc.gui import theme
from umc.gui.editor.crop_bar import nearest_corner, place_bar

SELECT_TOOLS = ("select", "ellipse_select", "lasso", "polygon_lasso", "wand")

MIN_ZOOM, MAX_ZOOM = 0.05, 32.0


def pil_to_qimage(img: Image.Image) -> QImage:
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888).copy()


def qimage_to_pil(img: QImage) -> Image.Image:
    """Any QImage (e.g. from the clipboard) → a Pillow RGBA image."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(img.constBits())[: img.bytesPerLine() * img.height()]
    return Image.frombuffer(
        "RGBA", (img.width(), img.height()), data, "raw", "RGBA", img.bytesPerLine(), 1
    ).copy()


def _checker() -> QPixmap:
    pm = QPixmap(16, 16)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 8, QColor("#d9d9d9"))
    p.fillRect(8, 8, 8, 8, QColor("#d9d9d9"))
    p.end()
    return pm


class EditorCanvas(QWidget):
    changed = Signal()  # after any push / undo / redo
    color_picked = Signal(tuple, str)  # colour, button
    zoom_changed = Signal(float)
    cursor_moved = Signal(int, int)
    text_requested = Signal(float, float)  # text tool clicked at image coordinates
    nudge_requested = Signal(int, int)  # arrow keys with the Move tool
    tool_notice = Signal(str)  # a tool needs something first (e.g. a clone source)
    view_changed = Signal()  # zoomed or panned (things placed over the picture follow)
    before_press = None  # called first on every mouse press (set by the page)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 200)
        self.doc: Document | None = None
        self.history: History | None = None
        self.tool: Tool | None = None
        self.context_provider: Callable[[], ToolContext] | None = None
        self.zoom = 1.0
        self._origin = QPointF(0, 0)  # widget position of image pixel (0, 0)
        self._cache: QImage | None = None
        self._view = Rect(0, 0, 0, 0)  # the area the cache covers: canvas + pasteboard
        self._preview_layer: tuple[int, Image.Image] | None = None
        self._pressed: str | None = None  # "left" | "right" | "pan"
        self._pan_from = QPointF()
        self._space = False
        self._mouse: QPointF | None = None
        self._ants = 0
        self._checker = QBrush(_checker())
        self._timer = QTimer(self, interval=150)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        # Floating Apply / Cancel bar (a CropBar, set by the page), next to the crop rectangle or
        # the selection; _release_at = where the last drag ended, in image coordinates.
        self.bar = None
        self._release_at: tuple[float, float] | None = None
        self._outline_key = None
        self._outline_path = QPainterPath()

    # ------------------------------------------------------------ document
    def set_document(self, doc: Document, history: History) -> None:
        self.cancel_gesture()
        self.doc, self.history = doc, history
        self._preview_layer = None
        self.refresh()
        self.fit()

    def set_tool(self, tool: Tool) -> None:
        if tool is not self.tool:
            self.cancel_gesture()  # also a polygon lasso's unfinished corners
        self._pressed = None
        self.tool = tool
        self._restore_cursor()
        self.update()

    def _restore_cursor(self) -> None:
        if self.tool is not None and self.tool.cursor == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()

    def refresh(self, rect: Rect | None = None) -> None:
        """Re-composite ``rect`` (canvas coordinates; None = everything) into the cache."""
        if self.doc is None:
            return
        override = {self._preview_layer[0]: self._preview_layer[1]} if self._preview_layer else None
        step = max(16, max(self.doc.width, self.doc.height) // 8)
        view = self.doc.pasteboard(step)
        grown = view.union(self._view) != self._view
        if rect is None or self._cache is None or grown:
            self._view = view
            self._cache = pil_to_qimage(self.doc.composite(view, override=override, clip=False))
            if rect is None:  # not while dragging: the view would jump under the mouse
                self._clamp()
        else:
            # a change also moves what layer effects draw around it (a shadow, an outline…)
            m = max((layerfx.margin(la.fx) for la in self.doc.layers if la.fx), default=0)
            if m:
                rect = Rect(rect.x0 - m, rect.y0 - m, rect.x1 + m, rect.y1 + m)
            rect = rect.intersect(self._view)
            if not rect.empty:
                part = pil_to_qimage(self.doc.composite(rect, override=override, clip=False))
                p = QPainter(self._cache)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.drawImage(rect.x0 - self._view.x0, rect.y0 - self._view.y0, part)
                p.end()
        self.update()

    def set_preview_layer(self, index: int | None, image: Image.Image | None) -> None:
        """Show ``image`` instead of layer ``index`` (live previews); None restores."""
        self._preview_layer = None if index is None or image is None else (index, image)
        self.refresh()

    def cached_pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        c = self._cache.pixelColor(x - self._view.x0, y - self._view.y0)
        return (c.red(), c.green(), c.blue(), c.alpha())

    # ------------------------------------------------------------ history
    def push(self, cmd) -> None:
        if cmd is None or self.history is None:
            return
        self.history.push(cmd)
        self.changed.emit()

    def cancel_gesture(self, corners: bool = True) -> None:
        """Abort a stroke / drag in progress (before undo, crop and other document changes);
        ``corners`` also drops a polygon lasso's unfinished corners."""
        pending = corners and getattr(self.tool, "outline", None) is not None
        if (
            (self._pressed in ("left", "right") or pending)
            and self.tool is not None
            and self.doc is not None
        ):
            self._pressed = None
            self._after_change(self.tool.cancel(self.doc))
            self.update()

    def undo(self) -> None:
        self.cancel_gesture()
        if self.history and self.history.can_undo:
            self.history.undo()
            self.refresh()
            self.changed.emit()

    def redo(self) -> None:
        self.cancel_gesture()
        if self.history and self.history.can_redo:
            self.history.redo()
            self.refresh()
            self.changed.emit()

    def _after_change(self, rect: Rect | None) -> None:
        if rect is not None:
            self.refresh(rect)

    # ------------------------------------------------------------ geometry
    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        ix, iy = self.widget_to_image(anchor)
        self.zoom = zoom
        self._origin = QPointF(anchor.x() - ix * zoom, anchor.y() - iy * zoom)
        self._clamp()
        self.zoom_changed.emit(self.zoom)
        self.update()

    def fit(self) -> None:
        if self.doc is None:
            return
        margin = 24
        z = min(
            (self.width() - margin) / self.doc.width, (self.height() - margin) / self.doc.height
        )
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, z))
        self._center()
        self.zoom_changed.emit(self.zoom)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / 1.25)

    def pan_by(self, delta: QPointF) -> None:
        self._origin += delta
        self._clamp()
        self.update()

    def _center(self) -> None:
        if self.doc is None:
            return
        w, h = self.doc.width * self.zoom, self.doc.height * self.zoom
        self._origin = QPointF((self.width() - w) / 2, (self.height() - h) / 2)

    def _clamp(self) -> None:
        """Keep at least part of the image on screen; centre it when it (and the layers lying on
        the pasteboard around it) fit."""
        if self.doc is None:
            return
        z = self.zoom
        v = self._view if not self._view.empty else self.doc.bounds
        ox, oy = self._origin.x(), self._origin.y()
        keep = 40
        if v.width * z <= self.width():
            ox = (self.width() - self.doc.width * z) / 2
            # …unless that pushes pasteboard layers off screen
            ox = max(-v.x0 * z, min(self.width() - v.x1 * z, ox))
        else:
            ox = min(keep - v.x0 * z, max(self.width() - v.x1 * z - keep, ox))
        if v.height * z <= self.height():
            oy = (self.height() - self.doc.height * z) / 2
            oy = max(-v.y0 * z, min(self.height() - v.y1 * z, oy))
        else:
            oy = min(keep - v.y0 * z, max(self.height() - v.y1 * z - keep, oy))
        self._origin = QPointF(ox, oy)
        self.view_changed.emit()

    def widget_to_image(self, pos: QPointF) -> tuple[float, float]:
        return ((pos.x() - self._origin.x()) / self.zoom, (pos.y() - self._origin.y()) / self.zoom)

    def image_to_widget(self, x: float, y: float) -> QPoint:
        return QPoint(
            round(self._origin.x() + x * self.zoom), round(self._origin.y() + y * self.zoom)
        )

    def _image_rect(self, r: Rect) -> QRectF:
        return QRectF(
            self._origin.x() + r.x0 * self.zoom,
            self._origin.y() + r.y0 * self.zoom,
            r.width * self.zoom,
            r.height * self.zoom,
        )

    # ------------------------------------------------------------ painting
    def _tick(self) -> None:
        if self.doc is not None and (
            self.doc.selection is not None
            or self._crop_rect()
            or getattr(self.tool, "outline", None) is not None
        ):
            self._ants = (self._ants + 1) % 8
            self.update()

    def _crop_rect(self) -> Rect | None:
        return getattr(self.tool, "pending", None)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.palette()["bg"]))
        if self.doc is None or self._cache is None:
            p.end()
            return
        target = self._image_rect(self.doc.bounds)
        p.fillRect(target, self._checker)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1)
        view = self._image_rect(self._view)
        p.drawImage(view, self._cache)
        if self._view != self.doc.bounds:  # the pasteboard: shown faded, not part of the picture
            self._shade_outside(p, view, target, QColor(theme.palette()["bg"]), 150)
            p.setPen(QPen(QColor(128, 128, 128, 200), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self.tool is not None:
            prev = self.tool.preview(self.doc)
            if prev is not None:
                img, x, y = prev
                p.drawImage(
                    self._image_rect(Rect(x, y, x + img.width, y + img.height)), pil_to_qimage(img)
                )
        crop = self._crop_rect()
        if crop is not None:
            shade = QColor(0, 0, 0, 110)
            outer = target
            inner = self._image_rect(crop)
            p.fillRect(
                QRectF(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()), shade
            )
            p.fillRect(
                QRectF(
                    outer.left(), inner.bottom(), outer.width(), outer.bottom() - inner.bottom()
                ),
                shade,
            )
            p.fillRect(
                QRectF(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),
                shade,
            )
            p.fillRect(
                QRectF(inner.right(), inner.top(), outer.right() - inner.right(), inner.height()),
                shade,
            )
            self._ants_rect(p, inner)
        if self.doc.selection is not None:
            if self.doc.selection_mask is None:
                self._ants_rect(p, self._image_rect(self.doc.selection))
            else:
                self._ants_path(p, self._selection_path())
        self._paint_tool_outline(p)
        self._paint_loupe(p)
        if self._mouse is not None and self.tool is not None and self.tool.cursor == "brush":
            opts = self.context_provider().options if self.context_provider else None
            size = getattr(opts, "size", 0)
            if size:
                r = size * self.zoom / 2
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(self._mouse, r, r)
                p.setPen(QPen(QColor(255, 255, 255, 200), 1))
                p.drawEllipse(self._mouse, r + 1, r + 1)
        p.end()
        self._place_bar()

    def _paint_loupe(self, p: QPainter) -> None:
        """While the colour picker is held: a magnified view of the 11 × 11 pixels around the
        cursor (the one that will be picked outlined), up and to the left of it."""
        if (
            self.tool is None
            or self.tool.id != "picker"
            or self._pressed not in ("left", "right")
            or self._mouse is None
            or self._cache is None
        ):
            return
        n, cell = 11, 11
        size, half = n * cell, (n - 1) // 2
        ix, iy = (math.floor(v) for v in self.widget_to_image(self._mouse))
        cx, cy = self._mouse.x() - size / 2 - 24, self._mouse.y() - size / 2 - 24
        if cx - size / 2 < 4:  # keep it on screen
            cx = self._mouse.x() + size / 2 + 24
        if cy - size / 2 < 4:
            cy = self._mouse.y() + size / 2 + 24
        # a small window: keep it inside, even if it covers the cursor
        cx = min(max(cx, size / 2 + 4), max(size / 2 + 4, self.width() - size / 2 - 4))
        cy = min(max(cy, size / 2 + 4), max(size / 2 + 4, self.height() - size / 2 - 4))
        x0, y0 = cx - size / 2, cy - size / 2
        src = self._cache.copy(
            ix - half - self._view.x0, iy - half - self._view.y0, n, n
        )  # outside the cache comes back transparent
        circle = QPainterPath()
        circle.addEllipse(QPointF(cx, cy), size / 2, size / 2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipPath(circle)
        p.fillRect(QRectF(x0, y0, size, size), QColor(128, 128, 128))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QRectF(x0, y0, size, size), src)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        for k in range(1, n):
            p.drawLine(QPointF(x0 + k * cell, y0), QPointF(x0 + k * cell, y0 + size))
            p.drawLine(QPointF(x0, y0 + k * cell), QPointF(x0 + size, y0 + k * cell))
        p.restore()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("white"), 2))
        p.drawRect(QRectF(x0 + half * cell, y0 + half * cell, cell, cell))
        p.setPen(QPen(QColor("black"), 1))
        p.drawRect(QRectF(x0 + half * cell - 1.5, y0 + half * cell - 1.5, cell + 3, cell + 3))
        p.setPen(QPen(QColor(0, 0, 0, 140), 3))
        p.drawPath(circle)
        p.setPen(QPen(QColor("white"), 1.5))
        p.drawPath(circle)

    @staticmethod
    def _shade_outside(p: QPainter, outer: QRectF, inner: QRectF, color: QColor, alpha: int):
        """Cover ``outer`` minus ``inner`` with ``color`` at ``alpha``."""
        c = QColor(color)
        c.setAlpha(alpha)
        top = max(outer.top(), min(inner.top(), outer.bottom()))
        bottom = min(outer.bottom(), max(inner.bottom(), outer.top()))
        p.fillRect(QRectF(outer.left(), outer.top(), outer.width(), top - outer.top()), c)
        p.fillRect(QRectF(outer.left(), bottom, outer.width(), outer.bottom() - bottom), c)
        p.fillRect(QRectF(outer.left(), top, inner.left() - outer.left(), bottom - top), c)
        p.fillRect(QRectF(inner.right(), top, outer.right() - inner.right(), bottom - top), c)

    def bar_target(self) -> tuple[str, Rect] | None:
        """("crop" | "select", rectangle) the floating bar belongs to, or None to hide it."""
        if self.doc is None or self.tool is None or self._pressed in ("left", "right"):
            return None
        crop = self._crop_rect()
        if self.tool.id == "crop" and crop is not None:
            return "crop", crop
        if self.tool.id in SELECT_TOOLS and self.doc.selection is not None:
            if getattr(self.tool, "outline", None) is not None:
                return None  # a polygon still being drawn
            return "select", self.doc.selection
        return None

    def _place_bar(self) -> None:
        bar = self.bar
        if bar is None:
            return
        target = self.bar_target()
        if target is None:
            if bar.isVisible():
                bar.hide()
            return
        mode, rect = target
        bar.show_for(mode, rect.width, rect.height)
        r = self._image_rect(rect)
        box = (r.left(), r.top(), r.right(), r.bottom())
        corner = (True, True)
        if self._release_at is not None:
            at = self.image_to_widget(*self._release_at)
            corner = nearest_corner(box, at.x(), at.y())
        pos = QPoint(
            *place_bar(box, corner, (bar.width(), bar.height()), (self.width(), self.height()))
        )
        if bar.pos() != pos:
            bar.move(pos)

    def _selection_path(self) -> QPainterPath:
        """The selection's outline in canvas pixels (cached until the selection changes)."""
        key = (id(self.doc.selection_mask), self.doc.selection)
        if self._outline_key != key:
            path = QPainterPath()
            for x0, y0, x1, y1 in selection_outline(self.doc):
                path.moveTo(x0, y0)
                path.lineTo(x1, y1)
            self._outline_key, self._outline_path = key, path
        return self._outline_path

    def _ants_path(self, p: QPainter, path: QPainterPath) -> None:
        p.save()
        p.translate(self._origin)
        p.scale(self.zoom, self.zoom)
        p.setBrush(Qt.BrushStyle.NoBrush)
        white = QPen(QColor("white"), 1)
        white.setCosmetic(True)  # 1 screen pixel whatever the zoom
        p.setPen(white)
        p.drawPath(path)
        pen = QPen(QColor("black"), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        pen.setDashOffset(self._ants)
        p.setPen(pen)
        p.drawPath(path)
        p.restore()

    def _paint_tool_outline(self, p: QPainter) -> None:
        """The shape a selection tool is drawing (before the mouse is let go); the clone source."""
        handles = getattr(self.tool, "handles", None)
        if handles is not None and self.doc is not None and self._pressed is None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for hx, hy in handles(self.doc):  # a shape layer's corners / ends
                c = self.image_to_widget(hx, hy)
                p.setPen(QPen(QColor(0, 0, 0, 200), 1))
                p.setBrush(QColor("white"))
                p.drawRect(QRectF(c.x() - 4, c.y() - 4, 8, 8))
        marker = getattr(self.tool, "marker", None)
        if marker is not None and self._mouse is not None:
            pt = marker(*self.widget_to_image(self._mouse))
            if pt is not None:
                c = self.image_to_widget(*pt)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                for color, width in ((QColor(0, 0, 0, 170), 3), (QColor(255, 255, 255, 230), 1)):
                    p.setPen(QPen(color, width))
                    p.drawLine(c.x() - 8, c.y(), c.x() + 8, c.y())
                    p.drawLine(c.x(), c.y() - 8, c.x(), c.y() + 8)
        outline = getattr(self.tool, "outline", None)
        if outline is None:
            return
        kind, geo = outline
        path = QPainterPath()
        if kind == "rect":
            path.addRect(QRectF(geo.x0, geo.y0, geo.width, geo.height))
        elif kind == "ellipse":
            path.addEllipse(QRectF(geo.x0, geo.y0, geo.width, geo.height))
        elif geo:
            path.moveTo(*geo[0])
            for pt in geo[1:]:
                path.lineTo(*pt)
            if self.tool.id == "polygon_lasso" and self._mouse is not None:
                path.lineTo(*self.widget_to_image(self._mouse))  # where the next corner goes
        self._ants_path(p, path)

    def _ants_rect(self, p: QPainter, rect: QRectF) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("white"), 1))
        p.drawRect(rect)
        pen = QPen(QColor("black"), 1, Qt.PenStyle.DashLine)
        pen.setDashOffset(self._ants)
        p.setPen(pen)
        p.drawRect(rect)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._clamp()
        super().resizeEvent(event)

    # ------------------------------------------------------------ input
    def _ctx(self, button: str, modifiers) -> ToolContext:
        ctx = self.context_provider()
        ctx.button = button
        ctx.shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctx.alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        ctx.zoom = self.zoom
        return ctx

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.before_press is not None:
            self.before_press()  # e.g. finish text being typed before anything else changes
        if self.doc is None or self._pressed is not None:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.MiddleButton or (
            e.button() == Qt.MouseButton.LeftButton and self._space
        ):
            self._pressed, self._pan_from = "pan", pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        button = {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.RightButton: "right"}.get(
            e.button()
        )
        if button is None or self.tool is None or self.context_provider is None:
            return
        self._pressed = button
        x, y = self.widget_to_image(pos)
        if self.tool.id == "text":
            self.tool.press(self.doc, x, y, self._ctx(button, e.modifiers()))
            self._pressed = None
            self.text_requested.emit(x, y)
            return
        self._after_change(self.tool.press(self.doc, x, y, self._ctx(button, e.modifiers())))
        notice = getattr(self.tool, "notice", None)
        if notice:
            self.tool.notice = None
            self._pressed = None
            self.tool_notice.emit(notice)
        self._emit_pick()
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        pos = e.position()
        self._mouse = pos
        x, y = self.widget_to_image(pos)
        self.cursor_moved.emit(int(x), int(y))
        if self._pressed == "pan":
            self.pan_by(pos - self._pan_from)
            self._pan_from = pos
            return
        if self._pressed in ("left", "right") and self.tool is not None:
            self._after_change(
                self.tool.move(self.doc, x, y, self._ctx(self._pressed, e.modifiers()))
            )
            self._emit_pick()
        elif (
            self._pressed is None
            and not self._space  # the space bar's hand cursor wins
            and self.tool is not None
            and self.tool.id in SELECT_TOOLS
        ):
            # inside the selection a drag moves it: say so with the cursor
            inside = (
                self.doc is not None
                and not getattr(self.tool, "points", None)
                and not e.modifiers()
                & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
                and selection_contains(self.doc, x, y)
            )
            if inside:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self._restore_cursor()
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._pressed == "pan":
            self._pressed = None
            self._restore_cursor()
            return
        if self._pressed not in ("left", "right") or self.tool is None:
            return
        button, self._pressed = self._pressed, None
        x, y = self.widget_to_image(e.position())
        self._release_at = (x, y)
        cmd = self.tool.release(self.doc, x, y, self._ctx(button, e.modifiers()))
        if cmd is not None:
            self.push(cmd)
            self.refresh(cmd.repaint())
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        """Double-click finishes a polygon lasso."""
        if self.tool is not None and hasattr(self.tool, "finish") and self.doc is not None:
            self.finish_tool()
            return
        super().mouseDoubleClickEvent(e)

    def finish_tool(self) -> bool:
        """Finish a tool that collects clicks (polygon lasso); True if it had something."""
        finish = getattr(self.tool, "finish", None)
        if finish is None or self.doc is None or not getattr(self.tool, "points", None):
            return False
        self.push(finish(self.doc))
        self.update()
        return True

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._mouse = None
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: N802
        steps = e.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.zoom * (1.25**steps), e.position())

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        arrows = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if (
            e.key() in arrows
            and self.tool is not None
            and self.tool.id == "move"
            and self._pressed is None
        ):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx, dy = arrows[e.key()]
            self.nudge_requested.emit(dx * step, dy * step)
            return
        if (
            e.key() in arrows
            and self.tool is not None
            and self.tool.id in SELECT_TOOLS
            and self.doc is not None
            and self.doc.selection is not None
            and self._pressed is None
            and not getattr(self.tool, "points", None)  # not while a polygon is being drawn
            and not e.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
            and self.history is not None
        ):  # arrows move the selection outline (Shift: 10 px); a run of presses = one undo step
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx, dy = arrows[e.key()]
            if nudge_selection(self.doc, self.history, dx * step, dy * step):
                self.changed.emit()
            self.update()
            return
        if e.key() == Qt.Key.Key_Escape and self.tool is not None and self.doc is not None:
            self._pressed = None
            self._after_change(self.tool.cancel(self.doc))
            self.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._restore_cursor()
            return
        super().keyReleaseEvent(e)

    def _emit_pick(self) -> None:
        picked = getattr(self.tool, "picked", None)
        if picked is not None:
            self.color_picked.emit(tuple(picked), self.tool.picked_button)
