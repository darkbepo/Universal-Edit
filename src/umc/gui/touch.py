"""Touch screens and narrow windows (Linux tablets, Windows 2-in-1s).

* ``enabled()`` — whether controls should be finger-sized. QSettings ``appearance/touch``:
  "auto" (a touch screen is connected), "on" or "off"; ``UMC_TOUCH=1/0`` overrides.
* ``stylesheet()`` — extra rules added after ``style.qss`` while touch is on (taller buttons,
  boxes and rows, wider scroll bars).
* ``install(app)`` — finger scrolling (QScroller) in every list, table, text box and scroll
  area; a mouse still selects / drags as before (the scroller only takes touch).
* ``PinchZoom`` — two-finger pinch + pan on a canvas.
* ``Foldable`` — side panels that fold away when the page gets narrow, with a button that
  shows them again.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QPointF, QSettings, Qt, Signal
from PySide6.QtGui import QInputDevice
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QPinchGesture,
    QPushButton,
    QScroller,
    QScrollerProperties,
    QWidget,
)

SETTING = "appearance/touch"
CHOICES = ("auto", "on", "off")
LABELS = {"auto": "When there's a touch screen", "on": "Always", "off": "Never"}
# Pages narrower than this fold their side panels away: in touch mode a 1280 px tablet gives the
# canvas the whole width and shows the panels on demand; with a mouse only a really narrow window
# folds (a 1366 px laptop keeps its panels).
FOLD_BELOW = 1400
FOLD_BELOW_MOUSE = 1000


class _Notifier(QObject):
    changed = Signal()  # touch mode switched on or off


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def has_touch_screen() -> bool:
    try:
        devices = QInputDevice.devices()
    except Exception:  # no QGuiApplication yet / very old Qt
        return False
    return any(d.type() == QInputDevice.DeviceType.TouchScreen for d in devices)


def choice() -> str:
    value = QSettings().value(SETTING, "auto", str)
    return value if value in CHOICES else "auto"


def set_choice(value: str) -> None:
    if value not in CHOICES:
        value = "auto"
    before = enabled()
    settings = QSettings()
    settings.setValue(SETTING, value)
    settings.sync()
    if enabled() != before:
        notifier().changed.emit()


def enabled() -> bool:
    env = os.environ.get("UMC_TOUCH", "").strip()
    if env in ("1", "0"):
        return env == "1"
    value = choice()
    if value != "auto":
        return value == "on"
    return has_touch_screen()


def stylesheet() -> str:
    """Finger-sized controls (~40 px targets). Appended after style.qss, so it wins."""
    if not enabled():
        return ""
    return """
QPushButton, QToolButton { min-height: 34px; padding-left: 12px; padding-right: 12px; }
QPushButton#Link { min-height: 30px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QFontComboBox {
    min-height: 34px;
}
QCheckBox::indicator, QRadioButton::indicator { width: 22px; height: 22px; }
QCheckBox, QRadioButton { min-height: 34px; spacing: 10px; }
QScrollBar:vertical { width: 16px; }
QScrollBar:horizontal { height: 16px; }
QScrollBar::handle:vertical { min-height: 48px; }
QScrollBar::handle:horizontal { min-width: 48px; }
QSlider { min-height: 34px; }
QTabBar::tab { min-height: 34px; padding-left: 14px; padding-right: 14px; }
QMenu::item { padding: 10px 28px 10px 18px; }
QHeaderView::section { min-height: 30px; }
"""


# ---------------------------------------------------------------- finger scrolling
_SCROLLED = "_umc_touch_scroller"


def _wants_scroller(area: QAbstractScrollArea) -> bool:
    if area.property(_SCROLLED):
        return False
    # a list the user reorders by dragging keeps its drag (the layers list, the media list)
    if isinstance(area, QAbstractItemView) and (
        area.dragDropMode() != QAbstractItemView.DragDropMode.NoDragDrop
    ):
        return False
    return not area.property("umc_no_touch_scroll")


def add_scroller(area: QAbstractScrollArea) -> None:
    """One-finger flick scrolling in ``area`` (touch only: the mouse is untouched)."""
    if not _wants_scroller(area):
        return
    area.setProperty(_SCROLLED, True)
    viewport = area.viewport()
    QScroller.grabGesture(viewport, QScroller.ScrollerGestureType.TouchGesture)
    scroller = QScroller.scroller(viewport)
    props = scroller.scrollerProperties()
    off = QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
    props.setScrollMetric(QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy, off)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.VerticalOvershootPolicy, off)
    props.setScrollMetric(QScrollerProperties.ScrollMetric.DragStartDistance, 0.004)
    scroller.setScrollerProperties(props)
    if isinstance(area, QAbstractItemView):
        area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        area.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)


class _ScrollerInstaller(QObject):
    """Gives every scroll area finger scrolling the first time it is shown."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(obj, QAbstractScrollArea):
            add_scroller(obj)
        return False


_installer: _ScrollerInstaller | None = None


def install(app: QApplication) -> None:
    global _installer
    if _installer is None:
        _installer = _ScrollerInstaller(app)
        app.installEventFilter(_installer)


# ---------------------------------------------------------------- pinch
class PinchZoom(QObject):
    """Two fingers on ``widget``: pinch zooms around their middle, moving them pans.

    ``on_start()`` runs when the second finger lands (drop a stroke the first finger began),
    ``on_zoom(factor, centre)`` with the change since the last step (> 1 = bigger) and the
    centre in widget coordinates, ``on_pan(delta)`` with the move of that centre."""

    def __init__(
        self,
        widget: QWidget,
        on_zoom: Callable[[float, QPointF], None],
        on_pan: Callable[[QPointF], None] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(widget)
        self.widget = widget
        self.on_zoom, self.on_pan, self.on_start = on_zoom, on_pan, on_start
        self._last: QPointF | None = None
        widget.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        widget.grabGesture(Qt.GestureType.PinchGesture)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.widget and event.type() == QEvent.Type.Gesture:
            g = event.gesture(Qt.GestureType.PinchGesture)
            if isinstance(g, QPinchGesture):
                self.handle(g.state(), g.scaleFactor(), g.centerPoint())
                event.accept(g)
                return True
        return False

    def handle(self, state, scale: float, centre_global: QPointF) -> None:
        """One pinch step (split out so tests can drive it without a touch screen)."""
        centre = self.widget.mapFromGlobal(centre_global)
        if state == Qt.GestureState.GestureStarted:
            self._last = centre
            if self.on_start:
                self.on_start()
            return
        if state in (Qt.GestureState.GestureFinished, Qt.GestureState.GestureCanceled):
            self._last = None
            return
        if scale and abs(scale - 1.0) > 1e-4:
            self.on_zoom(scale, centre)
        if self.on_pan and self._last is not None:
            delta = centre - self._last
            if delta.manhattanLength() > 0:
                self.on_pan(delta)
        self._last = centre


# ---------------------------------------------------------------- folding side panels
class Foldable(QObject):
    """Side panels of an editor page that fold away while ``page`` is narrower than
    ``below`` px (default: ``FOLD_BELOW`` in touch mode, else ``FOLD_BELOW_MOUSE``);
    ``button`` (put it in the top bar) shows / hides them then. On a wide page the button is
    hidden and the panels always show. ``on_fold(narrow)`` runs on each switch
    (e.g. to narrow a tool rail)."""

    def __init__(
        self,
        page: QWidget,
        panels: Sequence[QWidget],
        button: QPushButton,
        below: int | None = None,
        on_fold: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(page)
        self.page, self.panels, self.button = page, list(panels), button
        self.below, self.on_fold = below, on_fold
        self.narrow: bool | None = None
        button.setCheckable(True)
        button.setChecked(False)
        button.toggled.connect(self._toggled)
        button.hide()
        page.installEventFilter(self)
        notifier().changed.connect(self.update)

    def threshold(self) -> int:
        if self.below is not None:
            return self.below
        return FOLD_BELOW if enabled() else FOLD_BELOW_MOUSE

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.page and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update()
        return False

    def update(self) -> None:
        narrow = self.page.width() < self.threshold()
        if narrow == self.narrow:
            return
        self.narrow = narrow
        self.button.setVisible(narrow)
        self.button.blockSignals(True)
        self.button.setChecked(False)
        self.button.blockSignals(False)
        for p in self.panels:
            p.setVisible(not narrow)
        if self.on_fold:
            self.on_fold(narrow)

    def _toggled(self, shown: bool) -> None:
        if self.narrow:
            for p in self.panels:
                p.setVisible(shown)
