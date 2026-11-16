"""The "Change details" card for PDF, Word / Excel / PowerPoint and OpenDocument files.

Title, subject, author and keywords. A PDF locked with a password first shows a password box
("Open"); a signed PDF gets a note that readers will show it was changed after signing. With
several documents (and "Apply to all" on) a field that differs reads "Several values" and is only
changed when typed in. ``save_requested(edit, as_copy, all)`` — the panel does the saving;
``unlock_requested(password)`` — the panel reads the file again with it."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.properties import document
from umc.properties.document import FIELDS, DocumentDetails, DocumentEdit

LABELS = {"title": "Title", "subject": "Subject", "author": "Author", "keywords": "Keywords"}
HINTS = {"keywords": "Separate them with commas"}
SEVERAL = "Several values"


class DocumentForm(QFrame):
    save_requested = Signal(object, bool, bool)  # DocumentEdit, as a copy, every document
    unlock_requested = Signal(str)  # the password typed for the shown PDF

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="Card")
        self.setAccessibleName("Change details")
        self._start: dict[str, str | None] = {}
        self._loading = False
        self.password = ""  # the shown PDF's password, once it opened
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Change details", objectName="CardTitle"))

        # a locked PDF: the password first
        self.lock_box = QWidget()
        ll = QVBoxLayout(self.lock_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.lock_text = QLabel(document.LOCKED_TEXT, objectName="PropertyValue")
        self.lock_text.setWordWrap(True)
        ll.addWidget(self.lock_text)
        row = QHBoxLayout()
        self.password_box = QLineEdit(objectName="document_password")
        self.password_box.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_box.setAccessibleName("Password")
        self.password_box.setPlaceholderText("Password")
        self.password_box.returnPressed.connect(self._unlock)
        self.open_button = QPushButton("Open", objectName="Primary")
        self.open_button.setToolTip("Open the PDF with this password (it isn't stored)")
        self.open_button.clicked.connect(self._unlock)
        row.addWidget(self.password_box, 1)
        row.addWidget(self.open_button)
        ll.addLayout(row)
        lay.addWidget(self.lock_box)

        self.fields_box = QWidget()
        fl = QVBoxLayout(self.fields_box)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 110)
        self.edits: dict[str, QLineEdit] = {}
        for r, name in enumerate(FIELDS):
            box = QLineEdit(objectName=f"document_{name}")
            box.setAccessibleName(LABELS[name])
            box.setClearButtonEnabled(True)
            box.setMaxLength(document.MAX_CHARS)
            box.textEdited.connect(self._changed)
            self.edits[name] = box
            grid.addWidget(QLabel(LABELS[name], objectName="Muted"), r, 0)
            grid.addWidget(box, r, 1)
        fl.addLayout(grid)
        self.signed = QLabel(document.SIGNED_TEXT, objectName="Muted")
        self.signed.setWordWrap(True)
        fl.addWidget(self.signed)
        self.apply_all = QCheckBox()
        self.apply_all.toggled.connect(lambda _on: self.reload())
        fl.addWidget(self.apply_all)
        lay.addWidget(self.fields_box)

        self.note = QLabel(objectName="Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = QPushButton("Save as copy", objectName="Secondary")
        self.save_button = QPushButton("Save changes", objectName="Primary")
        self.copy_button.setToolTip("Keep the original as it is; save a changed copy next to it")
        self.save_button.setToolTip("Write the changes into the original file")
        self.copy_button.clicked.connect(lambda: self._save(as_copy=True))
        self.save_button.clicked.connect(lambda: self._save(as_copy=False))
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button)
        lay.addLayout(buttons)
        self._one: DocumentDetails | None = None
        self._all: list[DocumentDetails] = []
        self._locked = False
        self._show_locked(False)
        self._changed()

    # ------------------------------------------------------------------ filling
    def set_details(self, current: DocumentDetails, everyone: list[DocumentDetails]) -> None:
        """The shown document's details, and every changeable document's ("Apply to all")."""
        self._one = current
        self._all = everyone
        self._show_locked(False)
        self.apply_all.setText(f"Apply to all {len(everyone)} documents")
        self.apply_all.setVisible(len(everyone) > 1)
        if len(everyone) < 2:
            self.apply_all.setChecked(False)
        self.reload()

    def set_locked(self, message: str) -> None:
        """The shown PDF needs its password (``message`` says whether one was wrong)."""
        self._one, self._all = None, []
        self.password = ""
        self.lock_text.setText(message)
        self._show_locked(True)
        self.note.clear()
        self._changed()

    def _show_locked(self, locked: bool) -> None:
        self._locked = locked
        self.lock_box.setVisible(locked)
        self.fields_box.setVisible(not locked)
        self.copy_button.setVisible(not locked)
        self.save_button.setVisible(not locked)
        if locked:
            self.password_box.clear()
            self.password_box.setFocus()

    def _shown(self) -> list[DocumentDetails]:
        many = self.apply_all.isChecked() and len(self._all) > 1
        return self._all if many else ([self._one] if self._one else [])

    def reload(self) -> None:
        details = self._shown()
        if not details:
            return
        self._loading = True
        self._start = document.common(details)
        for name, box in self.edits.items():
            value = self._start[name]
            box.setText(value or "")
            box.setPlaceholderText(SEVERAL if value is None else HINTS.get(name, ""))
        self._loading = False
        self.signed.setVisible(any(d.signed for d in details))
        self.note.clear()
        self._changed()

    def _unlock(self) -> None:
        text = self.password_box.text()
        if text:
            self.password = text
            self.unlock_requested.emit(text)

    # ------------------------------------------------------------------ editing
    def edit(self) -> DocumentEdit:
        values = {}
        for name, box in self.edits.items():
            start = self._start.get(name)
            text = box.text()
            if start is None and text == "":
                continue  # "Several values" left alone
            if text.strip() != (start or "").strip():
                values[name] = text
        return DocumentEdit(values, self.password)

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        dirty = not self._locked and bool(self.edit().values)
        self.save_button.setEnabled(dirty)
        self.copy_button.setEnabled(dirty)

    def _save(self, as_copy: bool) -> None:
        edit = self.edit()
        try:
            document.clean_values(edit.values)
        except document.DocumentError as exc:
            self.show_message(str(exc), error=True)
            return
        many = self.apply_all.isChecked() and len(self._all) > 1
        self.save_requested.emit(edit, as_copy, many)

    def show_message(self, text: str, error: bool = False) -> None:
        self.note.setObjectName("Error" if error else "Muted")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self.note.setText(text)

    def set_busy(self, busy: bool) -> None:
        for w in (*self.edits.values(), self.apply_all, self.save_button, self.copy_button,
                  self.password_box, self.open_button):  # fmt: skip
            w.setEnabled(not busy)
        if not busy:
            self._changed()
