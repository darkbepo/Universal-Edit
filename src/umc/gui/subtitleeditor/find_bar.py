"""Find & replace over the subtitles' text (the code editor's rules: match case, whole word,
regular expression in Python's syntax). The page does the moving and replacing."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton


class FindBar(QFrame):
    find_next = Signal()
    find_prev = Signal()
    replace_one = Signal()
    replace_all = Signal()
    changed = Signal()  # the query or an option changed: count again

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FindBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(6)
        self.query = QLineEdit(placeholderText="Find in the subtitles")
        self.query.setAccessibleName("Find")
        self.replacement = QLineEdit(placeholderText="Replace with")
        self.replacement.setAccessibleName("Replace with")
        self.case = QCheckBox("Match case")
        self.word = QCheckBox("Whole word")
        self.regex = QCheckBox("Regex")
        self.count = QLabel(objectName="Muted")
        lay.addWidget(self.query, 2)
        for text, sig in (("↑", self.find_prev), ("↓", self.find_next)):
            b = QPushButton(text, objectName="Small")
            b.setToolTip("Previous match (Shift+F3)" if text == "↑" else "Next match (F3)")
            b.clicked.connect(sig)
            lay.addWidget(b)
        lay.addWidget(self.replacement, 2)
        self.replace_button = QPushButton("Replace", objectName="Small")
        self.replace_button.clicked.connect(self.replace_one)
        self.replace_all_button = QPushButton("Replace all", objectName="Small")
        self.replace_all_button.clicked.connect(self.replace_all)
        lay.addWidget(self.replace_button)
        lay.addWidget(self.replace_all_button)
        for w in (self.case, self.word, self.regex):
            w.toggled.connect(self.changed)
            lay.addWidget(w)
        lay.addWidget(self.count)
        close = QPushButton("×", objectName="Small")
        close.setToolTip("Close (Esc)")
        close.setAccessibleName("Close find")
        close.clicked.connect(self.hide)
        lay.addWidget(close)
        self.query.textChanged.connect(self.changed)
        self.query.returnPressed.connect(self.find_next)
        self.replacement.returnPressed.connect(self.replace_one)
        for w in (self.query, self.replacement):
            w.installEventFilter(self)
        self.hide()

    def options(self) -> dict[str, bool]:
        return {
            "case": self.case.isChecked(),
            "whole_word": self.word.isChecked(),
            "regex": self.regex.isChecked(),
        }

    def open(self, replace: bool) -> None:
        self.show()
        self.replacement.setVisible(True)
        target = self.replacement if replace and self.query.text() else self.query
        target.setFocus()
        target.selectAll()
        self.changed.emit()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.hide()
            return True
        return super().eventFilter(obj, e)
