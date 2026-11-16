"""The "Name and date" card: rename the shown file and change its "modified" date (desktop
only — a browser can't change either). Shown for every file, under its "Change details" card.
``save_requested(name, modified)`` carries only what changed ("" = unchanged); the panel renames
and reports back with ``show_message``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from umc.properties import fileinfo


class FileForm(QFrame):
    save_requested = Signal(str, str)  # new name or "", new modified date or ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="Card")
        self.setAccessibleName("Name and date")
        self._name = ""
        self._modified = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Name and date", objectName="CardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 110)
        self.name_box = QLineEdit(objectName="file_name")
        self.name_box.setAccessibleName("File name")
        self.name_box.setMaxLength(fileinfo.MAX_NAME + 20)
        self.name_box.textEdited.connect(self._changed)
        self.name_box.returnPressed.connect(self._save)
        self.modified_box = QLineEdit(objectName="file_modified")
        self.modified_box.setAccessibleName("Modified")
        self.modified_box.setPlaceholderText("2026-11-15 14:30")
        self.modified_box.textEdited.connect(self._changed)
        self.modified_box.returnPressed.connect(self._save)
        self.now_button = QPushButton("Now", objectName="Secondary")
        self.now_button.setToolTip("Set the modified date to this moment")
        self.now_button.clicked.connect(self._now)
        date_row = QHBoxLayout()
        date_row.setSpacing(6)
        date_row.addWidget(self.modified_box, 1)
        date_row.addWidget(self.now_button)
        grid.addWidget(QLabel("Name", objectName="Muted"), 0, 0)
        grid.addWidget(self.name_box, 0, 1)
        grid.addWidget(QLabel("Modified", objectName="Muted"), 1, 0)
        grid.addLayout(date_row, 1, 1)
        lay.addLayout(grid)
        self.note = QLabel(objectName="Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("Save changes", objectName="Primary")
        self.save_button.setToolTip("Change the file's name and modified date on your disk")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        lay.addLayout(buttons)
        self._changed()

    def set_file(self, path: Path) -> None:
        self._name = path.name
        try:
            self._modified = fileinfo.shown_modified(path)
        except OSError:
            self._modified = ""
        self.name_box.setText(self._name)
        self.modified_box.setText(self._modified)
        self.note.clear()
        self._changed()

    def _now(self) -> None:
        from datetime import datetime

        self.modified_box.setText(f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        self._changed()

    def edit(self) -> tuple[str, str]:
        """(name, date) the user changed; "" for each left as it was."""
        name = self.name_box.text().strip()
        date = self.modified_box.text().strip()
        return (name if name != self._name else "", date if date != self._modified else "")

    def _changed(self, *_args) -> None:
        self.save_button.setEnabled(any(self.edit()))

    def _save(self) -> None:
        name, date = self.edit()
        if name or date:
            self.save_requested.emit(name, date)

    def show_message(self, text: str, error: bool = False) -> None:
        self.note.setObjectName("Error" if error else "Muted")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self.note.setText(text)

    def set_busy(self, busy: bool) -> None:
        for w in (self.name_box, self.modified_box, self.now_button, self.save_button):
            w.setEnabled(not busy)
        if not busy:
            self._changed()
