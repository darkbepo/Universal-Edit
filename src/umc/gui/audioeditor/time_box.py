"""A small box for typing a time (the selection's Start / End / Length in the status line)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QLineEdit


class TimeBox(QLineEdit):
    """Enter (or leaving the box) sends ``committed`` with the typed text when it changed;
    Esc puts the shown time back. Esc and the editing keys stay with the box while it has
    focus (the page's Esc = Deselect, Delete, Home … shortcuts don't fire)."""

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
        self.setModified(False)

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()  # Esc reverts the box instead of deselecting
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
