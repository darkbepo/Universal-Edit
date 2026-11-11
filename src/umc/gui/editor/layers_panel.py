"""Layers list (top layer first): thumbnail, name, visibility, up / down arrows on every row.

Click selects a layer, Ctrl+click / Shift+click select several, dragging rows reorders them,
right-click opens a menu with every layer action. The opacity slider and the buttons below act
on all selected layers.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDragLeaveEvent, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from umc.editor import layerfx, ops
from umc.editor.document import Document
from umc.gui import theme
from umc.gui.editor.canvas import pil_to_qimage

THUMB = 40
MASK_THUMB = 26


def fx_names(layer) -> str:
    """The layer's effects that are on, as "Drop shadow, Outline" ("" = none)."""
    fx = layer.fx
    if fx is None:
        return ""
    return ", ".join(label for key, label, _icon in layerfx.EFFECTS if getattr(fx, key))


MASK_ROLE = Qt.ItemDataRole.UserRole + 1  # a QPixmap of the layer's mask, if it has one
FX_ROLE = Qt.ItemDataRole.UserRole + 2  # the names of the layer's effects ("" = none)
FX_W = 22  # width of the "fx" badge
ARROW_W = 22  # width of the up / down arrow column at the right of each row


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


def mask_thumbnail(mask: Image.Image) -> QPixmap:
    t = mask.copy()
    t.thumbnail((MASK_THUMB, MASK_THUMB))
    bg = Image.new("L", (MASK_THUMB, MASK_THUMB), 128)
    bg.paste(t, ((MASK_THUMB - t.width) // 2, (MASK_THUMB - t.height) // 2))
    return QPixmap.fromImage(pil_to_qimage(bg.convert("RGBA")))


def arrow_rects(row_rect: QRect) -> tuple[QRect, QRect]:
    """The up and down arrow buttons of one row (widget coordinates of the viewport)."""
    x = row_rect.right() - ARROW_W - 2
    top, mid, bottom = row_rect.top() + 3, row_rect.center().y(), row_rect.bottom() - 3
    return QRect(x, top, ARROW_W, mid - top), QRect(x, mid + 1, ARROW_W, bottom - mid)


class LayerDelegate(QStyledItemDelegate):
    """Default row (check box, thumbnail, name) with the name elided before the arrows."""

    def __init__(self, view: LayerList) -> None:
        super().__init__(view)
        self.view = view

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        mask = index.data(MASK_ROLE)
        fx = bool(index.data(FX_ROLE))
        extra = (MASK_THUMB + 6 if mask is not None else 0) + (FX_W + 4 if fx else 0)
        width = max(10, text_rect.width() - ARROW_W - 6 - extra)
        opt.text = opt.fontMetrics.elidedText(opt.text, Qt.TextElideMode.ElideRight, width)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        r = option.rect
        x = r.right() - ARROW_W - 6
        if mask is not None:  # the layer mask, left of the arrows
            x -= MASK_THUMB
            painter.drawPixmap(x, r.center().y() - MASK_THUMB // 2, mask)
            x -= 4
        if fx:  # a small "fx" badge: the layer has effects
            self._paint_fx(painter, QRect(x - FX_W, r.center().y() - 8, FX_W, 16))
        self._paint_arrows(painter, option.rect, index.row())

    def _paint_fx(self, painter: QPainter, box: QRect) -> None:
        pal = theme.palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(pal["border"]))
        painter.setBrush(QColor(pal["surface_alt"]))
        painter.drawRoundedRect(box, 4, 4)
        font = painter.font()
        font.setItalic(True)
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        painter.setFont(font)
        painter.setPen(QColor(pal["text"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "fx")
        painter.restore()

    def _paint_arrows(self, painter: QPainter, rect: QRect, row: int) -> None:
        pal = theme.palette()
        count = self.view.count()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for box, up in zip(arrow_rects(rect), (True, False), strict=True):
            enabled = row > 0 if up else row < count - 1
            if enabled and self.view.hover_arrow == (row, up):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(pal["surface_alt"]))
                painter.drawRoundedRect(box, 5, 5)
            color = QColor(pal["text"] if enabled else pal["border"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            c = QPointF(box.center())
            w, h = 4.5, 3.0
            pts = (
                [
                    QPointF(c.x() - w, c.y() + h),
                    QPointF(c.x() + w, c.y() + h),
                    QPointF(c.x(), c.y() - h),
                ]
                if up
                else [
                    QPointF(c.x() - w, c.y() - h),
                    QPointF(c.x() + w, c.y() - h),
                    QPointF(c.x(), c.y() + h),
                ]
            )
            painter.drawPolygon(QPolygonF(pts))
        painter.restore()


class LayerList(QListWidget):
    """The list itself: arrow clicks and drops are reported, Qt never moves rows by itself."""

    arrow_clicked = Signal(int, int)  # row, +1 (up) / −1 (down)
    rows_dropped = Signal(int)  # gap row: 0 = above the first row, count() = below the last

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LayerList")
        self.hover_arrow: tuple[int, bool] | None = None
        self.setItemDelegate(LayerDelegate(self))
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def arrow_at(self, pos: QPoint) -> tuple[int, int] | None:
        """(row, delta) when ``pos`` is on an enabled arrow."""
        item = self.itemAt(pos)
        if item is None:
            return None
        row = self.row(item)
        up, down = arrow_rects(self.visualItemRect(item))
        if up.contains(pos) and row > 0:
            return row, 1
        if down.contains(pos) and row < self.count() - 1:
            return row, -1
        return None

    def gap_at(self, pos: QPoint) -> int:
        item = self.itemAt(pos)
        if item is None:
            if self.count() and pos.y() < self.visualItemRect(self.item(0)).top():
                return 0
            return self.count()
        row = self.row(item)
        return row + (1 if pos.y() > self.visualItemRect(item).center().y() else 0)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        hit = self.arrow_at(e.position().toPoint())
        if hit is not None and e.button() == Qt.MouseButton.LeftButton:
            self.arrow_clicked.emit(*hit)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.arrow_at(e.position().toPoint()) is not None:
            self.mousePressEvent(e)  # a quick second click on an arrow moves again
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        hit = self.arrow_at(e.position().toPoint())
        hover = (hit[0], hit[1] > 0) if hit else None
        if hover != self.hover_arrow:
            self.hover_arrow = hover
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if self.hover_arrow is not None:
            self.hover_arrow = None
            self.viewport().update()
        super().leaveEvent(e)

    def viewportEvent(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.ToolTip:
            hit = self.arrow_at(e.pos())
            if hit is not None:
                QToolTip.showText(e.globalPos(), "Move up" if hit[1] > 0 else "Move down", self)
                return True
        return super().viewportEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.source() is not self:
            e.ignore()
            return
        gap = self.gap_at(e.position().toPoint())
        # Clears the drop indicator and drag state like a normal drop, without moving rows;
        # IgnoreAction also stops the drag from deleting the "moved" source rows afterwards.
        super().dragLeaveEvent(QDragLeaveEvent())
        e.setDropAction(Qt.DropAction.IgnoreAction)
        e.accept()
        QTimer.singleShot(0, lambda: self.rows_dropped.emit(gap))


class LayersPanel(QWidget):
    command = Signal(object)  # a Command produced by ops.* (already applied)
    active_changed = Signal(int)
    edit_text_requested = Signal(int)  # layer index
    fx_requested = Signal(str)  # an effect's window: "shadow", "glow", "outline", "overlay"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc: Document | None = None
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(QLabel("LAYERS", objectName="Section"))

        self.list = LayerList()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.list.setToolTip(
            "Double-click a name to rename · tick to show / hide · drag to reorder\n"
            "Ctrl+click or Shift+click to select several · right-click for more"
        )
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.currentRowChanged.connect(lambda _r: self._on_selection())
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.arrow_clicked.connect(self._on_arrow)
        self.list.rows_dropped.connect(self._on_drop)
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

        self.add_button = button("Add", "New empty layer", self.add)
        self.duplicate_button = button("Duplicate", "Copy the selected layers", self.duplicate)
        self.delete_button = button("Delete", "Delete the selected layers", self.delete)
        self.merge_button = button("Merge down", "Merge into the layer below", self.merge)
        for i, b in enumerate(
            (self.add_button, self.duplicate_button, self.delete_button, self.merge_button)
        ):
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

    # ------------------------------------------------------------ state
    def _row_to_index(self, row: int) -> int:
        return len(self.doc.layers) - 1 - row

    def _index_to_row(self, index: int) -> int:
        return len(self.doc.layers) - 1 - index

    def selected_rows(self) -> set[int]:
        return {self.list.row(item) for item in self.list.selectedItems()}

    def in_sync(self, doc: Document) -> bool:
        """True when the list shows ``doc``'s layers, names, visibility and selection."""
        if self.doc is not doc or self.list.count() != len(doc.layers):
            return False
        for row in range(self.list.count()):
            layer = doc.layers[self._row_to_index(row)]
            item = self.list.item(row)
            visible = item.checkState() == Qt.CheckState.Checked
            if item.text() != layer.name or visible != layer.visible:
                return False
            if (item.data(MASK_ROLE) is None) != (layer.mask is None):  # mask added / removed
                return False
            if (item.data(FX_ROLE) or "") != fx_names(layer):
                return False
        return (
            self.list.currentRow() == self._index_to_row(doc.active)
            and self.selected_rows() == {self._index_to_row(i) for i in doc.selected_indices}
            and self.opacity.value() == round(doc.active_layer.opacity * 100)
        )

    def refresh(self, doc: Document) -> None:
        self.doc = doc
        self._updating = True
        try:
            self.list.clear()
            for layer in reversed(doc.layers):
                item = QListWidgetItem(thumbnail(layer.image), layer.name)
                tips = []
                if layer.mask is not None:
                    item.setData(MASK_ROLE, mask_thumbnail(layer.mask))
                    tips.append("This layer has a mask: black hides, white shows")
                if fx_names(layer):
                    item.setData(FX_ROLE, fx_names(layer))
                    tips.append(f"Effects: {fx_names(layer)}")
                if tips:
                    item.setToolTip("\n".join(tips))
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEditable
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                state = Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
                item.setCheckState(state)
                self.list.addItem(item)
            self._show_selection()
            self._sync_controls()
        finally:
            self._updating = False

    def _show_selection(self) -> None:
        doc = self.doc
        self.list.setCurrentRow(
            self._index_to_row(doc.active), QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        for i in doc.selected_indices:
            self.list.item(self._index_to_row(i)).setSelected(True)

    def refresh_thumbnails(self) -> None:
        if self.doc is None or self.list.count() != len(self.doc.layers):
            return
        for row in range(self.list.count()):
            layer = self.doc.layers[self._row_to_index(row)]
            self.list.item(row).setIcon(thumbnail(layer.image))
            if layer.mask is not None:
                self.list.item(row).setData(MASK_ROLE, mask_thumbnail(layer.mask))

    def _sync_controls(self) -> None:
        doc = self.doc
        layer = doc.active_layer
        self.opacity.blockSignals(True)
        self.opacity.setValue(round(layer.opacity * 100))
        self.opacity.blockSignals(False)
        self.opacity_label.setText(f"{round(layer.opacity * 100)} %")
        n, many = len(doc.layers), len(doc.selected_indices) > 1
        self.delete_button.setEnabled(len(doc.selected_indices) < n)
        self.merge_button.setText("Merge layers" if many else "Merge down")
        self.merge_button.setToolTip(
            "Merge the selected layers into one" if many else "Merge into the layer below"
        )
        self.merge_button.setEnabled(many or doc.active > 0)

    # ------------------------------------------------------------ actions
    def _run(self, fn) -> None:
        if self.doc is None:
            return
        cmd = fn(self.doc)
        if cmd is not None:
            self.command.emit(cmd)

    def add(self) -> None:
        self._run(ops.add_layer)

    def duplicate(self) -> None:
        self._run(lambda d: ops.duplicate_layers(d, d.selected_indices))

    def delete(self) -> None:
        self._run(lambda d: ops.delete_layers(d, d.selected_indices))

    def merge(self) -> None:
        def go(d: Document):
            chosen = d.selected_indices
            return ops.merge_layers(d, chosen) if len(chosen) > 1 else ops.merge_down(d)

        self._run(go)

    def move(self, delta: int) -> None:
        self._run(lambda d: ops.move_layers_by(d, d.selected_indices, delta))

    def move_to_end(self, top: bool) -> None:
        self._run(lambda d: ops.move_layers_to(d, d.selected_indices, len(d.layers) if top else 0))

    def set_visible(self, visible: bool) -> None:
        self._run(lambda d: ops.set_visible_layers(d, d.selected_indices, visible))

    def select_all(self) -> None:
        if self.doc is not None:
            ops.select_layers(self.doc, range(len(self.doc.layers)), self.doc.active)
            self.refresh(self.doc)
            self.active_changed.emit(self.doc.active)

    def rename(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.editItem(item)

    # ------------------------------------------------------------ events
    def _on_selection(self) -> None:
        if self._updating or self.doc is None or self.list.count() != len(self.doc.layers):
            return
        rows = sorted(self.selected_rows())
        current = self.list.currentRow()
        if not rows:  # Ctrl+click on the only selected layer: keep it selected
            if current < 0:
                return
            self._updating = True
            self.list.item(current).setSelected(True)
            self._updating = False
            rows = [current]
        indices = [self._row_to_index(r) for r in rows]
        active = self._row_to_index(current) if current in rows else max(indices)
        before = (self.doc.active, set(self.doc.selected_indices))
        ops.select_layers(self.doc, indices, active)
        self._sync_controls()
        if (self.doc.active, set(self.doc.selected_indices)) != before:
            self.active_changed.emit(self.doc.active)

    def _on_arrow(self, row: int, delta: int) -> None:
        if self.doc is None:
            return
        index = self._row_to_index(row)
        if index not in self.doc.selected_indices:
            ops.select_layer(self.doc, index)  # an arrow on another row moves just that row
        self.move(delta)

    def _on_drop(self, gap_row: int) -> None:
        if self.doc is None:
            return
        gap = len(self.doc.layers) - gap_row
        self._run(lambda d: ops.move_layers_to(d, d.selected_indices, gap))
        if not self.in_sync(self.doc):  # whatever Qt's drag did to the rows, show the layers
            self.refresh(self.doc)

    def build_menu(self, row: int | None) -> QMenu:
        """The right-click menu for ``row`` (None = empty space below the layers)."""
        menu = QMenu(self)
        menu.addAction("New layer", self.add)
        doc = self.doc
        if doc is None or row is None:
            return menu
        chosen = doc.selected_indices
        n, many = len(chosen), len(chosen) > 1
        word = f"{n} layers" if many else "layer"
        menu.addAction(f"Duplicate {word}", self.duplicate)
        delete = menu.addAction(f"Delete {word}", self.delete)
        delete.setEnabled(n < len(doc.layers))
        merge = menu.addAction(f"Merge {n} layers" if many else "Merge down", self.merge)
        merge.setEnabled(many or doc.active > 0)
        menu.addSeparator()
        top, bottom = max(chosen) == len(doc.layers) - 1, min(chosen) == 0
        for label, fn, enabled in (
            ("Move up", lambda: self.move(1), not top),
            ("Move down", lambda: self.move(-1), not bottom),
            ("Move to top", lambda: self.move_to_end(True), not top or many),
            ("Move to bottom", lambda: self.move_to_end(False), not bottom or many),
        ):
            menu.addAction(label, fn).setEnabled(enabled)
        menu.addSeparator()
        if not many:
            menu.addAction("Rename…", self.rename)
            if doc.layers[doc.active].text is not None:
                i = doc.active
                menu.addAction("Edit text…", lambda: self.edit_text_requested.emit(i))
            active = doc.layers[doc.active]
            if active.text is not None or active.shape is not None or active.fx is not None:
                i = doc.active
                menu.addAction(
                    "Rasterize layer (plain pixels)",
                    lambda: self._run(lambda d: ops.rasterize_layer(d, i)),
                )
            effects = menu.addMenu("Layer effects")
            for key, label, _icon in layerfx.EFFECTS:
                on = active.fx is not None and getattr(active.fx, key)
                effects.addAction(
                    f"{label}…" + ("  ✓" if on else ""),
                    lambda k=key: self.fx_requested.emit(k),
                )
            if active.fx is not None:
                effects.addSeparator()
                effects.addAction(
                    "Clear layer effects", lambda: self._run(lambda d: ops.clear_layer_fx(d))
                )
        if any(doc.layers[i].visible for i in chosen):
            menu.addAction(f"Hide {word}", lambda: self.set_visible(False))
        if any(not doc.layers[i].visible for i in chosen):
            menu.addAction(f"Show {word}", lambda: self.set_visible(True))
        if len(chosen) == 1 and len(doc.layers) > 1:
            only = chosen[0]
            menu.addAction(
                "Show all layers"
                if ops.is_solo(doc, only)
                else "Show only this layer (Alt+click eye)",
                lambda: self._run(lambda d: ops.solo_layer(d, only)),
            )
        menu.addAction("Select all layers", self.select_all)
        if not many:  # layer mask actions for the one selected layer
            menu.addSeparator()
            layer = doc.layers[doc.active]
            if layer.mask is None:
                menu.addAction("Add layer mask", lambda: self._run(lambda d: ops.add_mask(d)))
                if doc.selection is not None:
                    menu.addAction(
                        "Mask from selection",
                        lambda: self._run(lambda d: ops.add_mask(d, from_selection=True)),
                    )
            else:
                for label, fn in (
                    ("Invert mask", ops.invert_mask),
                    ("Apply mask (bake it in)", ops.apply_mask),
                    ("Delete mask", ops.delete_mask),
                ):
                    menu.addAction(label, lambda f=fn: self._run(lambda d: f(d)))
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        if self.doc is None:
            return
        item = self.list.itemAt(pos)
        row = None if item is None else self.list.row(item)
        if row is not None and row not in self.selected_rows():
            self.list.setCurrentRow(row)  # right-click outside the selection selects that row
        menu = self.build_menu(row)
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating or self.doc is None:
            return
        index = self._row_to_index(self.list.row(item))
        layer = self.doc.layers[index]
        visible = item.checkState() == Qt.CheckState.Checked
        if visible != layer.visible:
            alt = QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier
            if alt:  # Alt+click the eye: show only this layer (again: show all)
                cmd = ops.solo_layer(self.doc, index)
                if cmd is None:  # nothing to change (e.g. one layer): undo Qt's own toggle
                    self._updating = True
                    item.setCheckState(
                        Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
                    )
                    self._updating = False
                else:
                    self.command.emit(cmd)
            else:
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
        self._run(lambda d: ops.set_opacity_layers(d, d.selected_indices, value))
