"""The vector editor's side panels: Layers (the drawing's parts, top first) and Appearance
(fill, outline, opacity, position / size, text of the selection).

Both only show and ask: every change goes through the page (``page.set_fill`` …), which makes
it one undo step."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from umc.vectoredit import doc as vdoc
from umc.vectoredit import shapes
from umc.vectoredit.doc import Path, VectorDoc

PATH_ROLE = Qt.ItemDataRole.UserRole


class LayersPanel(QTreeWidget):
    """One row per element (groups fold open), the topmost first. The tick shows / hides."""

    chosen = Signal(list)  # paths
    visibility = Signal(object, bool)  # path, shown
    rename_requested = Signal(object)  # path
    menu_requested = Signal(object)  # global position

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("VectorLayers")
        self.setAccessibleName("Layers")
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menu_requested.emit(self.viewport().mapToGlobal(pos))
        )
        self.itemSelectionChanged.connect(self._selection_changed)
        self.itemChanged.connect(self._item_changed)
        self.itemDoubleClicked.connect(
            lambda item, _c: self.rename_requested.emit(item.data(0, PATH_ROLE))
        )
        self._filling = False
        self._shape: list | None = None

    def show_document(self, doc: VectorDoc, selection: list[Path]) -> None:
        listing = vdoc.items(doc)
        shape = [(i.path, i.name, i.hidden) for i in listing]
        self._filling = True
        if shape != self._shape:  # rebuild only when the parts changed (keeps folds / scroll)
            self._shape = shape
            self.clear()
            made: dict[Path, QTreeWidgetItem] = {}
            for item in listing:  # document order; each row goes on top: topmost first
                parent = made.get(item.path[:-1])
                row = QTreeWidgetItem([item.name])
                row.setData(0, PATH_ROLE, item.path)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    0, Qt.CheckState.Unchecked if item.hidden else Qt.CheckState.Checked
                )
                row.setToolTip(0, f"{item.kind} — tick to show, double-click to rename")
                if parent is None:
                    self.insertTopLevelItem(0, row)
                else:
                    parent.insertChild(0, row)
                made[item.path] = row
            self.expandToDepth(0)
        wanted = set(selection)
        it = QTreeWidgetItemIterator(self)
        while it.value() is not None:
            row = it.value()
            row.setSelected(row.data(0, PATH_ROLE) in wanted)
            it += 1
        self._filling = False

    def _selection_changed(self) -> None:
        if not self._filling:
            self.chosen.emit([i.data(0, PATH_ROLE) for i in self.selectedItems()])

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if not self._filling:
            self.visibility.emit(
                item.data(0, PATH_ROLE), item.checkState(0) == Qt.CheckState.Checked
            )


class ColourButton(QPushButton):
    """A swatch; clicking asks for a colour (through the page, so tests can answer)."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.setObjectName("Small")
        self.setAccessibleName(label)
        self.setFixedWidth(64)
        self.value: str | None = None

    def show_colour(self, value: str | None) -> None:
        self.value = value
        c = QColor(value) if value and value != "none" else None
        if c is None or not c.isValid():
            self.setText("—" if value is None else ("none" if value == "none" else value))
            self.setStyleSheet("")
            return
        self.setText("")
        self.setStyleSheet(f"background: {c.name()};")


class AppearancePanel(QFrame):
    """Fill, outline, width, opacity, position / size and text of the selection."""

    def __init__(self, page) -> None:
        super().__init__()
        self.setObjectName("VectorAppearance")
        self.page = page
        self._filling = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(QLabel("Appearance", objectName="Section"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def colour_row(label: str, which: str) -> tuple[ColourButton, QCheckBox]:
            row = QHBoxLayout()
            button = ColourButton(label)
            button.clicked.connect(lambda _c=False: page.choose_colour(which))
            none = QCheckBox("None")
            none.setToolTip(f"No {which}")
            none.toggled.connect(lambda on: self._none_toggled(which, on))
            row.addWidget(button)
            row.addWidget(none)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            form.addRow(label, holder)
            return button, none

        self.fill_button, self.fill_none = colour_row("Fill", "fill")
        self.stroke_button, self.stroke_none = colour_row("Outline", "stroke")
        self.width_box = QDoubleSpinBox()
        self.width_box.setAccessibleName("Outline width")
        self.width_box.setRange(0, 1000)
        self.width_box.setDecimals(2)
        self.width_box.setSingleStep(0.5)
        self.width_box.editingFinished.connect(self._width_done)
        form.addRow("Width", self.width_box)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setAccessibleName("Opacity")
        self.opacity.setRange(0, 100)
        self.opacity.sliderReleased.connect(self._opacity_done)
        self.opacity.valueChanged.connect(self._opacity_moved)
        form.addRow("Opacity", self.opacity)
        lay.addLayout(form)

        lay.addWidget(QLabel("Position and size", objectName="Section"))
        geo = QFormLayout()
        self.geo: dict[str, QDoubleSpinBox] = {}
        for key, label in (("x", "X"), ("y", "Y"), ("w", "Width"), ("h", "Height")):
            box = QDoubleSpinBox()
            box.setAccessibleName(label)
            box.setRange(-1e7, 1e7)
            box.setDecimals(3)
            box.editingFinished.connect(self._geometry_done)
            geo.addRow(label, box)
            self.geo[key] = box
        lay.addLayout(geo)

        self.text_label = QLabel("Text", objectName="Section")
        self.text_box = QLineEdit()
        self.text_box.setAccessibleName("Text")
        self.text_box.editingFinished.connect(lambda: page.set_text_content(self.text_box.text()))
        lay.addWidget(self.text_label)
        lay.addWidget(self.text_box)
        self.hint = QLabel(
            "Choose something in the drawing to change how it looks.", objectName="Muted"
        )
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        lay.addStretch(1)

    def show_selection(self, doc: VectorDoc | None, selection: list[Path]) -> None:
        self._filling = True
        has = doc is not None and bool(selection)
        for w in (self.fill_button, self.fill_none, self.stroke_button, self.stroke_none,
                  self.width_box, self.opacity, *self.geo.values()):  # fmt: skip
            w.setEnabled(has)
        self.hint.setVisible(not has)
        text_el = None
        if has:
            el = vdoc.get(doc, selection[0])
            fill = shapes.style_get(el, "fill")
            stroke = shapes.style_get(el, "stroke")
            self.fill_button.show_colour(fill)
            self.fill_none.setChecked(fill == "none")
            self.stroke_button.show_colour(stroke)
            self.stroke_none.setChecked(stroke in (None, "none"))
            self.width_box.setValue(shapes.length(shapes.style_get(el, "stroke-width")) or 1.0)
            op = shapes.length(shapes.style_get(el, "opacity"))
            self.opacity.setValue(round(100 * (1.0 if op is None else op)))
            box = vdoc.selection_bounds(doc, selection)
            values = (box.x0, box.y0, box.width, box.height) if box is not None else (0, 0, 0, 0)
            for key, v in zip(("x", "y", "w", "h"), values, strict=True):
                self.geo[key].setValue(v)
            if len(selection) == 1 and shapes.kind(el) == "text":
                text_el = el
        else:
            self.fill_button.show_colour(None)
            self.stroke_button.show_colour(None)
        self.text_label.setVisible(text_el is not None)
        self.text_box.setVisible(text_el is not None)
        if text_el is not None:
            self.text_box.setText(text_el.text())
        self._filling = False

    def _none_toggled(self, which: str, on: bool) -> None:
        if self._filling:
            return
        if on:
            self.page.set_colour(which, "none")
        else:
            self.page.set_colour(which, "#000000" if which == "stroke" else "#808080")

    def _width_done(self) -> None:
        if not self._filling:
            self.page.set_stroke_width(self.width_box.value())

    def _opacity_moved(self, _v: int) -> None:
        if not self._filling and not self.opacity.isSliderDown():  # keyboard / click
            self._opacity_done()

    def _opacity_done(self) -> None:
        if not self._filling:
            self.page.set_opacity(self.opacity.value() / 100)

    def _geometry_done(self) -> None:
        if not self._filling:
            g = {k: b.value() for k, b in self.geo.items()}
            self.page.set_geometry(g["x"], g["y"], g["w"], g["h"])
