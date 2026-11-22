"""The grid: one sheet's cells. Text is shown on one line (a line break as ⏎); numbers and
true / false sit on the right; a null in JSON / YAML / XML shows a faint "null" (an absent key
shows nothing). Typing in a cell goes to the page (``edited``), which makes it an undo step and
gives the model the new sheet."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from umc.dataedit.cells import Num, display, edit_text
from umc.dataedit.table import Sheet, letter
from umc.gui import theme

_Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()


def column_title(sheet: Sheet, c: int) -> str:
    name = sheet.columns[c] if sheet.header else ""
    return name or letter(c)


class SheetModel(QAbstractTableModel):
    edited = Signal(int, int, str)  # row, column, the typed text

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.sheet = Sheet("", (), (), typed=False)

    def set_sheet(self, sheet: Sheet) -> bool:
        """Show ``sheet``. True when rows / columns were added or removed (the view's selection
        is gone then); otherwise only the changed rows and names are repainted."""
        old = self.sheet
        if (len(old.rows), len(old.columns), old.header) != (
            len(sheet.rows), len(sheet.columns), sheet.header
        ):  # fmt: skip
            self.beginResetModel()
            self.sheet = sheet
            self.endResetModel()
            return True
        self.sheet = sheet
        changed = [
            i for i, (a, b) in enumerate(zip(old.rows, sheet.rows, strict=True)) if a is not b
        ]
        if changed:
            last = len(sheet.columns) - 1
            self.dataChanged.emit(self.index(changed[0], 0), self.index(changed[-1], last))
        if old.columns != sheet.columns and sheet.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(sheet.columns) - 1)
        return False

    # ------------------------------------------------------------------ Qt model
    def rowCount(self, parent: _Index = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.sheet.rows)

    def columnCount(self, parent: _Index = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.sheet.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return (
                    column_title(self.sheet, section) if section < len(self.sheet.columns) else None
                )
            return str(section + 1)
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            return f"Column {letter(section)} — double-click to rename, right-click for more"
        return None

    def flags(self, index: _Index) -> Qt.ItemFlag:
        base = super().flags(index)
        if not index.isValid():
            return base
        cell = self.sheet.rows[index.row()][index.column()]
        if "\n" in edit_text(cell, self.sheet.typed):  # several lines: the edit box takes it
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: _Index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.sheet.rows):
            return None
        cell = self.sheet.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if cell is None and self.sheet.tree:
                return "null"
            return display(cell).replace("\n", " ⏎ ")
        if role == Qt.ItemDataRole.EditRole:
            return edit_text(cell, self.sheet.typed)
        if role == Qt.ItemDataRole.ToolTipRole:
            text = display(cell)
            return text if len(text) > 40 or "\n" in text else None
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(cell, Num | bool):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and cell is None and self.sheet.tree:
            return QColor(theme.palette()["muted"])
        if role == Qt.ItemDataRole.FontRole and cell is None and self.sheet.tree:
            f = QFont()
            f.setItalic(True)
            return f
        return None

    def setData(self, index: _Index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        self.edited.emit(index.row(), index.column(), str(value))
        return True
