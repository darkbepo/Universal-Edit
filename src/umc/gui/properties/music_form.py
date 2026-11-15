"""The "Change details" card for MP3 / M4A / FLAC / OGG / Opus songs in the Properties panel.

Title, artist, album, album artist, track, disc, year, genre and the cover picture (keep, choose
a JPG / PNG, or remove). With several songs (and "Apply to all" on) a field that differs reads
"Several values" and is only changed when typed in. ``save_requested(edit, as_copy, all)`` —
the panel does the saving (the same rules as photo_form.PhotoForm)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from umc.properties import music
from umc.properties.music import FIELDS, MusicDetails, MusicEdit

LABELS = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "album_artist": "Album artist",
    "track": "Track",
    "disc": "Disc",
    "year": "Year",
    "genre": "Genre",
}
HINTS = {"track": "Like 3 or 3/12", "disc": "Like 1 or 1/2", "year": "Like 2026"}
SEVERAL = "Several values"


class MusicForm(QFrame):
    save_requested = Signal(object, bool, bool)  # MusicEdit, as a copy, every song

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="Card")
        self.setAccessibleName("Change details")
        self._start: dict[str, str | None] = {}
        self._loading = False
        self._cover: bytes | None = None
        self._cover_name = ""
        self._remove_cover = False
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
            box = QLineEdit(objectName=f"music_{name}")
            box.setAccessibleName(LABELS[name])
            box.setClearButtonEnabled(True)
            box.textEdited.connect(self._changed)
            self.edits[name] = box
            grid.addWidget(QLabel(LABELS[name], objectName="Muted"), row, 0)
            grid.addWidget(box, row, 1)
        cover_row = QHBoxLayout()
        cover_row.setSpacing(8)
        self.cover_status = QLabel(objectName="PropertyValue")
        self.cover_status.setWordWrap(True)
        self.cover_button = QPushButton("Choose picture…", objectName="Secondary")
        self.cover_button.setToolTip("Use a JPG or PNG picture as the cover")
        self.remove_cover_button = QPushButton("Remove", objectName="Secondary")
        self.remove_cover_button.setToolTip("Take the cover picture out")
        self.cover_button.clicked.connect(self._choose_cover)
        self.remove_cover_button.clicked.connect(self.remove_cover)
        cover_row.addWidget(self.cover_status, 1)
        cover_row.addWidget(self.cover_button)
        cover_row.addWidget(self.remove_cover_button)
        grid.addWidget(QLabel("Cover", objectName="Muted"), len(FIELDS), 0)
        grid.addLayout(cover_row, len(FIELDS), 1)
        lay.addLayout(grid)
        self.apply_all = QCheckBox()
        self.apply_all.toggled.connect(lambda _on: self.reload())
        lay.addWidget(self.apply_all)
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
        self._one: MusicDetails | None = None
        self._all: list[MusicDetails] = []
        self._changed()

    # ------------------------------------------------------------------ filling
    def set_details(self, current: MusicDetails, everyone: list[MusicDetails]) -> None:
        """The shown song's tags, and every changeable song's (for "Apply to all")."""
        self._one = current
        self._all = everyone
        self.apply_all.setText(f"Apply to all {len(everyone)} songs")
        self.apply_all.setVisible(len(everyone) > 1)
        if len(everyone) < 2:
            self.apply_all.setChecked(False)
        self.reload()

    def _shown(self) -> list[MusicDetails]:
        many = self.apply_all.isChecked() and len(self._all) > 1
        return self._all if many else ([self._one] if self._one else [])

    def reload(self) -> None:
        details = self._shown()
        if not details:
            return
        self._loading = True
        self._start = music.common(details)
        for name, box in self.edits.items():
            value = self._start[name]
            box.setText(value or "")
            box.setPlaceholderText(SEVERAL if value is None else HINTS.get(name, ""))
        self._cover, self._cover_name, self._remove_cover = None, "", False
        self._loading = False
        self.note.clear()
        self._show_cover()
        self._changed()

    def _show_cover(self) -> None:
        details = self._shown()
        if self._cover is not None:
            text = f"New cover: {self._cover_name}"
        elif self._remove_cover:
            text = "The cover will be removed"
        elif all(d.has_cover for d in details):
            text = "Has a cover picture" if len(details) == 1 else "All have a cover picture"
        elif any(d.has_cover for d in details):
            text = "Some have a cover picture"
        else:
            text = "No cover picture"
        self.cover_status.setText(text)
        has_one = self._cover is not None or (
            not self._remove_cover and any(d.has_cover for d in details)
        )
        self.remove_cover_button.setEnabled(has_one)

    # ------------------------------------------------------------------ cover
    def _choose_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a cover picture", "", "Pictures (*.jpg *.jpeg *.png)"
        )
        if path:
            self.set_cover_file(Path(path))

    def set_cover_file(self, path: Path) -> None:
        """Use the picture at ``path`` as the new cover (checked first)."""
        try:
            data = path.read_bytes()
            music.cover_mime(data)
        except music.MusicError as exc:
            self.show_message(str(exc), error=True)
            return
        except OSError:
            self.show_message(f"“{path.name}” couldn't be read.", error=True)
            return
        self._cover, self._cover_name, self._remove_cover = data, path.name, False
        self.note.clear()
        self._show_cover()
        self._changed()

    def remove_cover(self) -> None:
        self._cover, self._cover_name, self._remove_cover = None, "", True
        self._show_cover()
        self._changed()

    # ------------------------------------------------------------------ editing
    def edit(self) -> MusicEdit:
        values = {}
        for name, box in self.edits.items():
            start = self._start.get(name)
            text = box.text()
            if start is None and text == "":
                continue  # "Several values" left alone
            if text.strip() != (start or "").strip():
                values[name] = text
        return MusicEdit(values, self._cover, self._remove_cover and self._cover is None)

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        dirty = not self.edit().is_empty()
        self.save_button.setEnabled(dirty)
        self.copy_button.setEnabled(dirty)
        for name in ("track", "disc", "year"):
            try:
                music.clean_values({name: self.edits[name].text()})
                self.edits[name].setProperty("invalid", False)
            except music.MusicError:
                self.edits[name].setProperty("invalid", True)
            self.edits[name].style().unpolish(self.edits[name])
            self.edits[name].style().polish(self.edits[name])

    def _save(self, as_copy: bool) -> None:
        edit = self.edit()
        try:
            music.clean_values(edit.values)
        except music.MusicError as exc:
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
        for w in (*self.edits.values(), self.cover_button, self.remove_cover_button,
                  self.apply_all, self.save_button, self.copy_button):  # fmt: skip
            w.setEnabled(not busy)
        if not busy:
            self._show_cover()
            self._changed()
