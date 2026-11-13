"""The "Change details" card for JPG / PNG / WebP pictures in the Properties panel.

Shows the picture's title, comments, author, copyright, tags, date taken and rotation; with
several pictures (and "Apply to all" on) a field that differs reads "Several values" and is only
changed when typed in. ``save_requested(edit, as_copy, all)`` — the panel does the saving."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from umc.properties import photo
from umc.properties.photo import FIELDS, PhotoDetails, PhotoEdit

LABELS = {
    "title": "Title",
    "comment": "Comments",
    "author": "Author",
    "copyright": "Copyright",
    "tags": "Tags",
    "taken": "Date taken",
}
HINTS = {
    "tags": "Separate with commas",
    "taken": "Like 2026-11-12 14:03",
}
SEVERAL = "Several values"
KEEP_ROTATION = 0  # combo data: leave each picture's rotation as it is


class PhotoForm(QFrame):
    save_requested = Signal(object, bool, bool)  # PhotoEdit, as a copy, every picture

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="Card")
        self.setAccessibleName("Change details")
        self._start: dict[str, str | None] = {}
        self._start_rotation = 1
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Change details", objectName="CardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 110)
        self.edits: dict[str, QLineEdit] = {}
        for row, name in enumerate(FIELDS):
            box = QLineEdit(objectName=f"photo_{name}")
            box.setAccessibleName(LABELS[name])
            box.setClearButtonEnabled(True)
            box.textEdited.connect(self._changed)
            self.edits[name] = box
            grid.addWidget(QLabel(LABELS[name], objectName="Muted"), row, 0)
            grid.addWidget(box, row, 1)
        self.rotation = QComboBox(objectName="photo_rotation")
        self.rotation.setAccessibleName("Rotation")
        self.rotation.currentIndexChanged.connect(self._changed)
        grid.addWidget(QLabel("Rotation", objectName="Muted"), len(FIELDS), 0)
        grid.addWidget(self.rotation, len(FIELDS), 1)
        lay.addLayout(grid)
        self.remove_location = QCheckBox("Remove the location")
        self.remove_all = QCheckBox("Remove all hidden details (camera, dates, location, …)")
        self.apply_all = QCheckBox()
        for box in (self.remove_location, self.remove_all, self.apply_all):
            box.toggled.connect(self._changed)
            lay.addWidget(box)
        self.apply_all.toggled.connect(lambda _on: self.reload())
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
        self._one: PhotoDetails | None = None
        self._all: list[PhotoDetails] = []
        self._changed()

    # ------------------------------------------------------------------ filling
    def set_details(self, current: PhotoDetails, everyone: list[PhotoDetails]) -> None:
        """The shown picture's details, and every changeable picture's (for "Apply to all")."""
        self._one = current
        self._all = everyone
        self.apply_all.setText(f"Apply to all {len(everyone)} pictures")
        self.apply_all.setVisible(len(everyone) > 1)
        if len(everyone) < 2:
            self.apply_all.setChecked(False)
        self.reload()

    def reload(self) -> None:
        many = self.apply_all.isChecked() and len(self._all) > 1
        details = self._all if many else ([self._one] if self._one else [])
        if not details:
            return
        self._loading = True
        self._start = photo.common(details)
        for name, box in self.edits.items():
            value = self._start[name]
            box.setText(value or "")
            box.setPlaceholderText(SEVERAL if value is None else HINTS.get(name, ""))
        self.rotation.clear()
        if many:
            self.rotation.addItem("Keep as they are", KEEP_ROTATION)
        for value, label in photo.ROTATIONS.items():
            self.rotation.addItem(label, value)
        self._start_rotation = KEEP_ROTATION if many else details[0].orientation
        i = self.rotation.findData(self._start_rotation)
        if i < 0:  # mirrored: show it, so it isn't changed by accident
            self.rotation.addItem(f"As it is ({details[0].orientation})", details[0].orientation)
            i = self.rotation.count() - 1
        self.rotation.setCurrentIndex(i)
        self.remove_location.setChecked(False)
        self.remove_location.setEnabled(any(d.has_location for d in details))
        self.remove_location.setToolTip(
            "" if self.remove_location.isEnabled() else "There's no location in the picture"
        )
        self.remove_all.setChecked(False)
        self._loading = False
        self.note.clear()
        self._changed()

    # ------------------------------------------------------------------ editing
    def edit(self) -> PhotoEdit:
        values = {}
        for name, box in self.edits.items():
            start = self._start.get(name)
            text = box.text()
            if start is None and text == "":
                continue  # "Several values" left alone
            if text.strip() != (start or "").strip():
                values[name] = text
        rot = self.rotation.currentData()
        orientation = None if rot in (None, KEEP_ROTATION, self._start_rotation) else int(rot)
        return PhotoEdit(
            values,
            orientation,
            self.remove_location.isChecked(),
            self.remove_all.isChecked(),
        )

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        dirty = not self.edit().is_empty()
        self.save_button.setEnabled(dirty)
        self.copy_button.setEnabled(dirty)
        for name in ("taken",):
            text = self.edits[name].text().strip()
            try:
                if text:
                    photo.exif_date(text)
                self.edits[name].setProperty("invalid", False)
            except photo.PhotoError:
                self.edits[name].setProperty("invalid", True)

    def _save(self, as_copy: bool) -> None:
        edit = self.edit()
        if edit.values.get("taken"):
            try:
                photo.exif_date(edit.values["taken"])
            except photo.PhotoError as exc:
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
        for w in (*self.edits.values(), self.rotation, self.remove_location, self.remove_all,
                  self.apply_all, self.save_button, self.copy_button):  # fmt: skip
            w.setEnabled(not busy)
        if not busy:
            self.reload_enabled()

    def reload_enabled(self) -> None:
        shown = self._all if self.apply_all.isChecked() else [self._one]
        self.remove_location.setEnabled(any(d.has_location for d in shown if d))
        self._changed()
