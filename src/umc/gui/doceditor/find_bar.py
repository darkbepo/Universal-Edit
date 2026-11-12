"""The document editor's find & replace bar."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRegularExpression, Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from umc.gui.doceditor.html_export import LINK_PLACEHOLDER
from umc.gui.doceditor.text_view import DocTextView

_Find = QTextDocument.FindFlag


class FindBar(QWidget):
    def __init__(self, view: DocTextView) -> None:
        super().__init__()
        self.view = view
        self.setVisible(False)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with")
        self.case_box = QCheckBox("Match case")
        self.whole_box = QCheckBox("Whole word")
        self.status = QLabel("")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.find_edit, 1)
        row.addWidget(self._button("Previous", lambda: self._find_clicked(True)))
        row.addWidget(self._button("Next", lambda: self._find_clicked(False)))
        row.addWidget(self.replace_edit, 1)
        row.addWidget(self._button("Replace", self._replace_clicked))
        row.addWidget(self._button("Replace all", self._replace_all_clicked))
        row.addWidget(self.case_box)
        row.addWidget(self.whole_box)
        row.addWidget(self.status)

        self._replace_widgets = [self.replace_edit, *self._replace_buttons]
        for w in (self.find_edit, self.replace_edit):
            w.installEventFilter(self)

    _replace_buttons: list[QToolButton]

    def _button(self, text: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda _checked=False: slot())
        if text.startswith("Replace"):
            if not hasattr(self, "_replace_buttons"):
                self._replace_buttons = []
            self._replace_buttons.append(button)
        return button

    # ----- showing ----------------------------------------------------------
    def open(self, replace: bool) -> None:
        self.setVisible(True)
        for w in self._replace_widgets:
            w.setVisible(replace)
        selected = self.view.textCursor().selectedText()
        if selected and "\u2029" not in selected and "\n" not in selected:
            self.find_edit.setText(selected)
        self.status.setText("")
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def close_bar(self) -> None:
        self.setVisible(False)
        self.view.setFocus()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._find_clicked(shift)
                return True
        return super().eventFilter(obj, event)

    # ----- searching --------------------------------------------------------
    @staticmethod
    def _flags(case: bool, whole: bool, backward: bool = False) -> _Find:
        flags = _Find(0)
        if case:
            flags |= _Find.FindCaseSensitively
        if whole:
            flags |= _Find.FindWholeWords
        if backward:
            flags |= _Find.FindBackward
        return flags

    @staticmethod
    def _pattern(text: str, case: bool) -> QRegularExpression:
        """``text`` as a pattern that also matches across an empty link's LINK_PLACEHOLDER."""
        skip = f"{LINK_PLACEHOLDER}*"
        pattern = skip.join(QRegularExpression.escape(c) for c in text)
        options = QRegularExpression.PatternOption.NoPatternOption
        if not case:
            options |= QRegularExpression.PatternOption.CaseInsensitiveOption
        return QRegularExpression(pattern, options)

    def _search(self, text: str, cursor: QTextCursor, case: bool, whole: bool, backward=False):
        flags = self._flags(case, whole, backward)
        return self.view.document().find(self._pattern(text, case), cursor, flags)

    def find(
        self, text: str, backward: bool = False, case: bool = False, whole: bool = False
    ) -> bool:
        found = self._find(text, backward, case, whole)
        self.status.setText("" if found else "No matches.")
        return found

    def _find(self, text: str, backward: bool, case: bool, whole: bool) -> bool:
        if not text:
            return False
        doc = self.view.document()
        match = self._search(text, self.view.textCursor(), case, whole, backward)
        if match.isNull():  # wrap around
            start = QTextCursor(doc)
            start.movePosition(
                QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
            )
            match = self._search(text, start, case, whole, backward)
        if match.isNull():
            return False
        self.view.setTextCursor(match)
        self.view.ensureCursorVisible()
        return True

    def replace(self, text: str, new: str, case: bool = False, whole: bool = False) -> bool:
        if not text:
            return False
        cursor = self.view.textCursor()
        if cursor.hasSelection():
            probe = QTextCursor(self.view.document())
            probe.setPosition(cursor.selectionStart())
            match = self._search(text, probe, case, whole)
            if (
                not match.isNull()
                and match.selectionStart() == cursor.selectionStart()
                and match.selectionEnd() == cursor.selectionEnd()
            ):
                cursor.insertText(new)
                self.view.setTextCursor(cursor)
                self._find(text, False, case, whole)
                self.status.setText("")
                return True
        self.find(text, False, case, whole)
        return False

    def replace_all(self, text: str, new: str, case: bool = False, whole: bool = False) -> int:
        if not text:
            return 0
        doc = self.view.document()
        count = 0
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        cursor = QTextCursor(doc)
        while True:
            cursor = self._search(text, cursor, case, whole)
            if cursor.isNull():
                break
            cursor.insertText(new)
            count += 1
        edit.endEditBlock()
        self.status.setText(f"Replaced {count}." if count else "No matches.")
        return count

    # ----- button slots -----------------------------------------------------
    def _find_clicked(self, backward: bool) -> None:
        self.find(
            self.find_edit.text(), backward, self.case_box.isChecked(), self.whole_box.isChecked()
        )

    def _replace_clicked(self) -> None:
        self.replace(
            self.find_edit.text(),
            self.replace_edit.text(),
            self.case_box.isChecked(),
            self.whole_box.isChecked(),
        )

    def _replace_all_clicked(self) -> None:
        self.replace_all(
            self.find_edit.text(),
            self.replace_edit.text(),
            self.case_box.isChecked(),
            self.whole_box.isChecked(),
        )
