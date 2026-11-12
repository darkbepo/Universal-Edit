"""The file-type badges orbiting the empty drop zone (desktop drawing of ``core/orbit.py``).

``Orbit`` belongs to a widget and paints into it (call ``paint`` from its ``paintEvent``, before
child widgets draw the text). A 60 fps timer runs only while the widget is visible, active and
motion isn't reduced (Windows' "Show animations" off, or ``UMC_REDUCE_MOTION=1``)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from umc.core import orbit as model
from umc.gui import icons

DIM = 0.62  # "lively but dimmed": the brightest badge is this opaque (same as the web)
BADGE_H = 30


def reduce_motion() -> bool:
    """The user asked for less motion (Windows: Settings → Accessibility → Animation effects)."""
    env = os.environ.get("UMC_REDUCE_MOTION")
    if env is not None:
        return env == "1"
    if sys.platform == "win32":
        try:
            import ctypes

            on = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):
                return not on.value
        except Exception:
            return False
    return False


def render_badge(icon: str, label: str, colour: str, dpr: float) -> QPixmap:
    """Icon + label in a soft pill of the badge's colour, drawn once at the screen's density."""
    font = QFont()
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.DemiBold)
    w = 10 + 16 + 6 + QFontMetricsF(font).horizontalAdvance(label) + 12
    pm = QPixmap(int(w * dpr + 1), int(BADGE_H * dpr + 1))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QColor(colour)
    fill, edge = QColor(base), QColor(base)
    fill.setAlpha(0x26)
    edge.setAlpha(0x66)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1, BADGE_H - 1), BADGE_H / 2, BADGE_H / 2)
    p.fillPath(path, fill)
    p.setPen(edge)
    p.drawPath(path)
    p.drawPixmap(QRectF(10, (BADGE_H - 16) / 2, 16, 16).toRect(), icons.pixmap(icon, 16, colour))
    p.setFont(font)
    p.setPen(base)
    p.drawText(QRectF(32, 0, w - 32, BADGE_H), Qt.AlignmentFlag.AlignVCenter, label)
    p.end()
    return pm


class Orbit(QObject):
    def __init__(self, widget: QWidget, text: Callable[[], list[QWidget]]) -> None:
        super().__init__(widget)
        self.widget = widget
        self.text = text  # the widgets whose area the badges keep clear of
        self.tau = 0.0
        self.pull = 0.0
        self.pull_target = 0.0
        self.burst_start: float | None = None
        self.active = True
        self.still = reduce_motion()
        self._badges: list[QPixmap] = []
        self._dpr = 0.0
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ state
    @property
    def moving(self) -> bool:
        w = self.widget
        return self.active and not self.still and w.isVisible() and not w.window().isMinimized()

    def set_active(self, on: bool) -> None:
        self.active = on
        if not on:
            self.burst_start = None
        self.update_timer()
        self.widget.update()

    def set_pull(self, on: bool) -> None:
        self.pull_target = 1.0 if on else 0.0

    def burst_then(self, done: Callable[[], None]) -> None:
        """The drop burst, then ``done`` (at once when nothing moves)."""
        if not self.moving:
            done()
            return
        self.burst_start = self._now()

        def finished() -> None:
            self.burst_start = None  # back in orbit if the drop added nothing
            done()

        QTimer.singleShot(model.BURST_MS, self.widget, finished)

    def update_timer(self) -> None:
        """Call from the widget's show / hide events and the window's state changes too."""
        if self.moving and not self.timer.isActive():
            self._last = self._now()
            self.timer.start()
        elif not self.moving:
            self.timer.stop()

    def _now(self) -> float:
        return self._clock.elapsed() / 1000

    def _tick(self) -> None:
        if not self.moving:
            self.timer.stop()
            return
        now = self._now()
        dt = min(now - self._last, 0.05)
        self._last = now
        self.pull += (self.pull_target - self.pull) * min(1.0, dt * model.PULL_EASE)
        self.tau += dt * model.speed(self.pull)
        self.widget.update()

    def burst(self) -> float:
        if self.burst_start is None:
            return 0.0
        return min((self._now() - self.burst_start) * 1000 / model.BURST_MS, 1.0)

    # ------------------------------------------------------------------ drawing
    def _ensure_badges(self) -> None:
        dpr = self.widget.devicePixelRatioF()
        if self._badges and dpr == self._dpr:
            return
        self._dpr = dpr
        self._badges = [render_badge(i, label, c, dpr) for i, label, c in model.BADGES]

    def spots(self) -> list[model.Spot]:
        w, h = self.widget.width(), self.widget.height()
        cx, cy = w / 2, h / 2
        tw = th = 0.0
        for t in self.text():
            if not t.isVisible():
                continue
            g = t.geometry()
            tw = max(tw, 2 * max(abs(g.left() - cx), abs(g.right() + 1 - cx)))
            th = max(th, 2 * max(abs(g.top() - cy), abs(g.bottom() + 1 - cy)))
        return model.layout(self.tau, w, h, tw, th, self.pull, self.burst())

    def paint(self, p: QPainter) -> None:
        if not self.active:
            return
        self._ensure_badges()
        w, h = self.widget.width(), self.widget.height()
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        order = sorted(enumerate(self.spots()), key=lambda e: e[1].z)  # far side first
        for i, s in order:
            if s.alpha <= 0.01:
                continue
            pm = self._badges[i]
            bw = pm.width() / pm.devicePixelRatio() * s.scale
            bh = pm.height() / pm.devicePixelRatio() * s.scale
            p.setOpacity(s.alpha * DIM)
            p.drawPixmap(QRectF(w / 2 + s.x - bw / 2, h / 2 + s.y - bh / 2, bw, bh), pm, pm.rect())
        p.restore()
