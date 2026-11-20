"""The cue table: #, Start, End, Length, Text; a row with problems gets a warning mark and its
notes as the tooltip; the cue being shown in the preview is bold."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from umc.gui import icons, theme
from umc.subedit.checks import Problem
from umc.subedit.formats import Cue, clock, plain_text

COLUMNS = ("#", "Start", "End", "Length", "Text")
TEXT_COLUMN = 4

_Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()


def length_text(ms: int) -> str:
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    return f"{sign}{ms // 1000}.{ms % 1000:03d}"


class CueModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cues: tuple[Cue, ...] = ()
        self.fmt = "srt"
        self.problems: dict[int, list[Problem]] = {}
        self.playing: set[int] = set()
        self._warn = None

    # ------------------------------------------------------------------ updates
    def set_cues(self, cues: tuple[Cue, ...], fmt: str) -> bool:
        """Show ``cues``. Returns True when rows were added / removed (the view's selection is
        then gone and the page puts it back); same-length changes only repaint changed rows."""
        old = self.cues
        if len(old) != len(cues) or fmt != self.fmt:
            self.beginResetModel()
            self.cues, self.fmt = cues, fmt
            self.problems = {}
            self.endResetModel()
            return True
        self.cues = cues
        changed = [i for i, (a, b) in enumerate(zip(old, cues, strict=True)) if a is not b]
        if changed:
            self.dataChanged.emit(
                self.index(changed[0], 0), self.index(changed[-1], len(COLUMNS) - 1)
            )
        return False

    def set_problems(self, problems: list[Problem]) -> None:
        by_row: dict[int, list[Problem]] = {}
        for p in problems:
            by_row.setdefault(p.index, []).append(p)
        rows = [r for r in set(by_row) | set(self.problems) if r < len(self.cues)]
        self.problems = by_row
        if rows:
            self.dataChanged.emit(self.index(min(rows), 0), self.index(max(rows), 0))

    def set_playing(self, rows: set[int]) -> None:
        if rows == self.playing:
            return
        changed = rows ^ self.playing
        self.playing = rows
        for r in changed:
            if r < len(self.cues):
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    # ------------------------------------------------------------------ Qt model
    def rowCount(self, parent: _Index = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.cues)

    def columnCount(self, parent: _Index = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: _Index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.cues):
            return None
        row, col = index.row(), index.column()
        c = self.cues[row]
        comment = c.get("kind") == "Comment"
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return str(row + 1)
            if col == 1:
                return clock(c.start)
            if col == 2:
                return clock(c.end)
            if col == 3:
                return length_text(c.end - c.start)
            text = plain_text(c.text, self.fmt).replace("\n", " ⏎ ")
            return ("(comment) " if comment else "") + text
        if role == Qt.ItemDataRole.ToolTipRole:
            notes = [p.message for p in self.problems.get(row, [])]
            if comment:
                notes.insert(0, "A comment line: never shown")
            return "\n".join(notes) or None
        if role == Qt.ItemDataRole.DecorationRole and col == 0 and row in self.problems:
            if self._warn is None:
                self._warn = icons.icon("alert", theme.palette()["danger"], 14)
            return self._warn
        if role == Qt.ItemDataRole.ForegroundRole and comment:
            return QColor(theme.palette()["muted"])
        if role == Qt.ItemDataRole.FontRole and row in self.playing:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 1, 2, 3):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.AccessibleTextRole and col == 0 and row in self.problems:
            return f"{row + 1}, problem: " + "; ".join(p.message for p in self.problems[row])
        return None

    def retint(self) -> None:
        """The theme changed: the warning icon is drawn again in the new colour."""
        self._warn = None
        if self.cues:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.cues) - 1, 0))
