"""Small dialogs: resize image, canvas size, brightness / contrast, save options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from umc.editor import ops
from umc.editor.document import MAX_SIDE, Document
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


class BrightnessContrastDialog(QDialog):
    """Live preview on the active layer; nothing changes until OK."""

    def __init__(self, page: EditorPage) -> None:
        super().__init__(page)
        self.setWindowTitle("Brightness / contrast")
        self.page = page
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self.update_preview)
        form = QFormLayout()
        self.brightness = self._slider()
        self.contrast = self._slider()
        form.addRow("Brightness", self._row(self.brightness))
        form.addRow("Contrast", self._row(self.contrast))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(_buttons(self))

    def _slider(self) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(-100, 100)
        s.setValue(0)
        s.setMinimumWidth(240)
        s.valueChanged.connect(lambda _v: self._timer.start())
        return s

    @staticmethod
    def _row(slider: QSlider) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        label = QLabel("0")
        label.setMinimumWidth(32)
        slider.valueChanged.connect(lambda v: label.setText(str(v)))
        h.addWidget(slider, 1)
        h.addWidget(label)
        return w

    def update_preview(self) -> None:
        doc = self.page.doc
        b, c = self.brightness.value(), self.contrast.value()
        if b == 0 and c == 0:
            self.page.canvas.set_preview_layer(None, None)
            return
        layer = doc.active_layer
        from umc.editor.tools.base import clip_rect

        region = clip_rect(doc, layer)
        img = layer.image.copy()
        if not region.empty:
            img.paste(ops.brightness_contrast(img.crop(region.box), b, c), (region.x0, region.y0))
        self.page.canvas.set_preview_layer(doc.active, img)

    def done(self, result: int) -> None:
        self._timer.stop()
        self.page.canvas.set_preview_layer(None, None)
        super().done(result)

    def result_values(self) -> tuple[int, int]:
        return self.brightness.value(), self.contrast.value()


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


def shortcut_rows(tools) -> dict[str, list[tuple[str, str]]]:
    """(keys, what it does) per group, from the shortcut table, the tools and the INFO lines."""
    from PySide6.QtGui import QKeySequence

    from umc.gui.editor.shortcuts import GROUPS, INFO, SHORTCUTS

    rows: dict[str, list[tuple[str, str]]] = {g: [] for g in GROUPS}
    native = QKeySequence.SequenceFormat.NativeText
    for sc in SHORTCUTS:
        rows[sc.group].append((QKeySequence(sc.keys[0]).toString(native), sc.label))
    for tool in tools:
        if tool.shortcut and tool.shortcut != "U":  # U (rectangle / ellipse) is a table row
            rows["Tools"].append((tool.shortcut, tool.label))
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
