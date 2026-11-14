"""Find & replace bar of the code editor (match case, whole word, regular expressions)."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from umc.codeedit import search
from umc.gui.codeeditor.code_view import CodeView, raw_text

_ASTRAL = re.compile(r"[\U00010000-\U0010FFFF]")


def qt_positions(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Python string indices → Qt text positions (Qt counts characters beyond U+FFFF twice)."""
    if not _ASTRAL.search(text):
        return spans
    extra = [0] * (len(text) + 1)
    count = 0
    for i, ch in enumerate(text):
        extra[i] = count
        if ord(ch) > 0xFFFF:
            count += 1
    extra[len(text)] = count
    return [(s + extra[s], e + extra[e]) for s, e in spans]


def _py_index(text: str, qt_pos: int) -> int:
    """Qt text position → Python string index."""
    if not _ASTRAL.search(text):
        return qt_pos
    units = 0
    for i, ch in enumerate(text):
        if units >= qt_pos:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


class FindBar(QFrame):
    def __init__(self, view: CodeView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FindBar")
        self.view = view
        self._spans: list[tuple[int, int]] = []  # Python indices of the matches
        self._keep_count = False
        lay = QGridLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(4)

        self.query = QLineEdit(placeholderText="Find")
        self.case = QCheckBox("Match case")
        self.whole = QCheckBox("Whole word")
        self.regex = QCheckBox("Pattern (.*)")
        self.regex.setToolTip("Regular expression: \\d, \\w, ( ) groups, …")
        self.count = QLabel(objectName="Muted")
        prev_b = QPushButton("↑", objectName="Small")
        prev_b.setToolTip("Previous match (Shift+F3)")
        next_b = QPushButton("↓", objectName="Small")
        next_b.setToolTip("Next match (F3)")
        close_b = QToolButton(text="×")
        close_b.setToolTip("Close (Esc)")
        close_b.setAutoRaise(True)
        self.replacement = QLineEdit(placeholderText="Replace with")
        self.replace_button = QPushButton("Replace", objectName="Small")
        self.replace_all_button = QPushButton("Replace all", objectName="Small")

        lay.addWidget(self.query, 0, 0)
        lay.addWidget(prev_b, 0, 1)
        lay.addWidget(next_b, 0, 2)
        lay.addWidget(self.case, 0, 3)
        lay.addWidget(self.whole, 0, 4)
        lay.addWidget(self.regex, 0, 5)
        lay.addWidget(self.count, 0, 6)
        lay.addWidget(close_b, 0, 7)
        lay.addWidget(self.replacement, 1, 0)
        lay.addWidget(self.replace_button, 1, 1, 1, 2)
        lay.addWidget(self.replace_all_button, 1, 3)
        lay.setColumnStretch(0, 1)
        self._replace_row = (self.replacement, self.replace_button, self.replace_all_button)

        self.query.textChanged.connect(self._options_changed)
        for box in (self.case, self.whole, self.regex):
            box.toggled.connect(self._options_changed)
        self.query.returnPressed.connect(self.find_next)
        self.replacement.returnPressed.connect(self.replace)
        prev_b.clicked.connect(self.find_prev)
        next_b.clicked.connect(self.find_next)
        close_b.clicked.connect(self.close_bar)
        self.replace_button.clicked.connect(self.replace)
        self.replace_all_button.clicked.connect(self.replace_all)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_bar)

        self._timer = QTimer(self)  # re-count after typing in the document
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self.setVisible(False)

    # ------------------------------------------------------------------ open / close
    def open(self, replace: bool) -> None:
        cursor = self.view.textCursor()
        picked = cursor.selectedText()
        if picked and " " not in picked:
            self.query.setText(picked)
        for w in self._replace_row:
            w.setVisible(replace)
        self.setVisible(True)
        self.query.setFocus()
        self.query.selectAll()
        self.refresh()

    def close_bar(self) -> None:
        self.setVisible(False)
        self._spans = []
        self.view.set_find_matches([])
        self.view.setFocus()

    def document_changed(self) -> None:
        """The text changed (typing, another tab): count again shortly."""
        if self.isVisible():
            self._timer.start()

    # ------------------------------------------------------------------ matching
    def _options(self) -> dict:
        return {
            "case": self.case.isChecked(),
            "whole_word": self.whole.isChecked(),
            "regex": self.regex.isChecked(),
        }

    def _options_changed(self) -> None:
        self._keep_count = False
        self.refresh()

    def refresh(self) -> None:
        text = raw_text(self.view.document())
        try:
            self._spans = search.find_all(text, self.query.text(), **self._options())
        except search.SearchError as exc:
            self._spans = []
            self.count.setText(str(exc))
            self.view.set_find_matches([])
            return
        self.view.set_find_matches(qt_positions(text, self._spans))
        if self._keep_count:
            self._keep_count = False
            return
        n = len(self._spans)
        if not self.query.text():
            self.count.setText("")
        else:
            self.count.setText("No matches" if not n else f"{n} match" + ("es" if n > 1 else ""))

    def _select(self, text: str, span: tuple[int, int]) -> None:
        ((start, end),) = qt_positions(text, [span])
        cursor = self.view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def find_next(self) -> None:
        self.refresh()
        if not self._spans:
            return
        text = raw_text(self.view.document())
        cursor = self.view.textCursor()
        here = _py_index(
            text, cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
        )
        span = next((s for s in self._spans if s[0] >= here), self._spans[0])
        self._select(text, span)

    def find_prev(self) -> None:
        self.refresh()
        if not self._spans:
            return
        text = raw_text(self.view.document())
        here = _py_index(text, self.view.textCursor().selectionStart())
        span = next((s for s in reversed(self._spans) if s[0] < here), self._spans[-1])
        self._select(text, span)

    # ------------------------------------------------------------------ replacing
    def replace(self) -> None:
        text = raw_text(self.view.document())
        cursor = self.view.textCursor()
        start = _py_index(text, cursor.selectionStart()) if cursor.hasSelection() else -1
        try:
            hit = search.replacement_at(
                text, self.query.text(), self.replacement.text(), start, **self._options()
            )
        except search.SearchError as exc:
            self.count.setText(str(exc))
            return
        end = _py_index(text, cursor.selectionEnd()) if cursor.hasSelection() else -1
        if hit is None or hit[1] != end:  # the selection must be exactly one match
            self.find_next()
            return
        cursor.insertText(hit[2])
        self.find_next()

    def replace_all(self) -> None:
        text = raw_text(self.view.document())
        try:
            new, n = search.replace_all(
                text, self.query.text(), self.replacement.text(), **self._options()
            )
        except search.SearchError as exc:
            self.count.setText(str(exc))
            return
        if not n:
            self.count.setText("No matches")
            return
        cursor = QTextCursor(self.view.document())
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(new)
        cursor.endEditBlock()
        self._keep_count = True
        self.refresh()
        self.count.setText(f"{n} replaced")
