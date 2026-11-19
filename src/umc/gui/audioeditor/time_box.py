"""A small box for typing a time (the selection's Start / End / Length in the status line)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLineEdit


class TimeBox(QLineEdit):
    """Enter (or leaving the box) sends ``committed`` with the typed text when it changed;
    the page calls ``setModified(False)`` once it took the time (a refused one stays as typed).
    Esc puts the shown time back. Every key but Ctrl+S stays with the box while it has focus,
    so the page's shortcuts (Esc = Deselect, Delete, Ctrl+T …) don't fire while typing."""

    committed = Signal(str)

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TimeBox")
        self.setAccessibleName(name)
        self.setToolTip(f"{name} — type a time like 1:05.250 and press Enter")
        self.setFixedWidth(self.fontMetrics().horizontalAdvance("00:00:00.0000") + 16)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._shown = ""
        self.editingFinished.connect(self._finish)

    def show_time(self, text: str) -> None:
        """Show ``text`` unless the user is typing in the box."""
        self._shown = text
        if not (self.hasFocus() and self.isModified()):
            self.setText(text)
            self.setModified(False)

    def set_error(self, error: bool) -> None:
        if self.property("invalid") != error:
            self.setProperty("invalid", error)
            self.style().unpolish(self)
            self.style().polish(self)

    def _finish(self) -> None:
        text = self.text()
        if text.strip() != self._shown:
            self.committed.emit(text)
        else:
            self.setModified(False)

    def event(self, e) -> bool:
        override = e.type() == QEvent.Type.ShortcutOverride
        if override and not e.matches(QKeySequence.StandardKey.Save):
            e.accept()  # the key goes to the box, not to a page shortcut
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.setText(self._shown)
            self.setModified(False)
            self.set_error(False)
            self.clearFocus()
            return
        super().keyPressEvent(e)
