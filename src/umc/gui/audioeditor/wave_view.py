"""The waveform: a time ruler on top, one lane per channel, the selection, cursor and play head.

Click = put the cursor, drag = select, Shift+click = extend the selection, wheel = scroll,
Ctrl+wheel = zoom around the pointer, double-click = select everything."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from umc.audioedit import peaks
from umc.audioedit.document import AudioDoc
from umc.audioedit.ops import format_time
from umc.gui import theme

RULER_H = 24
LANE_GAP = 6
MIN_FPP = 1 / 32  # most zoomed in: 32 px per sample
ZOOM_STEP = 2.0
TICK_STEPS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120,
              300, 600, 900, 1800, 3600)  # fmt: skip


class WaveView(QWidget):
    selection_changed = Signal()
    view_changed = Signal()  # scrolled or zoomed (the page updates its scroll bar)
    seek_requested = Signal(int)  # a click while playing: play from there

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("WaveView")
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.doc: AudioDoc | None = None
        self.start = 0.0  # frame at the left edge
        self.fpp = 1.0  # frames per pixel
        self.play_head: int | None = None
        self.playing = False
        self._peaks: peaks.Peaks | None = None
        self._peaks_rev = -1
        self._anchor: int | None = None  # where the drag started (frames)

    # ------------------------------------------------------------ model
    def set_document(self, doc: AudioDoc | None) -> None:
        self.doc = doc
        self._peaks, self._peaks_rev, self.play_head = None, -1, None
        self.fit()

    def _summary(self) -> peaks.Peaks:
        if self._peaks is None or self._peaks_rev != self.doc.revision:
            self._peaks = peaks.build(self.doc.samples)
            self._peaks_rev = self.doc.revision
        return self._peaks

    # ------------------------------------------------------------ view
    @property
    def frames(self) -> int:
        return self.doc.frames if self.doc is not None else 0

    def max_fpp(self) -> float:
        return max(MIN_FPP, self.frames / max(1, self.width()))

    def visible_frames(self) -> float:
        return self.fpp * max(1, self.width())

    def _clamp(self) -> None:
        self.fpp = max(MIN_FPP, min(self.fpp, self.max_fpp()))
        self.start = max(0.0, min(self.start, self.frames - self.visible_frames()))

    def fit(self) -> None:
        self.fpp, self.start = self.max_fpp(), 0.0
        self._changed()

    def zoom(self, factor: float, at_x: float | None = None) -> None:
        """``factor`` > 1 zooms in, around ``at_x`` (default: the cursor, else the middle)."""
        if self.doc is None:
            return
        if at_x is None:
            cur = self.doc.selection[0]
            at_x = self.x_of(cur) if 0 <= self.x_of(cur) <= self.width() else self.width() / 2
        frame = self.start + at_x * self.fpp
        self.fpp /= factor
        self._clamp()
        self.start = frame - at_x * self.fpp
        self._changed()

    def zoom_to(self, a: int, b: int) -> None:
        if b <= a:
            return
        self.fpp = (b - a) / max(1, self.width()) * 1.05
        self._clamp()
        self.start = (a + b) / 2 - self.visible_frames() / 2
        self._changed()

    def set_start(self, frame: float) -> None:
        self.start = frame
        self._changed()

    def ensure_visible(self, frame: int) -> None:
        """Playback follows the play head a page at a time, like Audacity."""
        if frame < self.start or frame > self.start + self.visible_frames():
            self.set_start(frame - self.visible_frames() * 0.05)

    def _changed(self) -> None:
        self._clamp()
        self.update()
        self.view_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        was_fit = self.fpp >= self.frames / max(1, event.oldSize().width()) - 1e-9
        super().resizeEvent(event)
        if was_fit:
            self.fpp = self.max_fpp()
        self._changed()

    def x_of(self, frame: float) -> float:
        return (frame - self.start) / self.fpp

    def frame_at(self, x: float) -> int:
        return max(0, min(self.frames, int(round(self.start + x * self.fpp))))

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.doc is None or event.button() != Qt.MouseButton.LeftButton:
            return
        f = self.frame_at(event.position().x())
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            a, b = self.doc.selection
            self._anchor = b if abs(f - a) < abs(f - b) else a
            self.doc.set_selection(self._anchor, f)
        else:
            self._anchor = f
            self.doc.set_selection(f)
            if self.playing:
                self.seek_requested.emit(f)
        self.update()
        self.selection_changed.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.doc is None:
            return
        if self._anchor is None:
            self.setCursor(Qt.CursorShape.IBeamCursor)
            return
        x = event.position().x()
        if x < 0 or x > self.width():  # dragging past an edge scrolls
            self.set_start(self.start + (x if x < 0 else x - self.width()) * self.fpp)
        self.doc.set_selection(self._anchor, self.frame_at(x))
        self.update()
        self.selection_changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._anchor = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.doc is not None:
            self.doc.select_all()
            self.update()
            self.selection_changed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.doc is None:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(ZOOM_STEP ** (delta / 240), event.position().x())
        else:
            self.set_start(self.start - delta / 120 * self.visible_frames() / 10)
        event.accept()

    # ------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.palette()
        painter = QPainter(self)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(p["surface"]))
        if self.doc is None or self.frames == 0:
            return
        doc = self.doc
        lanes_top = RULER_H
        lane_h = (h - lanes_top - LANE_GAP * (doc.channels - 1)) / doc.channels
        a, b = doc.selection
        sel_x0, sel_x1 = self.x_of(a), self.x_of(b)
        end_x = min(w, self.x_of(self.frames))

        for c in range(doc.channels):
            top = lanes_top + c * (lane_h + LANE_GAP)
            painter.fillRect(QRectF(0, top, end_x, lane_h), QColor(p["surface_alt"]))
            if b > a:
                sel = QColor(p["accent"])
                sel.setAlpha(45)
                painter.fillRect(QRectF(sel_x0, top, sel_x1 - sel_x0, lane_h), sel)
        self._paint_waves(painter, lanes_top, lane_h, p)
        self._paint_ruler(painter, w, p)

        # cursor / selection edges, then the play head
        pen = QPen(QColor(p["text"]))
        pen.setWidthF(1)
        painter.setPen(pen)
        for x in {sel_x0, sel_x1}:
            if 0 <= x <= w:
                painter.drawLine(QLineF(x, lanes_top, x, h))
        if self.play_head is not None:
            x = self.x_of(self.play_head)
            if 0 <= x <= w:
                pen = QPen(QColor(p["success"]))
                pen.setWidthF(2)
                painter.setPen(pen)
                painter.drawLine(QLineF(x, 0, x, h))

    def _paint_waves(self, painter: QPainter, top0: float, lane_h: float, p) -> None:
        doc = self.doc
        mins, maxs = peaks.columns(doc.samples, self._summary(), self.start, self.fpp, self.width())
        k = mins.shape[1]
        if k == 0:
            return
        color = QColor(p["accent"])
        for c in range(doc.channels):
            top = top0 + c * (lane_h + LANE_GAP)
            mid, half = top + lane_h / 2, lane_h / 2 * 0.95
            painter.setPen(QPen(QColor(p["border_strong"]), 1))
            painter.drawLine(QLineF(0, mid, self.x_of(self.frames), mid))
            lo = np.clip(mins[c], -1, 1)
            hi = np.clip(maxs[c], -1, 1)
            pen = QPen(color)
            pen.setWidthF(1)
            painter.setPen(pen)
            if self.fpp < 1:  # zoomed in to single samples: join the dots
                xs = np.arange(k, dtype=np.float64)
                ys = mid - hi * half
                painter.drawPolyline([_pt(x, y) for x, y in zip(xs, ys, strict=True)])
                if self.fpp < 1 / 8:  # and show the samples themselves
                    for i in range(1, k):
                        if math.floor(self.start + i * self.fpp) != math.floor(
                            self.start + (i - 1) * self.fpp
                        ):
                            painter.drawEllipse(_pt(i, ys[i]), 2, 2)
                continue
            y0 = mid - hi * half
            y1 = np.maximum(mid - lo * half, y0 + 1)
            painter.drawLines([QLineF(i + 0.5, y0[i], i + 0.5, y1[i]) for i in range(k)])

    def _paint_ruler(self, painter: QPainter, w: int, p) -> None:
        painter.fillRect(QRectF(0, 0, w, RULER_H), QColor(p["surface"]))
        painter.setPen(QPen(QColor(p["border"]), 1))
        painter.drawLine(QLineF(0, RULER_H - 0.5, w, RULER_H - 0.5))
        rate = self.doc.rate
        sec_per_px = self.fpp / rate
        step = next((s for s in TICK_STEPS if s / sec_per_px >= 90), TICK_STEPS[-1])
        t0 = self.start / rate
        t = math.floor(t0 / step) * step
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        muted = QColor(p["muted"])
        while True:
            x = (t - t0) / sec_per_px
            if x > w:
                break
            if x >= -60:
                painter.setPen(QPen(muted, 1))
                painter.drawLine(QLineF(x, RULER_H - 8, x, RULER_H - 1))
                for i in range(1, 5):  # minor ticks
                    mx = x + i * step / 5 / sec_per_px
                    painter.drawLine(QLineF(mx, RULER_H - 4, mx, RULER_H - 1))
                label = format_time(t)
                if step >= 1:
                    label = label.rsplit(".", 1)[0]
                painter.drawText(QRectF(x + 3, 2, 120, RULER_H - 8), label)
            t += step


def _pt(x: float, y: float) -> QPointF:
    return QPointF(float(x), float(y))
