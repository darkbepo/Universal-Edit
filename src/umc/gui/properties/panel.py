"""The right column in Properties mode: "About this file" for one of the added files.

A file picker on top when there are several; the sections (``properties.read.describe``) are read
off the UI thread — PDFs on the PyMuPDF thread — and the newest request wins. JPG / PNG / WebP
pictures also get the "Change details" card (``photo_form.PhotoForm``); saving runs on a thread
too, after asking once before it changes an original."""

from __future__ import annotations

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
from umc.gui import icons, theme
from umc.gui.properties.photo_form import PhotoForm
from umc.gui.viewer.tasks import TaskRunner
from umc.properties import photo
from umc.properties.info import Section, build
from umc.properties.photo import PhotoEdit
from umc.properties.read import NOTE, describe, file_facts, uses_pdf_engine

ASK_KEY = "properties/ask_before_changing"


def _read(f: DetectedFile, others: list[DetectedFile]):
    """Runs on a thread: the sections, plus the details form's values for changeable pictures."""
    sections = describe(f)
    if not photo.can_change(f.ext):
        return sections, None, []
    everyone = []
    current = None
    for g in others:
        try:
            d = photo.read_details(g.path.read_bytes())
        except (OSError, ValueError):
            continue
        everyone.append(d)
        if g == f:
            current = d
    return sections, current, everyone


LABEL_W = 110


class PropertiesPanel(QWidget):
    files_saved = Signal(list)  # [Path] changed in place (the queue refreshes their thumbnails)

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
        self._keep_message: tuple[str, bool] | None = None
        self.form = PhotoForm()
        self.form.setVisible(False)
        self.form.save_requested.connect(self._save)
        self.body_lay.addWidget(self.form)
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
        others = self.changeable()
        self.tasks.submit(
            "describe",
            lambda: _read(f, others),
            self._loaded,
            self._failed,
            lane="pdf" if uses_pdf_engine(f) else "pool",
        )

    def changeable(self) -> list[DetectedFile]:
        """The added pictures whose details can be changed."""
        return [g for g in self.files if photo.can_change(g.ext)]

    def _loaded(self, result) -> None:
        sections, current, everyone = result
        self.status.clear()
        self.status.setVisible(False)
        self._show(sections)
        self.form.setVisible(current is not None)
        if current is not None:
            self.form.set_details(current, everyone)
        if self._keep_message is not None:  # after a save: say how it went
            self.form.show_message(*self._keep_message)
            self._keep_message = None

    def _failed(self, message: str) -> None:
        """(``describe`` itself never fails; this is for the unexpected.) Show what the file is,
        so a later refresh doesn't read it again and again."""
        self.status.clear()
        self.status.setVisible(False)
        f = self.current
        facts = {"file": file_facts(f) if f else {}, "note": message or NOTE}
        self._show(build(facts))
        self.form.setVisible(False)

    # ------------------------------------------------------------------ drawing
    def _show(self, sections: list[Section]) -> None:
        self.sections = sections
        # the layout holds: the details form, the cards, a stretch
        while self.body_lay.count() > 2:
            item = self.body_lay.takeAt(1)
            if item.widget() is not None:
                item.widget().deleteLater()
        for i, s in enumerate(sections):
            self.body_lay.insertWidget(1 + i, self._card(s))

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

    # ------------------------------------------------------------------ saving
    def confirm_original(self, count: int) -> bool:
        """Asked before the first change to an original (can be switched off)."""
        settings = QSettings()
        if not settings.value(ASK_KEY, True, bool):
            return True
        what = "this picture" if count == 1 else f"these {count} pictures"
        box = QMessageBox(self)
        box.setWindowTitle("Change the original?")
        box.setText(f"This writes the new details into {what}.")
        box.setInformativeText(
            "The picture itself isn't touched — only its details. To keep the original as it "
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

    def _save(self, edit: PhotoEdit, as_copy: bool, every: bool) -> None:
        targets = self.changeable() if every else [self.current] if self.current else []
        if not targets:
            return
        if not as_copy and not self.confirm_original(len(targets)):
            return
        paths = [t.path for t in targets]
        self.form.set_busy(True)
        self.form.show_message("Saving…")
        self.tasks.submit(
            "save",
            lambda: _save_all(paths, edit, as_copy),
            self._saved,
            lambda m: self._save_failed(m),
            exclusive=False,
        )

    def _saved(self, result) -> None:
        written, problems = result
        self.form.set_busy(False)
        if problems:
            self.form.show_message(problems[0], error=True)
        elif any(p not in self._paths() for p in written):
            names = ", ".join(p.name for p in written[:3]) + ("…" if len(written) > 3 else "")
            self.form.show_message(f"Saved: {names}")
        else:
            n = len(written)
            self.form.show_message("Saved." if n == 1 else f"Saved {n} pictures.")
        changed = [p for p in written if p in self._paths()]
        if changed:
            self.files_saved.emit(changed)
        if self.current is not None:  # show what's in the file now
            message = self.form.note.text()
            error = bool(problems)
            self.show_file(self.files.index(self.current))
            self._keep_message = (message, error)

    def _save_failed(self, message: str) -> None:
        self.form.set_busy(False)
        self.form.show_message(message or "The details couldn't be saved.", error=True)

    def _paths(self) -> list[Path]:
        return [f.path for f in self.files]

    def rows(self) -> dict[str, dict[str, str]]:
        """{section: {label: value}} of what's shown (tests)."""
        return {s.title: dict(s.rows) for s in self.sections}

    def save_message(self) -> str:
        return self.form.note.text()

    def shutdown(self) -> None:
        self.tasks.shutdown()


def _save_all(paths: list[Path], edit: PhotoEdit, as_copy: bool) -> tuple[list[Path], list[str]]:
    """Runs on a thread: save each picture; plain-language problems are collected, not raised."""
    written, problems = [], []
    for path in paths:
        try:
            written.append(photo.save_file(path, edit, as_copy))
        except photo.PhotoError as exc:
            problems.append(str(exc))
        except OSError:
            problems.append(f"“{path.name}” couldn't be saved.")
    return written, problems
