"""The "Change details" card for MP4 / MOV / M4V / MKV / WebM videos in the Properties panel.

Title, description, date recorded, the rotation flag (MP4 / MOV / M4V) and — for one video —
each sound / subtitle track's language and which one plays by default. With several videos
(and "Apply to all" on) a field that differs reads "Several values" and is only changed when
typed in. Saving copies every track as it is (no re-encoding), so it's quick but a big video
still takes a moment: the card shows progress and a Cancel button. ``save_requested(edit,
as_copy, all)`` — the panel does the saving (the same rules as photo_form.PhotoForm)."""

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
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.properties import video
from umc.properties.video import VideoDetails, VideoEdit

SEVERAL = "Several values"
ROTATION_LABELS = {
    0: "Not turned",
    90: "Turned 90° clockwise",
    180: "Upside down",
    270: "Turned 90° anticlockwise",
}
KIND_LABELS = {"audio": "Sound", "subtitle": "Subtitles"}
NOT_SET = "Not set"
NONE = "None"


def track_label(track: video.Track, number: int) -> str:
    text = f"{KIND_LABELS[track.kind]} {number}"
    return f"{text} · {track.title}" if track.title else text


class VideoForm(QFrame):
    save_requested = Signal(object, bool, bool)  # VideoEdit, as a copy, every video
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="Card")
        self.setAccessibleName("Change details")
        self._start: dict[str, str | None] = {}
        self._start_tracks: dict[int, str] = {}
        self._start_defaults: dict[str, int | None] = {}
        self._loading = False
        self._one: VideoDetails | None = None
        self._all: list[VideoDetails] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Change details", objectName="CardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 110)
        self.title = QLineEdit(objectName="video_title")
        self.title.setAccessibleName("Title")
        self.title.setClearButtonEnabled(True)
        self.title.textEdited.connect(self._changed)
        self.description = QPlainTextEdit(objectName="video_description")
        self.description.setAccessibleName("Description")
        self.description.setTabChangesFocus(True)
        self.description.setFixedHeight(64)
        self.description.textChanged.connect(self._changed)
        self.date = QLineEdit(objectName="video_date")
        self.date.setAccessibleName("Date recorded")
        self.date.setClearButtonEnabled(True)
        self.date.setToolTip("Your computer's local time; it's stored as UTC in the file")
        self.date.textEdited.connect(self._changed)
        self.rotation = QComboBox(objectName="video_rotation")
        self.rotation.setAccessibleName("Rotation")
        self.rotation.currentIndexChanged.connect(self._changed)
        self.rotation_label = QLabel("Rotation", objectName="Muted")
        rows = [
            ("Title", self.title),
            ("Description", self.description),
            ("Date recorded", self.date),
            (None, self.rotation),
        ]
        for row, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label, objectName="Muted") if label else self.rotation_label,
                           row, 0)  # fmt: skip
            grid.addWidget(widget, row, 1)
        lay.addLayout(grid)
        # one video: the tracks
        self.tracks_box = QWidget(objectName="VideoTracks")
        self.tracks_grid = QGridLayout(self.tracks_box)
        self.tracks_grid.setContentsMargins(0, 4, 0, 0)
        self.tracks_grid.setHorizontalSpacing(14)
        self.tracks_grid.setVerticalSpacing(6)
        self.tracks_grid.setColumnStretch(1, 1)
        self.tracks_grid.setColumnMinimumWidth(0, 110)
        self.languages: dict[int, QComboBox] = {}
        self.defaults: dict[str, QComboBox] = {}
        lay.addWidget(self.tracks_box)
        self.apply_all = QCheckBox()
        self.apply_all.toggled.connect(lambda _on: self.reload())
        lay.addWidget(self.apply_all)
        self.lost = QLabel(objectName="Muted")
        self.lost.setWordWrap(True)
        lay.addWidget(self.lost)
        self.note = QLabel(objectName="Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.progress = QProgressBar(objectName="VideoSaveProgress")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", objectName="Secondary")
        self.cancel_button.setToolTip("Stop saving; the video stays as it was")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.setVisible(False)
        self.copy_button = QPushButton("Save as copy", objectName="Secondary")
        self.save_button = QPushButton("Save changes", objectName="Primary")
        self.copy_button.setToolTip("Keep the original as it is; save a changed copy next to it")
        self.save_button.setToolTip("Write the changes into the original file")
        self.copy_button.clicked.connect(lambda: self._save(as_copy=True))
        self.save_button.clicked.connect(lambda: self._save(as_copy=False))
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button)
        lay.addLayout(buttons)
        self._changed()

    # ------------------------------------------------------------------ filling
    def set_details(self, current: VideoDetails, everyone: list[VideoDetails]) -> None:
        """The shown video's details, and every changeable video's (for "Apply to all")."""
        self._one = current
        self._all = everyone
        self.apply_all.setText(f"Apply to all {len(everyone)} videos")
        self.apply_all.setVisible(len(everyone) > 1)
        if len(everyone) < 2:
            self.apply_all.setChecked(False)
        self.reload()

    def _many(self) -> bool:
        return self.apply_all.isChecked() and len(self._all) > 1

    def _shown(self) -> list[VideoDetails]:
        return self._all if self._many() else ([self._one] if self._one else [])

    def reload(self) -> None:
        details = self._shown()
        if not details:
            return
        self._loading = True
        common = video.common(details)
        self._start = dict(common)
        if common["date"]:
            self._start["date"] = video.shown_date(common["date"])
        for name, box in (("title", self.title), ("date", self.date)):
            value = self._start[name]
            box.setText(value or "")
            hint = "Like 2026-11-15 14:30" if name == "date" else ""
            box.setPlaceholderText(SEVERAL if value is None else hint)
        value = self._start["description"]
        self.description.setPlainText(value or "")
        self.description.setPlaceholderText(SEVERAL if value is None else "")
        self.rotation.clear()
        for turn, label in ROTATION_LABELS.items():
            self.rotation.addItem(label, turn)
        if common["rotation"] is None:
            self.rotation.insertItem(0, SEVERAL, None)
            self.rotation.setCurrentIndex(0)
        else:
            self.rotation.setCurrentIndex(self.rotation.findData(int(common["rotation"])))
        rotating = all(d.container in video.ROTATING_EXTS for d in details)
        self.rotation.setVisible(rotating)
        self.rotation_label.setVisible(rotating)
        self._fill_tracks(None if self._many() else details[0])
        lost = sorted({x for d in details for x in d.lost})
        self.lost.setText("Saving can't keep " + ", ".join(lost) + "." if lost else "")
        self.lost.setVisible(bool(lost))
        self._loading = False
        self.note.clear()
        self._changed()

    def _fill_tracks(self, one: VideoDetails | None) -> None:
        while self.tracks_grid.count():
            item = self.tracks_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.languages, self.defaults = {}, {}
        self._start_tracks, self._start_defaults = {}, {}
        tracks = one.tracks if one else []
        self.tracks_box.setVisible(bool(tracks))
        row = 0
        counts = {"audio": 0, "subtitle": 0}
        numbers: dict[int, int] = {}
        for t in tracks:
            counts[t.kind] += 1
            numbers[t.index] = counts[t.kind]
            box = QComboBox(objectName=f"video_language_{t.index}")
            label = track_label(t, counts[t.kind])
            box.setAccessibleName(f"{label} language")
            box.addItem(NOT_SET, "")
            for code, name in video.LANGUAGES:
                box.addItem(name, code)
            if t.language and box.findData(t.language) < 0:
                box.addItem(t.language, t.language)  # a code we don't list: keep it
            box.setCurrentIndex(box.findData(t.language))
            box.currentIndexChanged.connect(self._changed)
            self.languages[t.index] = box
            self._start_tracks[t.index] = t.language
            text = f"{label} ({t.codec})" if t.codec else label
            self.tracks_grid.addWidget(QLabel(text, objectName="Muted"), row, 0)
            self.tracks_grid.addWidget(box, row, 1)
            row += 1
        for kind in video.TRACK_KINDS:
            of_kind = [t for t in tracks if t.kind == kind]
            if not of_kind:
                continue
            marked = next((t.index for t in of_kind if t.default), None)
            box = QComboBox(objectName=f"video_default_{kind}")
            name = "Default sound" if kind == "audio" else "Default subtitles"
            box.setAccessibleName(name)
            if marked is None or video.allows_no_default(one.container if one else ""):
                box.addItem(NONE, None)
            for t in of_kind:
                box.addItem(track_label(t, numbers[t.index]), t.index)
            box.setCurrentIndex(box.findData(marked))
            box.currentIndexChanged.connect(self._changed)
            self.defaults[kind] = box
            self._start_defaults[kind] = marked
            self.tracks_grid.addWidget(QLabel(name, objectName="Muted"), row, 0)
            self.tracks_grid.addWidget(box, row, 1)
            row += 1

    # ------------------------------------------------------------------ editing
    def edit(self) -> VideoEdit:
        values = {}
        texts = {
            "title": self.title.text(),
            "description": self.description.toPlainText(),
            "date": self.date.text(),
        }
        for name, text in texts.items():
            start = self._start.get(name)
            if start is None and text == "":
                continue  # "Several values" left alone
            if text.strip() != (start or "").strip():
                values[name] = text
        turn = self.rotation.currentData()
        start_turn = self._start.get("rotation")
        rotating = not self.rotation.isHidden()  # MKV / WebM can't store one
        if rotating and turn is not None and (start_turn is None or int(start_turn) != turn):
            values["rotation"] = str(turn)
        languages = {
            index: box.currentData()
            for index, box in self.languages.items()
            if box.currentData() != self._start_tracks.get(index)
        }
        defaults = {
            kind: box.currentData()
            for kind, box in self.defaults.items()
            if box.currentData() != self._start_defaults.get(kind)
        }
        return VideoEdit(values, languages, defaults)

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        dirty = not self.edit().is_empty()
        self.save_button.setEnabled(dirty)
        self.copy_button.setEnabled(dirty)
        try:
            video.clean_values({"date": self.date.text()})
            self.date.setProperty("invalid", False)
        except video.VideoError:
            self.date.setProperty("invalid", True)
        self.date.style().unpolish(self.date)
        self.date.style().polish(self.date)

    def _save(self, as_copy: bool) -> None:
        edit = self.edit()
        try:
            video.clean_values(edit.values)
        except video.VideoError as exc:
            self.show_message(str(exc), error=True)
            return
        self.save_requested.emit(edit, as_copy, self._many())

    def show_message(self, text: str, error: bool = False) -> None:
        self.note.setObjectName("Error" if error else "Muted")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self.note.setText(text)

    def set_progress(self, fraction: float) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def set_busy(self, busy: bool) -> None:
        widgets = (self.title, self.description, self.date, self.rotation, self.apply_all,
                   self.save_button, self.copy_button, *self.languages.values(),
                   *self.defaults.values())  # fmt: skip
        for w in widgets:
            w.setEnabled(not busy)
        self.progress.setValue(0)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self._changed()
