"""Layers list (top layer first): thumbnail, name, visibility; opacity and layer buttons."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from umc.editor import ops
from umc.editor.document import Document
from umc.gui.editor.canvas import pil_to_qimage

THUMB = 40


def thumbnail(img: Image.Image) -> QIcon:
    t = img.copy()
    t.thumbnail((THUMB, THUMB))
    bg = Image.new("RGBA", (THUMB, THUMB), (255, 255, 255, 255))
    for x in range(0, THUMB, 8):  # checkerboard
        for y in range(0, THUMB, 8):
            if (x // 8 + y // 8) % 2:
                bg.paste((215, 215, 215, 255), (x, y, x + 8, y + 8))
    bg.alpha_composite(t, ((THUMB - t.width) // 2, (THUMB - t.height) // 2))
    return QIcon(QPixmap.fromImage(pil_to_qimage(bg)))


class LayersPanel(QWidget):
    command = Signal(object)  # a Command produced by ops.* (already applied)
    active_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc: Document | None = None
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(QLabel("LAYERS", objectName="Section"))

        self.list = QListWidget(objectName="LayerList")
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.list.setToolTip("Double-click a name to rename · tick to show / hide")
        self.list.currentRowChanged.connect(self._on_row)
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list, 1)

        op = QHBoxLayout()
        op.addWidget(QLabel("Opacity"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.sliderReleased.connect(self._on_opacity)
        self.opacity.valueChanged.connect(self._on_opacity_value)
        self.opacity_label = QLabel("100 %")
        self.opacity_label.setMinimumWidth(42)
        op.addWidget(self.opacity, 1)
        op.addWidget(self.opacity_label)
        lay.addLayout(op)

        grid = QGridLayout()
        grid.setSpacing(6)

        def button(text: str, tip: str, fn) -> QPushButton:
            b = QPushButton(text, objectName="Small")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            return b

        self.add_button = button("Add", "New empty layer", lambda: self._run(ops.add_layer))
        self.duplicate_button = button(
            "Duplicate", "Copy the selected layer", lambda: self._run(ops.duplicate_layer)
        )
        self.delete_button = button(
            "Delete", "Delete the selected layer", lambda: self._run(ops.delete_layer)
        )
        self.merge_button = button(
            "Merge down", "Merge into the layer below", lambda: self._run(ops.merge_down)
        )
        self.up_button = button("Up", "Move up", lambda: self._run(lambda d: ops.move_layer(d, 1)))
        self.down_button = button(
            "Down", "Move down", lambda: self._run(lambda d: ops.move_layer(d, -1))
        )
        for i, b in enumerate(
            (
                self.add_button,
                self.duplicate_button,
                self.delete_button,
                self.merge_button,
                self.up_button,
                self.down_button,
            )
        ):
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

    # ------------------------------------------------------------ state
    def _row_to_index(self, row: int) -> int:
        return len(self.doc.layers) - 1 - row

    def refresh(self, doc: Document) -> None:
        self.doc = doc
        self._updating = True
        self.list.clear()
        for layer in reversed(doc.layers):
            item = QListWidgetItem(thumbnail(layer.image), layer.name)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.setCurrentRow(len(doc.layers) - 1 - doc.active)
        self._sync_controls()
        self._updating = False

    def refresh_thumbnails(self) -> None:
        if self.doc is None or self.list.count() != len(self.doc.layers):
            return
        for row in range(self.list.count()):
            layer = self.doc.layers[self._row_to_index(row)]
            self.list.item(row).setIcon(thumbnail(layer.image))

    def _sync_controls(self) -> None:
        doc = self.doc
        layer = doc.active_layer
        self.opacity.blockSignals(True)
        self.opacity.setValue(round(layer.opacity * 100))
        self.opacity.blockSignals(False)
        self.opacity_label.setText(f"{round(layer.opacity * 100)} %")
        n = len(doc.layers)
        self.delete_button.setEnabled(n > 1)
        self.merge_button.setEnabled(doc.active > 0)
        self.up_button.setEnabled(doc.active < n - 1)
        self.down_button.setEnabled(doc.active > 0)

    # ------------------------------------------------------------ events
    def _run(self, fn) -> None:
        if self.doc is None:
            return
        cmd = fn(self.doc)
        if cmd is not None:
            self.command.emit(cmd)

    def _on_row(self, row: int) -> None:
        if self._updating or self.doc is None or row < 0:
            return
        ops.select_layer(self.doc, self._row_to_index(row))
        self._sync_controls()
        self.active_changed.emit(self.doc.active)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating or self.doc is None:
            return
        index = self._row_to_index(self.list.row(item))
        layer = self.doc.layers[index]
        visible = item.checkState() == Qt.CheckState.Checked
        if visible != layer.visible:
            self._run(lambda d: ops.set_visible(d, index, visible))
        elif item.text() != layer.name:
            name = item.text()
            self._run(lambda d: ops.rename_layer(d, index, name))

    def _on_opacity_value(self, value: int) -> None:
        self.opacity_label.setText(f"{value} %")
        if not self.opacity.isSliderDown():
            self._on_opacity()

    def _on_opacity(self) -> None:
        if self._updating or self.doc is None:
            return
        value = self.opacity.value() / 100
        index = self.doc.active
        self._run(lambda d: ops.set_opacity(d, index, value))
