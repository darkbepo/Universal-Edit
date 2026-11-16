"""The right column in Properties mode: "About this file" for one of the added files.

A file picker on top when there are several; the sections (``properties.read.describe``) are read
off the UI thread — PDFs on the PyMuPDF thread — and the newest request wins. JPG / PNG / WebP
pictures get the "Change details" card ``photo_form.PhotoForm``, MP3 / M4A / FLAC / OGG / Opus
songs ``music_form.MusicForm``, MP4 / MOV / M4V / MKV / WebM videos ``video_form.VideoForm``,
PDF / Office / OpenDocument files ``document_form.DocumentForm`` (a locked PDF asks for its
password first; it's kept in memory for this window only); saving runs on a thread too, after
asking once before it changes an original (videos report progress and can be cancelled).
Every file also gets ``file_form.FileForm`` ("Name and date": rename, modified date)."""

from __future__ import annotations

import struct
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import DetectedFile
from umc.core.errors import ConversionCancelled
from umc.gui import icons, theme
from umc.gui.properties.document_form import DocumentForm
from umc.gui.properties.file_form import FileForm
from umc.gui.properties.music_form import MusicForm
from umc.gui.properties.photo_form import PhotoForm
from umc.gui.properties.video_form import VideoForm
from umc.gui.viewer.tasks import TaskRunner
from umc.properties import document, fileinfo, music, photo, video
from umc.properties.info import Section, build
from umc.properties.read import NOTE, describe, file_facts, uses_pdf_engine

ASK_KEY = "properties/ask_before_changing"

# kind → (can it change this extension, read the form's values, save, its error, one, many)
KINDS = {
    "photo": (photo.can_change, lambda p: photo.read_details(p.read_bytes()), photo.save_file,
              photo.PhotoError, "picture", "pictures"),
    "music": (music.can_change, music.read_details, music.save_file, music.MusicError,
              "song", "songs"),
    "video": (video.can_change, video.read_details, video.save_file, video.VideoError,
              "video", "videos"),
    "document": (lambda ext: document.kind_of(ext) is not None, document.read_details,
                 document.save_file, document.DocumentError, "document", "documents"),
}  # fmt: skip
INSIDE = {"photo": "picture", "music": "sound", "video": "video and its sound",
          "document": "text and its pages"}  # fmt: skip


def kind_of(f: DetectedFile | None) -> str | None:
    """Which "Change details" card a file gets (None: read-only)."""
    if f is None:
        return None
    return next((k for k, spec in KINDS.items() if spec[0](f.ext)), None)


def _read(f: DetectedFile, others: list[DetectedFile], passwords: dict[Path, str] | None = None):
    """Runs on a thread: the sections, plus the details form's values for changeable files.
    A locked PDF that is the shown file gives its ``document.PasswordNeeded`` as the values."""
    sections = describe(f)
    kind = kind_of(f)
    if kind is None:
        return sections, None, None, []
    read = KINDS[kind][1]
    passwords = passwords or {}
    everyone = []  # (file, details) of every file of this kind whose details could be read
    current = None
    for g in others:
        try:
            d = read(g.path, passwords[g.path]) if g.path in passwords else read(g.path)
        except document.PasswordNeeded as exc:
            if g == f:
                current = exc
            continue
        except (OSError, ValueError, struct.error):
            continue
        everyone.append((g, d))
        if g == f:
            current = d
    return sections, kind, current, everyone


LABEL_W = 110


class PropertiesPanel(QWidget):
    files_saved = Signal(list)  # [Path] changed in place (the queue refreshes their thumbnails)
    file_renamed = Signal(object, object)  # old Path, new Path (the queue swaps the entry)
    _save_progress = Signal(str, float)  # kind, 0..1 (from the saving thread)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesPanel")
        self.tasks = TaskRunner(self)
        self.files: list[DetectedFile] = []
        self.current: DetectedFile | None = None
        self.sections: list[Section] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.heading = QLabel("About this file", objectName="PaneTitle")
        head.addWidget(self.icon)
        head.addWidget(self.heading)
        head.addStretch(1)
        self.picker = QComboBox(objectName="PropertiesFile")
        self.picker.setAccessibleName("File")
        self.picker.setMinimumContentsLength(18)
        self.picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.picker.currentIndexChanged.connect(self._picked)
        head.addWidget(self.picker)
        lay.addLayout(head)
        self.status = QLabel(objectName="Muted")
        lay.addWidget(self.status)
        self.scroll = QScrollArea(objectName="PropertiesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget(objectName="PropertiesBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 4, 0)
        self.body_lay.setSpacing(10)
        self._keep_message: tuple[str, bool, str] | None = None
        self._readable: list[DetectedFile] = []
        self.kind: str | None = None  # whose card is shown
        self.form = PhotoForm()
        self.music_form = MusicForm()
        self.video_form = VideoForm()
        self.document_form = DocumentForm()
        self.forms = {"photo": self.form, "music": self.music_form, "video": self.video_form,
                      "document": self.document_form}  # fmt: skip
        self.passwords: dict[Path, str] = {}  # PDFs opened with a password (this window only)
        self.document_form.unlock_requested.connect(self._unlock)
        self._cancel = threading.Event()
        self.video_form.cancel_requested.connect(self._cancel.set)
        self._save_progress.connect(self._show_progress)
        for form in self.forms.values():
            form.setVisible(False)
            form.save_requested.connect(self._save)
            self.body_lay.addWidget(form)
        self.file_form = FileForm()
        self.file_form.setVisible(False)
        self.file_form.save_requested.connect(self._rename)
        self.body_lay.addWidget(self.file_form)
        self._saving = 0  # details saves running (no rename meanwhile)
        self.body_lay.addStretch(1)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)
        self.retint()

    def retint(self) -> None:
        self.icon.setPixmap(icons.pixmap("info-circle", 18, theme.palette()["accent"]))

    # ------------------------------------------------------------------ files
    def set_files(self, files: list[DetectedFile]) -> None:
        """Keep showing the same file when it's still there (a file added or removed)."""
        keep = self.current
        self.files = list(files)
        self.picker.blockSignals(True)
        self.picker.clear()
        for f in self.files:
            self.picker.addItem(f.name)
        self.picker.setVisible(len(self.files) > 1)
        index = self.files.index(keep) if keep in self.files else 0
        self.picker.setCurrentIndex(index if self.files else -1)
        self.picker.blockSignals(False)
        if not self.files:
            self.current = None
            self._show([])
            self.status.clear()
            return
        if self.files[index] != self.current or not self.sections:
            self.show_file(index)

    def _picked(self, index: int) -> None:
        if 0 <= index < len(self.files):
            self.show_file(index)

    def show_file(self, index: int) -> None:
        f = self.files[index]
        self.current = f
        if self.picker.currentIndex() != index:
            self.picker.blockSignals(True)
            self.picker.setCurrentIndex(index)
            self.picker.blockSignals(False)
        self.status.setText(f"Reading {f.name}…")
        self.status.setVisible(True)
        for form in (*self.forms.values(), self.file_form):  # still the last file's cards
            form.setEnabled(False)
        others = self.changeable()
        passwords = dict(self.passwords)
        pdf_lane = uses_pdf_engine(f) or kind_of(f) == "document"  # PyMuPDF reads the PDFs
        self.tasks.submit(
            "describe",
            lambda: _read(f, others, passwords),
            self._loaded,
            self._failed,
            lane="pdf" if pdf_lane else "pool",
        )

    def _unlock(self, password: str) -> None:
        """Read the shown PDF again with ``password`` (a wrong one shows the box again)."""
        if self.current is not None:
            self.passwords[self.current.path] = password
            self.show_file(self.files.index(self.current))

    def changeable(self) -> list[DetectedFile]:
        """The added files whose details can be changed with the shown file's card."""
        kind = kind_of(self.current)
        return [g for g in self.files if kind is not None and kind_of(g) == kind]

    def active_form(self) -> PhotoForm | MusicForm | VideoForm | DocumentForm:
        return self.forms.get(self.kind or "", self.form)

    def _loaded(self, result) -> None:
        sections, kind, current, readable = result
        self._readable = [g for g, _d in readable]
        everyone = [d for _g, d in readable]
        self.status.clear()
        self.status.setVisible(False)
        self._show(sections)
        self._show_file_form()
        self.kind = kind if current is not None else None
        for k, form in self.forms.items():
            form.setVisible(k == self.kind)
            form.setEnabled(True)
        if isinstance(current, document.PasswordNeeded):
            if self.current is not None:
                self.passwords.pop(self.current.path, None)  # it didn't open the file
            self.document_form.set_locked(str(current))
        elif current is not None:
            self.active_form().set_details(current, everyone)
        if self._keep_message is not None:  # after a save: say how it went, on that card
            message, error, saved_kind = self._keep_message
            if self.kind is not None and self.kind == saved_kind:
                self.active_form().show_message(message, error)
            self._keep_message = None

    def _failed(self, message: str) -> None:
        """(``describe`` itself never fails; this is for the unexpected.) Show what the file is,
        so a later refresh doesn't read it again and again."""
        self.status.clear()
        self.status.setVisible(False)
        f = self.current
        facts = {"file": file_facts(f) if f else {}, "note": message or NOTE}
        self._show(build(facts))
        self._show_file_form()
        self.kind = None
        for form in self.forms.values():
            form.setVisible(False)
            form.setEnabled(True)

    # ------------------------------------------------------------------ drawing
    def _show(self, sections: list[Section]) -> None:
        self.sections = sections
        # the layout holds: the details forms, the name and date card, the cards, a stretch
        first = len(self.forms) + 1
        while self.body_lay.count() > first + 1:
            item = self.body_lay.takeAt(first)
            if item.widget() is not None:
                item.widget().deleteLater()
        for i, s in enumerate(sections):
            self.body_lay.insertWidget(first + i, self._card(s))

    @staticmethod
    def _card(s: Section) -> QFrame:
        card = QFrame(objectName="Card")
        card.setAccessibleName(s.title)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.setSpacing(6)
        cl.addWidget(QLabel(s.title, objectName="CardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, LABEL_W)  # values line up from card to card
        for row, (label, value) in enumerate(s.rows):
            name = QLabel(label, objectName="Muted")
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            text = QLabel(value, objectName="PropertyValue")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if label:
                grid.addWidget(name, row, 0)
                grid.addWidget(text, row, 1)
            else:
                grid.addWidget(text, row, 0, 1, 2)
        cl.addLayout(grid)
        return card

    # ------------------------------------------------------------------ name and date
    def _show_file_form(self) -> None:
        f = self.current
        self.file_form.setVisible(f is not None)
        self.file_form.setEnabled(True)
        if f is not None:
            self.file_form.set_file(f.path)
            if self._keep_file_message is not None:
                self.file_form.show_message(*self._keep_file_message)
        self._keep_file_message = None

    _keep_file_message: tuple[str, bool] | None = None

    def _rename(self, name: str, date: str) -> None:
        """Rename the shown file and / or set its modified date (instant, so on this thread)."""
        f = self.current
        if f is None or not self.status.isHidden():
            return
        form = self.file_form
        if self._saving:
            form.show_message("Wait until the details are saved.", error=True)
            return
        try:
            new_name = fileinfo.clean_name(name, f.path) if name else None
            modified = fileinfo.parse_modified(date) if date else None
            target = fileinfo.apply(f.path, new_name, modified)
        except fileinfo.FileError as exc:
            form.show_message(str(exc), error=True)
            return
        done = []
        if target != f.path:
            done.append(f"renamed to “{target.name}”")
        if modified is not None:
            done.append("date changed")
        message = ("Saved — " + " and ".join(done) + ".") if done else "Saved."
        if target != f.path:
            new = DetectedFile(target, f.family, f.ext)
            if f.path in self.passwords:
                self.passwords[target] = self.passwords.pop(f.path)
            self.files = [new if g == f else g for g in self.files]
            self.current = new
            self.picker.setItemText(self.files.index(new), new.name)
            self.file_renamed.emit(f.path, target)
        self._keep_file_message = (message, False)
        self.show_file(self.files.index(self.current))

    # ------------------------------------------------------------------ saving
    def confirm_original(self, count: int) -> bool:
        """Asked before the first change to an original (can be switched off)."""
        settings = QSettings()
        if not settings.value(ASK_KEY, True, bool):
            return True
        one, many = self._nouns()
        what = f"this {one}" if count == 1 else f"these {count} {many}"
        inside = INSIDE.get(self.kind or "photo", "picture")
        box = QMessageBox(self)
        box.setWindowTitle("Change the original?")
        box.setText(f"This writes the new details into {what}.")
        box.setInformativeText(
            f"The {inside} itself isn't touched — only its details. To keep the original as it "
            "is, use Save as copy instead."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        dont_ask = QCheckBox("Don't ask again")
        box.setCheckBox(dont_ask)
        ok = box.exec() == QMessageBox.StandardButton.Save
        if ok and dont_ask.isChecked():
            settings.setValue(ASK_KEY, False)
        return ok

    def _nouns(self) -> tuple[str, str]:
        spec = KINDS.get(self.kind or "photo")
        return spec[4], spec[5]

    def _save(self, edit, as_copy: bool, every: bool) -> None:
        if not self.status.isHidden():  # a file is still being read: the card isn't its yet
            return
        # "Apply to all N songs" = the files the form counted (whose details could be read)
        targets = list(self._readable) if every else [self.current] if self.current else []
        if not targets or self.kind is None:
            return
        if not as_copy and not self.confirm_original(len(targets)):
            return
        paths = [t.path for t in targets]
        kind = self.kind
        form = self.active_form()
        form.set_busy(True)
        form.show_message("Saving…")
        self._saving += 1
        self._cancel = threading.Event()
        cancel = self._cancel

        def progress(fraction: float) -> None:
            self._save_progress.emit(kind, fraction)

        passwords = dict(self.passwords)
        # the results go to this card, even if another file (or kind) is shown by then
        self.tasks.submit(
            "save",
            lambda: _save_all(kind, paths, edit, as_copy, progress, cancel, passwords),
            lambda result: self._saved(result, kind),
            lambda m: self._save_failed(m, kind),
            exclusive=False,
            lane="pdf" if kind == "document" else "pool",
        )

    def _show_progress(self, kind: str, fraction: float) -> None:
        form = self.forms.get(kind)
        if isinstance(form, VideoForm):
            form.set_progress(fraction)

    def cancel_save(self) -> None:
        self._cancel.set()

    def _saved(self, result, kind: str) -> None:
        written, problems = result
        self._saving = max(0, self._saving - 1)
        form = self.forms[kind]
        form.set_busy(False)
        if problems:
            form.show_message(problems[0], error=True)
        elif any(p not in self._paths() for p in written):
            names = ", ".join(p.name for p in written[:3]) + ("…" if len(written) > 3 else "")
            form.show_message(f"Saved: {names}")
        else:
            n = len(written)
            form.show_message("Saved." if n == 1 else f"Saved {n} {KINDS[kind][5]}.")
        changed = [p for p in written if p in self._paths()]
        if changed:
            self.files_saved.emit(changed)
        if self.current is not None:  # show what's in the file now
            message = form.note.text()
            error = bool(problems)
            self.show_file(self.files.index(self.current))
            self._keep_message = (message, error, kind)

    def _save_failed(self, message: str, kind: str) -> None:
        self._saving = max(0, self._saving - 1)
        form = self.forms[kind]
        form.set_busy(False)
        form.show_message(message or "The details couldn't be saved.", error=True)

    def _paths(self) -> list[Path]:
        return [f.path for f in self.files]

    def rows(self) -> dict[str, dict[str, str]]:
        """{section: {label: value}} of what's shown (tests)."""
        return {s.title: dict(s.rows) for s in self.sections}

    def save_message(self) -> str:
        return self.active_form().note.text()

    def shutdown(self) -> None:
        self._cancel.set()  # a video being saved stops (the original stays as it was)
        self.tasks.shutdown()


def _save_all(
    kind: str,
    paths: list[Path],
    edit,
    as_copy: bool,
    progress=None,
    cancel: threading.Event | None = None,
    passwords: dict[Path, str] | None = None,
) -> tuple[list[Path], list[str]]:
    """Runs on a thread: save each file; plain-language problems are collected, not raised.
    Videos report ``progress`` (0..1 over all of them) and stop when ``cancel`` is set; locked
    PDFs are opened with their ``passwords``."""
    save, error = KINDS[kind][2], KINDS[kind][3]
    cancel = cancel or threading.Event()
    written, problems = [], []
    for i, path in enumerate(paths):
        if cancel.is_set():
            problems.append("Cancelled — the rest were left as they were.")
            break
        try:
            if kind == "video":

                def part(p: float, i: int = i) -> None:
                    if progress is not None:
                        progress((i + p) / len(paths))

                written.append(save(path, edit, as_copy, part, cancel))
            elif kind == "document":
                password = (passwords or {}).get(path, "")
                written.append(save(path, document.DocumentEdit(edit.values, password), as_copy))
            else:
                written.append(save(path, edit, as_copy))
        except ConversionCancelled:
            problems.append(
                "Cancelled — nothing was changed."
                if not written
                else "Cancelled — the rest were left as they were."
            )
            break
        except error as exc:
            problems.append(str(exc))
        except OSError:
            problems.append(f"“{path.name}” couldn't be saved.")
    return written, problems
