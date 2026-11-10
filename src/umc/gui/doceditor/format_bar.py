"""The document editor's formatting bar: buttons that call the functions in ``formatting``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QSpinBox,
    QToolButton,
    QWidget,
)

from umc.core.errors import ConversionError
from umc.gui.doceditor import formatting as fm
from umc.gui.doceditor.text_view import DocTextView
from umc.gui.widgets.flow_layout import FlowLayout

STYLES = [
    ("normal", "Normal"),
    ("h1", "Heading 1"),
    ("h2", "Heading 2"),
    ("h3", "Heading 3"),
    ("quote", "Quote"),
    ("code", "Code"),
]
SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72]
_TOGGLES = [
    ("bold", "B", "Bold"),
    ("italic", "I", "Italic"),
    ("underline", "U", "Underline"),
    ("strike", "S", "Strikethrough"),
]
_ALIGNS = [("left", "Left"), ("center", "Centre"), ("right", "Right"), ("justify", "Justify")]
_LISTS = [("bullet", "• List", "Bulleted list"), ("number", "1. List", "Numbered list")]
_TABLE_OPS = [
    ("row_above", "Insert row above"),
    ("row_below", "Insert row below"),
    ("col_left", "Insert column left"),
    ("col_right", "Insert column right"),
    ("del_row", "Delete row"),
    ("del_col", "Delete column"),
    ("del_table", "Delete table"),
    ("merge", "Merge selected cells"),
    ("split", "Split cell"),
]
_PICTURES = "Pictures (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"


class FormatBar(QWidget):
    def __init__(self, view: DocTextView) -> None:
        super().__init__()
        self.view = view
        self.buttons: dict[str, QToolButton] = {}
        flow = FlowLayout(self, spacing=4)

        self.style_box = QComboBox()
        for key, label in STYLES:
            self.style_box.addItem(label, key)
        self.style_box.activated.connect(self._style_chosen)
        flow.addWidget(self.style_box)

        self.font_box = QFontComboBox()
        self.font_box.activated.connect(
            lambda _i: self._apply(fm.set_font_family, self.font_box.currentFont().family())
        )
        flow.addWidget(self.font_box)

        self._size_shown = ""  # the size the box shows for the cursor
        self.size_box = QComboBox()
        self.size_box.setEditable(True)
        self.size_box.addItems([str(s) for s in SIZES])
        self.size_box.setFixedWidth(64)
        self.size_box.activated.connect(lambda _i: self._size_chosen())
        self.size_box.lineEdit().editingFinished.connect(self._size_chosen)
        flow.addWidget(self.size_box)

        for key, text, tip in _TOGGLES:
            flow.addWidget(self._button(key, text, tip, lambda k=key: self._apply(fm.toggle, k)))
        self.color_button = self._plain("A", "Text colour", self._pick_color)
        self.highlight_button = self._plain("Mark", "Highlight", self._pick_highlight)
        self.link_button = self._plain("Link", "Insert link", self._insert_link)
        for w in (self.color_button, self.highlight_button, self.link_button):
            flow.addWidget(w)
        for key, tip in _ALIGNS:
            flow.addWidget(
                self._button(key, tip, f"Align {tip.lower()}", lambda k=key: self._align(k))
            )
        for key, text, tip in _LISTS:
            flow.addWidget(
                self._button(key, text, tip, lambda k=key: self._apply(fm.toggle_list, k))
            )
        flow.addWidget(self._plain("→", "Indent", lambda: self._apply(fm.change_indent, 1)))
        flow.addWidget(self._plain("←", "Outdent", lambda: self._apply(fm.change_indent, -1)))
        self.picture_button = self._plain("Picture", "Insert picture", self._insert_picture)
        self.table_button = self._plain("Table", "Insert table", self._insert_table)
        flow.addWidget(self.picture_button)
        flow.addWidget(self.table_button)

        self.table_menu = QMenu("Table", self)
        for op, label in _TABLE_OPS:
            self.table_menu.addAction(label, lambda o=op: self._apply(fm.table_op, o))
        self.table_menu_button = QToolButton()
        self.table_menu_button.setText("Table ▾")
        self.table_menu_button.setToolTip("Edit the table")
        self.table_menu_button.setMenu(self.table_menu)
        self.table_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        flow.addWidget(self.table_menu_button)

        flow.addWidget(
            self._plain("Break", "Page break", lambda: self._apply(fm.insert_page_break))
        )
        flow.addWidget(self._plain("—", "Horizontal line", lambda: self._apply(fm.insert_hr)))
        flow.addWidget(
            self._plain("Clear", "Clear formatting", lambda: self._apply(fm.clear_formatting))
        )

        view.cursorPositionChanged.connect(self.refresh)
        view.currentCharFormatChanged.connect(lambda _f: self.refresh())
        self.refresh()

    # ----- widgets ----------------------------------------------------------
    def _plain(self, text: str, tip: str, slot: Callable[[], None]) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _button(self, key: str, text: str, tip: str, slot: Callable[[], None]) -> QToolButton:
        button = self._plain(text, tip, slot)
        button.setCheckable(True)
        self.buttons[key] = button
        return button

    # ----- applying ---------------------------------------------------------
    def _apply(self, fn: Callable, *args) -> None:
        c = self.view.textCursor()
        fn(c, *args)
        self.view.setTextCursor(c)
        self.view.setFocus()
        self.refresh()

    def _style_chosen(self, index: int) -> None:
        self._apply(fm.set_block_style, self.style_box.itemData(index))

    def _align(self, key: str) -> None:
        self._apply(fm.set_alignment, key)

    def _size_chosen(self) -> None:
        """Apply the size once, and only when it differs from what the box showed (leaving
        the box untouched must not flatten a selection of mixed sizes)."""
        text = self.size_box.currentText().strip()
        if text == self._size_shown:
            return
        try:
            pt = float(text.replace(",", "."))
        except ValueError:
            self.refresh()
            return
        self._size_shown = text  # activated + editingFinished: applied once
        self._apply(fm.set_font_size, min(400.0, max(1.0, pt)))

    # ----- dialogs (methods, so tests can replace them) -----------------------
    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor("black"), self, "Text colour")
        if color.isValid():
            self._apply(fm.set_color, color)

    def _pick_highlight(self) -> None:
        color = QColorDialog.getColor(QColor("yellow"), self, "Highlight colour")
        if color.isValid():
            self._apply(fm.set_highlight, color)

    def _insert_link(self) -> None:
        url, ok = QInputDialog.getText(self, "Insert link", "Web address:", text="https://")
        url = url.strip()
        if ok and url and url != "https://":
            self._apply(fm.insert_link, url, None)

    def _insert_picture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert picture", "", _PICTURES)
        if not path:
            return
        try:
            self.view.insert_image(Path(path))
        except ConversionError as exc:
            QMessageBox.warning(self, "Picture", str(exc))
        self.view.setFocus()

    def ask_table_size(self) -> tuple[int, int] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Insert table")
        form = QFormLayout(dialog)
        rows, cols = QSpinBox(), QSpinBox()
        for box in (rows, cols):
            box.setRange(1, 50)
            box.setValue(3)
        form.addRow("Rows", rows)
        form.addRow("Columns", cols)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return rows.value(), cols.value()

    def _insert_table(self) -> None:
        size = self.ask_table_size()
        if size is not None:
            self._apply(fm.insert_table, *size)

    # ----- state ------------------------------------------------------------
    def refresh(self) -> None:
        c = self.view.textCursor()
        for key in ("bold", "italic", "underline", "strike"):
            self.buttons[key].setChecked(fm.is_on(c, key))
        align = c.blockFormat().alignment()
        current = "left"
        if align & Qt.AlignmentFlag.AlignHCenter:
            current = "center"
        elif align & Qt.AlignmentFlag.AlignRight:
            current = "right"
        elif align & Qt.AlignmentFlag.AlignJustify:
            current = "justify"
        for key, _tip in _ALIGNS:
            self.buttons[key].setChecked(key == current)
        lst = c.currentList()
        bullet = number = False
        if lst is not None:
            bullet = lst.format().style() in fm._BULLETS
            number = not bullet
        self.buttons["bullet"].setChecked(bullet)
        self.buttons["number"].setChecked(number)

        self.style_box.blockSignals(True)
        self.style_box.setCurrentIndex(self.style_box.findData(fm.block_style(c)))
        self.style_box.blockSignals(False)
        fmt = c.charFormat()
        self.font_box.blockSignals(True)
        self.font_box.setCurrentFont(fmt.font())
        self.font_box.blockSignals(False)
        pt = fmt.fontPointSize() or self.view.document().defaultFont().pointSizeF()
        if not self.size_box.lineEdit().hasFocus():
            self.size_box.blockSignals(True)
            self.size_box.setCurrentText(f"{pt:g}")
            self.size_box.blockSignals(False)
            self._size_shown = f"{pt:g}"
        in_table = c.currentTable() is not None
        self.table_menu.setEnabled(in_table)
        self.table_menu_button.setEnabled(in_table)
