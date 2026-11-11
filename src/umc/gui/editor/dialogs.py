"""Small dialogs: resize, canvas size, quick fix / levels / curves, save options, shortcuts."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.editor import adjust, effects, filters, ops
from umc.editor.document import MAX_SIDE, Document
from umc.gui.editor.canvas import pil_to_qimage
from umc.gui.widgets.settings_panel import SettingsPanel

if TYPE_CHECKING:
    from umc.gui.editor.editor_page import EditorPage


def _buttons(dlg: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    return box


def _spin(lo: int, hi: int, value: int, suffix: str) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSuffix(suffix)
    return s


class _SizeDialog(QDialog):
    def __init__(self, doc: Document, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.doc = doc
        self._syncing = False
        self.form = QFormLayout()
        self.width = _spin(1, MAX_SIDE, doc.width, " px")
        self.height = _spin(1, MAX_SIDE, doc.height, " px")
        self.keep = QCheckBox("Keep proportions", checked=True)
        self.width.valueChanged.connect(lambda v: self._linked(v, True))
        self.height.valueChanged.connect(lambda v: self._linked(v, False))
        self.form.addRow("Width", self.width)
        self.form.addRow("Height", self.height)
        self.form.addRow("", self.keep)

    def _linked(self, value: int, from_width: bool) -> None:
        if self._syncing or not self.keep.isChecked():
            return
        self._syncing = True
        if from_width:
            self.height.setValue(max(1, round(value * self.doc.height / self.doc.width)))
        else:
            self.width.setValue(max(1, round(value * self.doc.width / self.doc.height)))
        self._syncing = False


class ResizeDialog(_SizeDialog):
    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(doc, "Resize image", parent)
        self.percent = _spin(1, 1000, 100, " %")
        self.percent.valueChanged.connect(self._from_percent)
        self.form.insertRow(0, "Scale", self.percent)
        self.resample = QComboBox()
        for value, label in (
            ("lanczos", "Smooth (best quality)"),
            ("bilinear", "Bilinear (faster)"),
            ("nearest", "Nearest neighbour (pixel art)"),
        ):
            self.resample.addItem(label, value)
        self.form.addRow("Resampling", self.resample)
        lay = QVBoxLayout(self)
        lay.addLayout(self.form)
        lay.addWidget(_buttons(self))

    def _from_percent(self, pct: int) -> None:
        self._syncing = True
        self.width.setValue(max(1, round(self.doc.width * pct / 100)))
        self.height.setValue(max(1, round(self.doc.height * pct / 100)))
        self._syncing = False

    def result_values(self) -> tuple[int, int, str]:
        return self.width.value(), self.height.value(), self.resample.currentData()


class CanvasSizeDialog(_SizeDialog):
    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(doc, "Canvas size", parent)
        self.keep.setChecked(False)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setSpacing(2)
        self.anchor = QButtonGroup(self)
        for i, name in enumerate(ops.ANCHORS):
            b = QRadioButton()
            b.setToolTip(name.replace("-", " "))
            self.anchor.addButton(b, i)
            grid.addWidget(b, i // 3, i % 3)
        self.anchor.button(4).setChecked(True)
        self.form.addRow("Anchor", grid_host)
        lay = QVBoxLayout(self)
        lay.addLayout(self.form)
        lay.addWidget(_buttons(self))

    def result_values(self) -> tuple[int, int, str]:
        return self.width.value(), self.height.value(), ops.ANCHORS[self.anchor.checkedId()]


class _AdjustDialog(QDialog):
    """Live preview on the active layer (inside the selection); nothing changes until OK.

    Subclasses build their controls and return what to apply from ``effect()`` (adjustment
    windows from ``adjustments()``); ``label`` is the undo step's name.
    """

    title = "Adjust"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        self.setWindowTitle(self.title)
        self.page = page
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self.update_preview)
        self.lay = QVBoxLayout(self)
        self.body = QVBoxLayout()
        self.lay.addLayout(self.body)
        row = QHBoxLayout()
        self.original_button = QPushButton("Hold to see the original", objectName="Small")
        self.original_button.pressed.connect(lambda: self.page.canvas.set_preview_layer(None, None))
        self.original_button.released.connect(self.update_preview)
        self.reset_button = QPushButton("Reset", objectName="Small")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.original_button)
        row.addStretch(1)
        row.addWidget(self.reset_button)
        self.extra_row = row
        self.lay.addLayout(row)
        self.lay.addWidget(_buttons(self))

    def changed(self) -> None:
        self._timer.start()

    def adjustments(self) -> adjust.Adjustments:
        raise NotImplementedError

    def effect(self) -> effects.Effect:
        return self.adjustments()

    def reset(self) -> None:
        raise NotImplementedError

    def update_preview(self) -> None:
        doc = self.page.doc
        if doc is None:
            return
        self.page._preview_owner = self  # the window changed last shows its preview
        eff = self.effect()
        if doc is None or eff.identity:
            self.page.canvas.set_preview_layer(None, None)
            return
        self.page.canvas.set_preview_layer(doc.active, effects.preview(doc, eff))

    def done(self, result: int) -> None:
        self._timer.stop()
        if self.page._preview_owner in (self, None):
            self.page.canvas.set_preview_layer(None, None)
            self.page._preview_owner = None
        super().done(result)


class _ValueSlider(QWidget):
    """A slider with its value shown next to it; ``scale`` turns slider steps into the value."""

    def __init__(self, lo: int, hi: int, on_change, scale: float = 1.0, fmt: str = "{:+g}") -> None:
        super().__init__()
        self.scale, self.fmt = scale, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setMinimumWidth(240)
        self.label = QLabel()
        self.label.setMinimumWidth(48)
        self.slider.valueChanged.connect(self._show)
        self.slider.valueChanged.connect(lambda _v: on_change())
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self._show(0)

    def _show(self, v: int) -> None:
        value = v * self.scale
        self.label.setText("0" if value == 0 else self.fmt.format(value))

    def value(self) -> float:
        return self.slider.value() * self.scale

    def set_value(self, value: float) -> None:
        self.slider.setValue(round(value / self.scale))


class QuickFixDialog(_AdjustDialog):
    """Exposure, brightness / contrast, temperature / tint, saturation, vibrance, Auto tone."""

    title = "Quick fix"
    FIELDS = (
        ("exposure", "Exposure", -300, 300, 0.01, "{:+.2f}"),
        ("brightness", "Brightness", -100, 100, 1, "{:+g}"),
        ("contrast", "Contrast", -100, 100, 1, "{:+g}"),
        ("temperature", "Temperature", -100, 100, 1, "{:+g}"),
        ("tint", "Tint", -100, 100, 1, "{:+g}"),
        ("saturation", "Saturation", -100, 100, 1, "{:+g}"),
        ("vibrance", "Vibrance", -100, 100, 1, "{:+g}"),
    )
    TIPS = {
        "exposure": "Brighter or darker, as if the photo had been taken that way (stops)",
        "temperature": "Cooler (blue) ↔ warmer (orange)",
        "tint": "Greener ↔ more magenta",
        "vibrance": "Boosts dull colours more than strong ones, and skin tones less",
    }

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        form = QFormLayout()
        self.sliders: dict[str, _ValueSlider] = {}
        for name, label, lo, hi, scale, fmt in self.FIELDS:
            w = _ValueSlider(lo, hi, self.changed, scale, fmt)
            w.setToolTip(self.TIPS.get(name, ""))
            self.sliders[name] = w
            form.addRow(label, w)
        self.body.addLayout(form)
        self.auto = QCheckBox("Auto tone (stretch a dark or flat photo)")
        self.auto.toggled.connect(self._on_auto)
        self.body.addWidget(self.auto)
        self._auto_levels: adjust.Levels | None = None

    def _on_auto(self, on: bool) -> None:
        img = adjust.region_image(self.page.doc) if on and self.page.doc else None
        self._auto_levels = adjust.auto_levels(img) if img is not None else None
        self.changed()

    def adjustments(self) -> adjust.Adjustments:
        v = {name: w.value() for name, w in self.sliders.items()}
        levels = {"rgb": self._auto_levels} if self._auto_levels is not None else {}
        return adjust.Adjustments(
            exposure=round(v["exposure"], 2),
            **{k: round(v[k]) for k in v if k != "exposure"},
            levels=levels,
        )

    def reset(self) -> None:
        for w in self.sliders.values():
            w.set_value(0)
        self.auto.setChecked(False)


class Histogram(QWidget):
    """Histogram with draggable black / mid / white markers (levels)."""

    def __init__(self, dialog: LevelsDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.counts = None
        self.setMinimumSize(280, 130)
        self._drag: str | None = None

    def _x(self, value: float) -> float:
        return 6 + (self.width() - 12) * value / 255

    def _value(self, x: float) -> float:
        return max(0.0, min(255.0, (x - 6) * 255 / max(1, self.width() - 12)))

    def markers(self) -> dict[str, float]:
        lv = self.dialog.current()
        mid = lv.black + (lv.white - lv.black) * 0.5**lv.gamma
        return {"black": lv.black, "mid": mid, "white": lv.white}

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPolygonF

        from umc.gui import theme

        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height() - 14
        p.fillRect(0, 0, self.width(), h, QColor(pal["surface_alt"]))
        if self.counts is not None and self.counts.max() > 0:
            top = float(np.sqrt(self.counts).max())
            p.setPen(QColor(pal["muted"]))
            for v, c in enumerate(self.counts):
                if c:
                    x = self._x(v)
                    p.drawLine(QPointF(x, h), QPointF(x, h - (h - 4) * np.sqrt(c) / top))
        for name, value in self.markers().items():
            x = self._x(value)
            fill = {"black": "#000000", "mid": "#808080", "white": "#ffffff"}[name]
            p.setPen(QColor(pal["text"]))
            p.setBrush(QColor(fill))
            p.drawPolygon(
                QPolygonF([QPointF(x, h + 1), QPointF(x - 6, h + 12), QPointF(x + 6, h + 12)])
            )
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        x = e.position().x()
        self._drag = min(self.markers().items(), key=lambda kv: abs(self._x(kv[1]) - x))[0]
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None:
            self.dialog.drag_marker(self._drag, self._value(e.position().x()))

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._drag = None


class LevelsDialog(_AdjustDialog):
    title = "Levels"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        self.levels: dict[str, adjust.Levels] = {}
        img = adjust.region_image(page.doc) if page.doc else None
        self.hist = (
            adjust.histogram(img)
            if img is not None
            else {c: np.zeros(256) for c in adjust.CHANNELS}
        )
        self._image = img
        top = QHBoxLayout()
        top.addWidget(QLabel("Channel"))
        self.channel = QComboBox()
        for ch, label in zip(adjust.CHANNELS, ("All colours", "Red", "Green", "Blue"), strict=True):
            self.channel.addItem(label, ch)
        self.channel.currentIndexChanged.connect(self._show_channel)
        top.addWidget(self.channel, 1)
        self.auto_button = QPushButton("Auto", objectName="Small")
        self.auto_button.setToolTip("Stretch the tones (ignores the darkest and brightest 0.5 %)")
        self.auto_button.clicked.connect(self._auto)
        top.addWidget(self.auto_button)
        self.body.addLayout(top)
        self.histogram = Histogram(self)
        self.body.addWidget(self.histogram)
        form = QFormLayout()
        self.black, self.white = QSpinBox(), QSpinBox()
        self.black.setRange(0, 254)
        self.white.setRange(1, 255)
        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(0.1, 9.99)
        self.gamma.setSingleStep(0.05)
        self.gamma.setDecimals(2)
        self.out_black, self.out_white = QSpinBox(), QSpinBox()
        self.out_black.setRange(0, 255)
        self.out_white.setRange(0, 255)
        inputs = QHBoxLayout()
        for label, w in (("Black", self.black), ("Mid-tones", self.gamma), ("White", self.white)):
            inputs.addWidget(QLabel(label))
            inputs.addWidget(w)
        outputs = QHBoxLayout()
        for label, w in (("Black", self.out_black), ("White", self.out_white)):
            outputs.addWidget(QLabel(label))
            outputs.addWidget(w)
        outputs.addStretch(1)
        form.addRow("Input", inputs)
        form.addRow("Output", outputs)
        self.body.addLayout(form)
        for w in (self.black, self.white, self.gamma, self.out_black, self.out_white):
            w.valueChanged.connect(self._read)
        self._loading = False
        self._show_channel()

    def channel_id(self) -> str:
        return self.channel.currentData()

    def current(self) -> adjust.Levels:
        return self.levels.get(self.channel_id(), adjust.Levels())

    def _show_channel(self) -> None:
        lv = self.current()
        self._loading = True
        self.black.setValue(lv.black)
        self.white.setValue(lv.white)
        self.gamma.setValue(lv.gamma)
        self.out_black.setValue(lv.out_black)
        self.out_white.setValue(lv.out_white)
        self._loading = False
        self.histogram.counts = self.hist[self.channel_id()]
        self.histogram.update()

    def _read(self) -> None:
        if self._loading:
            return
        black = min(self.black.value(), 254)
        white = max(self.white.value(), black + 1)
        if white != self.white.value():  # show what is applied: white stays above black
            self._loading = True
            self.white.setValue(white)
            self._loading = False
        self.levels[self.channel_id()] = adjust.Levels(
            black,
            white,
            round(self.gamma.value(), 2),
            self.out_black.value(),
            self.out_white.value(),
        )
        self.histogram.update()
        self.changed()

    def drag_marker(self, name: str, value: float) -> None:
        lv = self.current()
        if name == "black":
            self.black.setValue(round(min(value, lv.white - 1)))
        elif name == "white":
            self.white.setValue(round(max(value, lv.black + 1)))
        else:
            t = (value - lv.black) / max(1, lv.white - lv.black)
            t = min(0.99, max(0.01, t))
            self.gamma.setValue(round(math.log(t) / math.log(0.5), 2))

    def _auto(self) -> None:
        if self._image is not None:
            lv = adjust.auto_levels(self._image, self.channel_id())
            cur = self.current()
            self.levels[self.channel_id()] = replace(
                lv, gamma=cur.gamma, out_black=cur.out_black, out_white=cur.out_white
            )
            self._show_channel()
            self.changed()

    def adjustments(self) -> adjust.Adjustments:
        return adjust.Adjustments(levels={k: v for k, v in self.levels.items() if not v.identity})

    def reset(self) -> None:
        self.levels.clear()
        self._show_channel()
        self.changed()


class CurveEditor(QWidget):
    """Click to add a point, drag to move it, drag it out of the box to remove it."""

    PAD = 8
    GRAB = 9  # px: how close a click must be to grab a point
    REMOVE = 24  # px outside the box: drop the point

    def __init__(self, dialog: CurvesDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.setMinimumSize(280, 280)
        self.counts = None
        self._drag: int | None = None
        self._removing = False

    def _box(self):
        side = min(self.width(), self.height()) - 2 * self.PAD
        return self.PAD, self.PAD, max(1, side)

    def to_widget(self, x: float, y: float) -> QPointF:
        ox, oy, side = self._box()
        return QPointF(ox + side * x / 255, oy + side * (1 - y / 255))

    def to_curve(self, pos: QPointF) -> tuple[float, float]:
        ox, oy, side = self._box()
        return (pos.x() - ox) * 255 / side, (1 - (pos.y() - oy) / side) * 255

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

        from umc.gui import theme

        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ox, oy, side = self._box()
        p.fillRect(ox, oy, side, side, QColor(pal["surface_alt"]))
        if self.counts is not None and self.counts.max() > 0:
            top = float(np.sqrt(self.counts).max())
            p.setPen(QColor(pal["border"]))
            for v, c in enumerate(self.counts):
                if c:
                    x = ox + side * v / 255
                    p.drawLine(
                        QPointF(x, oy + side), QPointF(x, oy + side - side * 0.9 * np.sqrt(c) / top)
                    )
        p.setPen(QPen(QColor(pal["border"]), 1))
        for i in range(1, 4):
            p.drawLine(QPointF(ox + side * i / 4, oy), QPointF(ox + side * i / 4, oy + side))
            p.drawLine(QPointF(ox, oy + side * i / 4), QPointF(ox + side, oy + side * i / 4))
        p.drawLine(self.to_widget(0, 0), self.to_widget(255, 255))
        table = adjust.curve_table(self.dialog.points())
        path = QPainterPath(self.to_widget(0, table[0]))
        for x in range(1, 256):
            path.lineTo(self.to_widget(x, table[x]))
        p.setPen(QPen(QColor(pal["accent"]), 2))
        p.drawPath(path)
        p.setBrush(QColor(pal["surface"]))
        for i, (x, y) in enumerate(self.dialog.points()):
            p.setPen(
                QPen(
                    QColor(pal["danger"] if self._removing and i == self._drag else pal["accent"]),
                    2,
                )
            )
            p.drawEllipse(self.to_widget(x, y), 5, 5)
        p.end()

    def point_at(self, pos: QPointF) -> int | None:
        best = None
        for i, (x, y) in enumerate(self.dialog.points()):
            d = (self.to_widget(x, y) - pos).manhattanLength()
            if d <= self.GRAB * 1.5 and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        i = self.point_at(pos)
        ox, oy, side = self._box()
        if i is None and not (ox <= pos.x() <= ox + side and oy <= pos.y() <= oy + side):
            return  # beside the graph: don't move an end point there
        if i is None:
            x, y = self.to_curve(pos)
            i = self.dialog.add_point(x, y)
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is None:
            return
        pos = e.position()
        ox, oy, side = self._box()
        outside = (
            pos.x() < ox - self.REMOVE
            or pos.x() > ox + side + self.REMOVE
            or pos.y() < oy - self.REMOVE
            or pos.y() > oy + side + self.REMOVE
        )
        self._removing = outside and self.dialog.can_remove(self._drag)
        if not self._removing:
            self._drag = self.dialog.move_point(self._drag, *self.to_curve(pos))
        self.update()

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._drag is not None and self._removing:
            self.dialog.remove_point(self._drag)
        self._drag, self._removing = None, False
        self.update()


class CurvesDialog(_AdjustDialog):
    title = "Curves"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        self.curves: dict[str, list[tuple[int, int]]] = {}
        img = adjust.region_image(page.doc) if page.doc else None
        self.hist = adjust.histogram(img) if img is not None else None
        top = QHBoxLayout()
        top.addWidget(QLabel("Channel"))
        self.channel = QComboBox()
        for ch, label in zip(adjust.CHANNELS, ("All colours", "Red", "Green", "Blue"), strict=True):
            self.channel.addItem(label, ch)
        self.channel.currentIndexChanged.connect(self._show_channel)
        top.addWidget(self.channel, 1)
        self.body.addLayout(top)
        self.editor = CurveEditor(self)
        self.body.addWidget(self.editor, 1)
        hint = QLabel("Click to add a point · drag to move · drag off the graph to remove")
        hint.setObjectName("Muted")
        self.body.addWidget(hint)
        self._show_channel()

    def channel_id(self) -> str:
        return self.channel.currentData()

    def points(self) -> list[tuple[int, int]]:
        return self.curves.get(self.channel_id(), list(adjust.IDENTITY_CURVE))

    def _set(self, pts: list[tuple[int, int]]) -> None:
        self.curves[self.channel_id()] = pts
        self.editor.update()
        self.changed()

    def _show_channel(self) -> None:
        self.editor.counts = None if self.hist is None else self.hist[self.channel_id()]
        self.editor.update()

    @staticmethod
    def _clamp(v: float) -> int:
        return int(max(0, min(255, round(v))))

    def add_point(self, x: float, y: float) -> int:
        pts = list(self.points())
        x, y = self._clamp(x), self._clamp(y)
        pts = [p for p in pts if p[0] != x] + [(x, y)]
        pts.sort()
        self._set(pts)
        return pts.index((x, y))

    def move_point(self, i: int, x: float, y: float) -> int:
        """Move point ``i``; it can't pass its neighbours (end points keep to their side)."""
        pts = list(self.points())
        lo = pts[i - 1][0] + 1 if i > 0 else 0
        hi = pts[i + 1][0] - 1 if i < len(pts) - 1 else 255
        pts[i] = (int(max(lo, min(hi, round(x)))), self._clamp(y))
        self._set(pts)
        return i

    def can_remove(self, i: int) -> bool:
        """Any point but the two end points."""
        return 0 < i < len(self.points()) - 1

    def remove_point(self, i: int) -> None:
        if self.can_remove(i):
            pts = list(self.points())
            del pts[i]
            self._set(pts)

    def adjustments(self) -> adjust.Adjustments:
        return adjust.Adjustments(
            curves={
                k: tuple(v) for k, v in self.curves.items() if tuple(v) != adjust.IDENTITY_CURVE
            }
        )

    def reset(self) -> None:
        self.curves.clear()
        self.editor.update()
        self.changed()


class BlurDialog(_AdjustDialog):
    title = "Blur"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        form = QFormLayout()
        self.radius = _ValueSlider(0, 500, self.changed, 0.1, "{:.1f} px")
        self.radius.setToolTip("How far colours spread (px)")
        form.addRow("Radius", self.radius)
        self.body.addLayout(form)
        self.radius.set_value(2.0)

    def effect(self) -> effects.Effect:
        return filters.Blur(round(self.radius.value(), 1))

    def reset(self) -> None:
        self.radius.set_value(0)


class SharpenDialog(_AdjustDialog):
    title = "Sharpen"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        form = QFormLayout()
        self.amount = _ValueSlider(0, 500, self.changed, 1, "{:g} %")
        self.radius = _ValueSlider(1, 100, self.changed, 0.1, "{:.1f} px")
        self.threshold = _ValueSlider(0, 64, self.changed, 1, "{:g}")
        self.amount.setToolTip("How much stronger edges get")
        self.radius.setToolTip("How wide the edges are that get sharper (px)")
        self.threshold.setToolTip("Leave small differences alone (keeps skin and sky smooth)")
        form.addRow("Amount", self.amount)
        form.addRow("Radius", self.radius)
        form.addRow("Threshold", self.threshold)
        self.body.addLayout(form)
        self.amount.set_value(100)
        self.radius.set_value(1.0)

    def effect(self) -> effects.Effect:
        return filters.Sharpen(
            round(self.amount.value()), round(self.radius.value(), 1), round(self.threshold.value())
        )

    def reset(self) -> None:
        self.amount.set_value(0)
        self.radius.set_value(1.0)
        self.threshold.set_value(0)


class LooksDialog(_AdjustDialog):
    """One-click looks: a tile per look (a small preview of the picture), plus a strength."""

    title = "Looks"
    TILE = 92

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        img = effects.region_image(page.doc) if page.doc else None
        thumb = (img or Image.new("RGBA", (8, 8), (128, 128, 128, 255))).copy()
        thumb.thumbnail((self.TILE, self.TILE))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.tiles: dict[str, QToolButton] = {}
        for i, (name, (label, _steps)) in enumerate(filters.LOOKS.items()):
            b = QToolButton(objectName="LookTile")
            b.setCheckable(True)
            b.setText(label)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(self.TILE, self.TILE))
            shown = filters.Look(name)(thumb)
            b.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(shown))))
            b.toggled.connect(lambda _on: self.changed())
            self.group.addButton(b)
            self.tiles[name] = b
            grid.addWidget(b, i // 4, i % 4)
        self.body.addLayout(grid)
        form = QFormLayout()
        self.strength = _ValueSlider(0, 100, self.changed, 1, "{:g} %")
        self.strength.set_value(100)
        form.addRow("Strength", self.strength)
        self.body.addLayout(form)

    def chosen(self) -> str:
        return next((n for n, b in self.tiles.items() if b.isChecked()), "")

    def choose(self, name: str) -> None:
        self.tiles[name].setChecked(True)

    def effect(self) -> effects.Effect:
        return filters.Look(self.chosen(), round(self.strength.value()) / 100)

    def reset(self) -> None:
        self.group.setExclusive(False)
        for b in self.tiles.values():
            b.setChecked(False)
        self.group.setExclusive(True)
        self.strength.set_value(100)
        self.changed()


class StickersDialog(QDialog):
    """Pick a built-in sticker and its size; it becomes a new layer in the middle of the view."""

    TILE = 64

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        from umc.editor.stickers import STICKERS, render_sticker

        self.setWindowTitle("Stickers")
        front, back = page.swatches.primary, page.swatches.secondary
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.tiles: dict[str, QToolButton] = {}
        for i, (name, info) in enumerate(STICKERS.items()):
            b = QToolButton(objectName="LookTile")
            b.setCheckable(True)
            b.setText(info["label"])
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(self.TILE, self.TILE))
            img = render_sticker(name, self.TILE, front, back)
            b.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(img))))
            b.clicked.connect(lambda _c=False: self.ok.setEnabled(True))
            if self._uses_swatch(info):
                b.setToolTip("Drawn in your front colour")
            self.group.addButton(b)
            self.tiles[name] = b
            grid.addWidget(b, i // 4, i % 4)
        lay.addLayout(grid)
        form = QFormLayout()
        doc = page.doc
        side = min(doc.width, doc.height) if doc else 400
        self.size = _spin(8, 4000, max(16, side // 4), " px")
        form.addRow("Size", self.size)
        lay.addLayout(form)
        box = _buttons(self)
        self.ok = box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Add")
        self.ok.setEnabled(False)
        lay.addWidget(box)

    @staticmethod
    def _uses_swatch(info: dict) -> bool:
        return any(
            sh.get(k) in ("front", "back") for sh in info["shapes"] for k in ("fill", "stroke")
        )

    def chosen(self) -> str:
        return next((n for n, b in self.tiles.items() if b.isChecked()), "")


class FeatherDialog(QDialog):
    """Soften the selection's edge by a radius (px)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Feather selection")
        form = QFormLayout(self)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.5, 250)
        self.radius.setValue(5)
        self.radius.setSuffix(" px")
        form.addRow("Radius", self.radius)
        form.addRow(_buttons(self))


class BackgroundBlurDialog(_AdjustDialog):
    """Blur everything outside the selection (the subject stays sharp)."""

    title = "Background blur"

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        form = QFormLayout()
        self.radius = _ValueSlider(0, 500, self.changed, 0.1, "{:.1f} px")
        self.soften = _ValueSlider(0, 400, self.changed, 0.1, "{:.1f} px")
        self.radius.setToolTip("How blurred the background gets (px)")
        self.soften.setToolTip("How gradually the sharp subject turns into the blur (px)")
        form.addRow("Blur", self.radius)
        form.addRow("Soft edge", self.soften)
        hint = QLabel("Everything outside the selection is blurred — select the subject first.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.body.addLayout(form)
        self.body.addWidget(hint)
        self.radius.set_value(8)
        self.soften.set_value(4)

    def effect(self) -> effects.Effect:
        return filters.BackgroundBlur(round(self.radius.value(), 1), round(self.soften.value(), 1))

    def reset(self) -> None:
        self.radius.set_value(0)
        self.soften.set_value(4)


class SaveOptionsDialog(QDialog):
    def __init__(self, target_id: str, values: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        from umc.editor.save import save_options_cls

        self.setWindowTitle(f"{target_id.upper()} options")
        self.panel = SettingsPanel()
        self.panel.set_schema(save_options_cls(), target_id, values)
        lay = QVBoxLayout(self)
        lay.addWidget(self.panel)
        lay.addWidget(_buttons(self))

    def values(self) -> dict[str, Any]:
        return self.panel.values()


def tool_groups(tools) -> dict[str, list]:
    """Tools by shortcut letter, in tool-bar order (a letter shared by several cycles them)."""
    groups: dict[str, list] = {}
    for t in tools:
        if t.shortcut:
            groups.setdefault(t.shortcut, []).append(t)
    return groups


def shortcut_rows(tools) -> dict[str, list[tuple[str, str]]]:
    """(keys, what it does) per group, from the shortcut table, the tools and the INFO lines."""
    from PySide6.QtGui import QKeySequence

    from umc.gui.editor.shortcuts import GROUPS, INFO, SHORTCUTS

    rows: dict[str, list[tuple[str, str]]] = {g: [] for g in GROUPS}
    native = QKeySequence.SequenceFormat.NativeText
    for sc in SHORTCUTS:
        rows[sc.group].append((QKeySequence(sc.keys[0]).toString(native), sc.label))
    for letter, group in tool_groups(tools).items():  # one row per letter: "M  Rectangle / Ellipse"
        label = " / ".join(t.label for t in group)
        rows["Tools"].append(
            (letter, label + (" (press again to switch)" if len(group) > 1 else ""))
        )
    for keys, label, group in INFO:
        rows[group].append((keys, label))
    return rows


class ShortcutsDialog(QDialog):
    """The "?" window: every keyboard shortcut, grouped."""

    def __init__(self, tools, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        lay = QVBoxLayout(self)
        columns = QHBoxLayout()
        columns.setSpacing(28)
        lay.addLayout(columns)
        rows = shortcut_rows(tools)
        groups = list(rows)
        half = (len(groups) + 1) // 2
        for part in (groups[:half], groups[half:]):
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(4)
            r = 0
            for group in part:
                grid.addWidget(QLabel(group.upper(), objectName="Section"), r, 0, 1, 2)
                r += 1
                for keys, label in rows[group]:
                    key = QLabel(keys, objectName="ShortcutKey")
                    key.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                    grid.addWidget(key, r, 0, Qt.AlignmentFlag.AlignTop)
                    grid.addWidget(QLabel(label), r, 1)
                    r += 1
                grid.setRowMinimumHeight(r, 10)
                r += 1
            grid.setRowStretch(r, 1)
            columns.addLayout(grid)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.rows = rows
