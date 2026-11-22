"""The subtitle editor page: a table of subtitles, an edit box for the chosen one, an optional
video preview with the subtitle over the picture, find & replace, shift / sync, undo.

The document is ``umc.subedit`` (immutable cues + snapshot undo). Reading and writing files run
on a QThread (results come back through bound-method slots, queued onto the GUI thread)."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.codeedit import search
from umc.converters.subtitles import CHARSETS
from umc.core.detect import SUBTITLE_EXTS, VIDEO_EXTS
from umc.core.filemode import match_mode
from umc.gui import theme
from umc.gui.subtitleeditor.cue_model import CueModel
from umc.gui.subtitleeditor.dialogs import ShiftDialog, SyncDialog, parse_ms
from umc.gui.subtitleeditor.preview import VideoPreview
from umc.gui.subtitleeditor.shortcuts import SHORTCUTS
from umc.gui.widgets.find_bar import FindBar
from umc.subedit import edits, formats, io
from umc.subedit.checks import Problem, check
from umc.subedit.formats import SubDoc, SubtitleError, clock
from umc.subedit.history import History

log = logging.getLogger(__name__)

FORMAT_NAMES = {"srt": "SRT", "vtt": "WebVTT", "ass": "ASS", "ssa": "SSA", "lrc": "LRC",
                "smi": "SAMI"}  # fmt: skip
ROW_HEIGHT = 24


class UserError(Exception):
    """A problem to show as it is."""


def _read(path: Path, charset: str | None) -> tuple[Path, io.Opened]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserError(f"“{path.name}” couldn't be opened: {exc.strerror or exc}") from exc
    ext = path.suffix.lower().lstrip(".")
    try:
        return path, io.open_bytes(data, ext, charset)
    except SubtitleError as exc:
        raise UserError(f"“{path.name}”: {exc}") from exc


def _write(path: Path, data: bytes, fmt: str) -> tuple[Path, io.Opened]:
    """Temp file in the same folder + os.replace, so a failed save never leaves half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".umc-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        match_mode(tmp, path)  # mkstemp's file is private: keep who may read it
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise UserError(f"“{path.name}” couldn't be saved: {exc.strerror or exc}") from exc
    return path, io.open_bytes(data, fmt)


def qt_pos(text: str, i: int) -> int:
    """Python index → Qt cursor position (Qt counts UTF-16 units: an emoji is two)."""
    return len(text[:i].encode("utf-16-le")) // 2


def py_pos(text: str, q: int) -> int:
    """Qt cursor position → Python index."""
    n = 0
    for i, ch in enumerate(text):
        if n >= q:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


class _Worker(QObject):
    done = Signal(object, object)  # job, result
    failed = Signal(object, str)  # job, message

    def __init__(self, job: dict, fn: Callable[[], object]) -> None:
        super().__init__()
        self.job, self.fn = job, fn

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn()
        except UserError as exc:
            self.failed.emit(self.job, str(exc))
        except Exception:
            log.exception("subtitle editor job failed")
            self.failed.emit(self.job, "Something went wrong. The details are in the log.")
        else:
            self.done.emit(self.job, result)


class _TimeBox(QLineEdit):
    """A typed time: Enter or leaving sends ``committed`` when it changed; Esc puts it back.
    Keys stay with the box (no page shortcut fires while typing) except Ctrl+S."""

    committed = Signal(str)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("TimeBox")
        self.setAccessibleName(name)
        self.setToolTip(f"{name} — type a time like 1:05.250 and press Enter")
        self.setMinimumWidth(110)  # the stylesheet's font is set later: room for 0:00:00.000
        self._shown = ""
        self.editingFinished.connect(self._finish)

    def show_time(self, text: str) -> None:
        self._shown = text
        if not (self.hasFocus() and self.isModified()):
            self.setText(text)
            self.setModified(False)

    def _finish(self) -> None:
        if self.text().strip() != self._shown:
            self.committed.emit(self.text())
        self.setModified(False)

    def event(self, e) -> bool:
        override = e.type() == QEvent.Type.ShortcutOverride
        if override and not e.matches(QKeySequence.StandardKey.Save):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.setText(self._shown)
            self.setModified(False)
            self.clearFocus()
            return
        super().keyPressEvent(e)


class _CueText(QPlainTextEdit):
    """The subtitle's text. Undo / redo are the page's (one history for everything), so the
    box lets those keys through to the page's shortcuts instead of taking them itself."""

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ShortcutOverride and (
            e.matches(QKeySequence.StandardKey.Undo) or e.matches(QKeySequence.StandardKey.Redo)
        ):
            e.ignore()
            return True
        return super().event(e)


class ShortcutsWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(16)
        row, group = 0, None
        for s in SHORTCUTS:
            if s.group != group:
                group = s.group
                grid.addWidget(QLabel(group, objectName="Section"), row, 0, 1, 2)
                row += 1
            grid.addWidget(QLabel(" / ".join(s.keys), objectName="ShortcutKey"), row, 0)
            grid.addWidget(QLabel(s.label), row, 1)
            row += 1
        grid.addWidget(QLabel("Space (in the list)", objectName="ShortcutKey"), row, 0)
        grid.addWidget(QLabel("Play / pause the video"), row, 1)


class SubtitleEditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubtitleEditorPage")
        self.opened: io.Opened = io.new_document("srt")
        self.history = History(self.opened.doc)
        self.path: Path | None = None  # where Save writes (None: ask first)
        self.source: Path | None = None  # the file opened (shown as the title)
        self._utf8_note = False
        self.problems: list[Problem] = []
        self._jobs: dict[int, tuple[QThread, _Worker]] = {}
        self._next_job = 0
        self._loading_box = False
        self._row = -1  # the cue shown in the edit box
        self._box_row = -1  # the cue whose text the box holds (its caret means something)
        self.actions: dict[str, Callable[[], object]] = {}

        self.model = CueModel(self)
        self.table = QTableView()
        self.table.setObjectName("SubtitleTable")
        self.table.setAccessibleName("Subtitles")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setStretchLastSection(True)
        fm = self.table.fontMetrics()
        widths = ("000000", "0:00:00.0000", "0:00:00.0000", "000.0000")
        for col, sample in enumerate(widths):  # fixed: measuring 100 000 rows would be slow
            head.resizeSection(col, fm.horizontalAdvance(sample) + 24)
        self.table.selectionModel().currentRowChanged.connect(self._current_changed)
        self.table.selectionModel().selectionChanged.connect(lambda *_a: self._update_buttons())
        self.table.doubleClicked.connect(lambda _i: self.text_box.setFocus())
        self.table.installEventFilter(self)

        self.find_bar = FindBar()
        self.find_bar.find_next.connect(lambda: self.find(1))
        self.find_bar.find_prev.connect(lambda: self.find(-1))
        self.find_bar.replace_one.connect(self.replace_one)
        self.find_bar.replace_all.connect(self.replace_all)
        self.find_bar.changed.connect(self._count_matches)

        self.preview = VideoPreview()
        self.preview.hide()
        self.preview.position_changed.connect(self._play_moved)
        self.preview.closed.connect(self._video_closed)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self.table, 1)
        ll.addWidget(self._build_edit_panel())
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self.find_bar)
        root.addWidget(self.splitter, 1)
        root.addWidget(self._build_status_bar())

        self._check_timer = QTimer(self, singleShot=True, interval=300)
        self._check_timer.timeout.connect(self.run_check)
        self._install_shortcuts()
        theme.notifier().changed.connect(self._retint)
        self._show_doc(reset=True)

    # ------------------------------------------------------------------ layout
    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorTopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        def tb(text: str, tip: str, fn, name: str = "Small") -> QPushButton:
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False: fn())
            lay.addWidget(b)
            return b

        self.back_button = tb(
            "← Back to Convert", "Leave the editor", self.back_requested.emit, "Link"
        )
        lay.addSpacing(12)
        tb("Open…", "Open subtitles (Ctrl+O)", self.choose_files)
        tb("New", "New subtitles (Ctrl+N)", self.open_empty)
        self.title = QLabel()
        lay.addSpacing(8)
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.video_button = tb("Video…", "Show a video next to the subtitles", self.choose_video)
        self.shift_button = tb("Shift times…", "Move subtitles earlier or later", self.ask_shift)
        self.sync_button = tb(
            "Sync…", "Fix subtitles that drift: give two subtitles their right times",
            self.ask_sync,
        )  # fmt: skip
        tb("Find", "Find and replace (Ctrl+F, Ctrl+H)", lambda: self.find_bar.open(False))
        tb("?", "Keyboard shortcuts (F1)", self.show_shortcuts)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", self.save_as)
        self.save_button = tb("Save", "Save (Ctrl+S)", self.save, "Primary")
        return bar

    def _build_edit_panel(self) -> QWidget:
        panel = QFrame(objectName="SubtitleEditPanel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)

        def b(text: str, tip: str, fn) -> QPushButton:
            btn = QPushButton(text, objectName="Small")
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c=False: fn())
            row.addWidget(btn)
            return btn

        self.add_button = b("+ Add", "Add a subtitle after this one (Ctrl+Enter)", self.add_cue)
        self.delete_button = b("Delete", "Delete the chosen subtitles (Alt+Delete)", self.delete)
        self.split_button = b("Split", "Split at the cursor, or in the middle (Alt+S)", self.split)
        self.split_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keeps the text box's caret
        self.join_button = b("Join", "Join the chosen subtitles, or with the next (Alt+J)",
                             self.join)  # fmt: skip
        self.sort_button = b("Sort by time", "Put the subtitles in time order", self.sort)
        row.addStretch(1)
        self.start_box = _TimeBox("Start")
        self.end_box = _TimeBox("End")
        self.length_box = _TimeBox("Length")
        self.start_box.committed.connect(lambda t: self._typed_time("start", t))
        self.end_box.committed.connect(lambda t: self._typed_time("end", t))
        self.length_box.committed.connect(lambda t: self._typed_time("length", t))
        self.set_start_button = b("⇤", "Start = the video's position (Ctrl+[)",
                                  lambda: self.set_from_video("start"))  # fmt: skip
        self.set_start_button.setAccessibleName("Start from the video")
        for label, box in (("Start", self.start_box), ("End", self.end_box)):
            row.addWidget(QLabel(label, objectName="Muted"))
            row.addWidget(box)
        self.set_end_button = b("⇥", "End = the video's position (Ctrl+])",
                                lambda: self.set_from_video("end"))  # fmt: skip
        self.set_end_button.setAccessibleName("End from the video")
        row.addWidget(QLabel("Length", objectName="Muted"))
        row.addWidget(self.length_box)
        lay.addLayout(row)
        self.text_box = _CueText()
        self.text_box.setObjectName("SubtitleText")
        self.text_box.setAccessibleName("Subtitle text")
        self.text_box.setPlaceholderText("The subtitle's text (Enter starts a new line)")
        self.text_box.setUndoRedoEnabled(False)  # Ctrl+Z is the page's undo
        self.text_box.setFixedHeight(84)
        self.text_box.textChanged.connect(self._text_typed)
        lay.addWidget(self.text_box)
        self.note = QLabel(objectName="Error")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        return panel

    def _build_status_bar(self) -> QWidget:
        status = QFrame(objectName="EditorStatus")
        lay = QHBoxLayout(status)
        lay.setContentsMargins(14, 4, 14, 4)
        self.message = QLabel(objectName="Muted")
        self.counts = QLabel(objectName="Muted")
        self.problem_button = QPushButton(objectName="Link")
        self.problem_button.setToolTip("Go to the next subtitle with a problem (F8)")
        self.problem_button.clicked.connect(self.next_problem)
        self.format_label = QLabel(objectName="Muted")
        self.encoding_button = QToolButton()
        self.encoding_button.setToolTip(
            "Garbled letters? Open the file again with another encoding"
        )
        self.encoding_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.encoding_button)
        menu.addAction("Detect again (UTF-8 / Windows-1252)", lambda: self.reopen(None))
        menu.addSeparator()
        for code, label in CHARSETS:
            menu.addAction(label, lambda c=code: self.reopen(c))
        self.encoding_button.setMenu(menu)
        lay.addWidget(self.message, 1)
        for w in (self.counts, self.problem_button, self.format_label, self.encoding_button):
            lay.addWidget(w)
        return status

    def _install_shortcuts(self) -> None:
        self.actions = {
            "new": self.open_empty,
            "open": self.choose_files,
            "save": self.save,
            "save_as": self.save_as,
            "undo": self.undo,
            "redo": self.redo,
            "add": self.add_cue,
            "delete": self.delete,
            "split": self.split,
            "join": self.join,
            "prev": lambda: self.step(-1),
            "next": lambda: self.step(1),
            "next_problem": self.next_problem,
            "find": lambda: self.find_bar.open(False),
            "replace": lambda: self.find_bar.open(True),
            "find_next": lambda: self.find(1),
            "find_prev": lambda: self.find(-1),
            "play": self.preview.toggle,
            "set_start": lambda: self.set_from_video("start"),
            "set_end": lambda: self.set_from_video("end"),
            "help": self.show_shortcuts,
        }
        for s in SHORTCUTS:
            for key in s.keys:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda f=self.actions[s.id]: f())

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.table and e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Space and not e.modifiers():
                self.preview.toggle()
                return True
            if e.key() == Qt.Key.Key_Delete and not e.modifiers():
                self.delete()
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers():
                self.text_box.setFocus()
                return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------------ questions (tests replace)
    def ask(self, title: str, text: str, buttons: tuple[str, ...]) -> str:
        names = {
            "save": ("Save", QMessageBox.ButtonRole.AcceptRole),
            "discard": ("Don't save", QMessageBox.ButtonRole.DestructiveRole),
            "cancel": ("Cancel", QMessageBox.ButtonRole.RejectRole),
            "yes": ("Yes", QMessageBox.ButtonRole.YesRole),
            "no": ("No", QMessageBox.ButtonRole.NoRole),
        }
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        made = {box.addButton(names[b][0], names[b][1]): b for b in buttons}
        box.setDefaultButton(next(iter(made)))
        box.exec()
        return made.get(box.clickedButton(), "cancel" if "cancel" in buttons else "no")

    def choose_save_path(self, suggested: Path, fmts: tuple[str, ...]) -> Path | None:
        filters = ";;".join(f"{FORMAT_NAMES[f]} subtitles (*.{f})" for f in fmts)
        path, chosen = QFileDialog.getSaveFileName(self, "Save as", str(suggested), filters)
        if not path:
            return None
        p = Path(path)
        if p.suffix.lower().lstrip(".") not in fmts:  # no / another extension: the chosen filter's
            ext = next((f for f in fmts if f"*.{f})" in chosen), fmts[0])
            p = p.with_name(p.name + "." + ext)
        return p

    def choose_open_paths(self) -> list[Path]:
        exts = " ".join(f"*.{e}" for e in sorted(SUBTITLE_EXTS))
        vids = " ".join(f"*.{e}" for e in sorted(VIDEO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open subtitles", "", f"Subtitles and videos ({exts} {vids});;All files (*)"
        )
        return [Path(p) for p in paths]

    def choose_video_path(self) -> Path | None:
        vids = " ".join(f"*.{e}" for e in sorted(VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Choose a video", "", f"Videos ({vids})")
        return Path(path) if path else None

    # ------------------------------------------------------------------ state
    @property
    def doc(self) -> SubDoc:
        return self.history.doc

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    @property
    def dirty(self) -> bool:
        return self.history.dirty

    def chosen_rows(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows and 0 <= self._row < len(self.doc.cues):
            rows = [self._row]
        return rows

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _retint(self) -> None:  # a bound method: dropped with the page
        self.model.retint()

    # ------------------------------------------------------------------ jobs
    def _run(self, job: dict, fn: Callable[[], object]) -> None:
        job["id"] = self._next_job
        self._next_job += 1
        thread = QThread(self)
        worker = _Worker(job, fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._job_finished)
        self._jobs[job["id"]] = (thread, worker)
        thread.start()
        self._update_buttons()

    @Slot()
    def _job_finished(self) -> None:
        for key, (thread, _worker) in list(self._jobs.items()):
            if thread.isFinished():
                del self._jobs[key]
                thread.deleteLater()
        self._update_buttons()

    @Slot(object, object)
    def _job_done(self, job: dict, result: object) -> None:
        if job["kind"] == "open":
            path, opened = result
            self._load(path, opened)
        else:
            self._saved(job["doc"], job["fmt"], *result)

    @Slot(object, str)
    def _job_failed(self, job: dict, message: str) -> None:
        self.show_message(message, error=True)

    def wait_idle(self) -> None:
        while self._jobs:
            for thread, _worker in list(self._jobs.values()):
                thread.wait(50)
            QApplication.processEvents()

    # ------------------------------------------------------------------ opening
    def choose_files(self) -> None:
        paths = self.choose_open_paths()
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths: list[Path], video: Path | None = None) -> bool:
        """Open the first subtitle file in ``paths`` (a video among them, or ``video``, goes
        to the preview). Asks about unsaved changes first; False if nothing was started."""
        paths = [Path(p) for p in paths]
        sub = next((p for p in paths if p.suffix.lower().lstrip(".") in SUBTITLE_EXTS), None)
        video = video or next(
            (p for p in paths if p.suffix.lower().lstrip(".") in VIDEO_EXTS), None
        )
        if sub is None and video is None:
            self.show_message("Choose a subtitle file (SRT, VTT, ASS, SSA, SMI or LRC).", True)
            return False
        if sub is not None:
            if self.busy or not self._discard_ok():
                return False
            self._run({"kind": "open"}, lambda: _read(sub, None))
        if video is not None:
            self.open_video(video)
        return True

    def open_empty(self) -> bool:
        if self.busy or not self._discard_ok():
            return False
        self._load(None, io.new_document("srt"))
        return True

    def reopen(self, charset: str | None) -> None:
        """Read the file again with ``charset`` (None: detect). Edits are lost (asked first)."""
        if self.source is None or self.busy or not self._discard_ok():
            return
        path = self.source
        self._run({"kind": "open"}, lambda: _read(path, charset))

    def _discard_ok(self) -> bool:
        if not self.dirty:
            return True
        answer = self.ask(
            "Unsaved changes", "Save the changes to the subtitles first?",
            ("save", "discard", "cancel"),
        )  # fmt: skip
        if answer == "discard":
            return True
        if answer == "save" and self.save():
            self.wait_idle()
            return not self.dirty
        return False

    def _load(self, path: Path | None, opened: io.Opened) -> None:
        self.opened = opened
        writable = opened.doc.format in formats.WRITE_FORMATS
        self.path = path if writable else None
        self.source = path
        self.history = History(opened.doc)
        self._row = -1
        self._show_doc(reset=True)
        if opened.doc.cues:
            self.select_row(0)
        n = len(opened.doc.cues)
        name = path.name if path else "New subtitles"
        note = f"Opened “{name}”: {n} subtitle{'s' if n != 1 else ''}." if path else ""
        if path is not None and opened.doc.format not in formats.WRITE_FORMATS:
            note += " SAMI files can't be written: Save asks for another format."
        if opened.charset:
            note += " It will be saved as UTF-8."
        self.show_message(note)

    # ------------------------------------------------------------------ the video
    def choose_video(self) -> None:
        path = self.choose_video_path()
        if path is not None:
            self.open_video(path)

    def open_video(self, path: Path) -> None:
        self.preview.open_video(Path(path))
        self._play_moved(self.preview.position())
        self._update_buttons()

    def _video_closed(self) -> None:
        self.preview.hide()
        self.model.set_playing(set())
        self._update_buttons()

    def _play_moved(self, ms: int) -> None:
        rows = edits.cue_at(self.doc.cues, ms)
        self.model.set_playing(set(rows))
        self.preview.set_caption([self.doc.cues[r].text for r in rows], self.doc.format)

    def set_from_video(self, which: str) -> None:
        if not self.preview.has_video or not 0 <= self._row < len(self.doc.cues):
            return
        ms = self.preview.position()
        if which == "start":
            self._push(edits.set_times(self.doc.cues, self._row, start=ms), "Set start")
        else:
            self._push(edits.set_times(self.doc.cues, self._row, end=ms), "Set end")

    # ------------------------------------------------------------------ showing the document
    def _push(self, cues: tuple, label: str, merge: object = None, row: int | None = None,
              select: list[int] | None = None) -> None:  # fmt: skip
        """Make ``cues`` the document (one undo step) and show it; ``row`` = the cue to show
        in the edit box afterwards, ``select`` = rows to choose."""
        new = SubDoc(self.doc.format, cues, self.doc.header, self.doc.footer, self.doc.fields)
        before = len(self.doc.cues)
        self.history.push(new, label, merge)
        self._show_doc(reset=len(cues) != before, row=row, select=select)

    def _show_doc(self, reset: bool = False, row: int | None = None,
                  select: list[int] | None = None) -> None:  # fmt: skip
        doc = self.doc
        keep = row if row is not None else self._row
        reset = self.model.set_cues(doc.cues, doc.format) or reset
        if reset or select is not None:
            self.table.clearSelection()
        n = len(doc.cues)
        if n:
            target = min(max(keep, 0), n - 1)
            if reset or row is not None or select is not None:
                self.select_row(target, select)
            else:
                self._fill_box()
        else:
            self._row = -1
            self._fill_box()
        self._check_timer.start()
        self._play_moved(self.preview.position())
        self._update_title()
        self._update_buttons()

    def select_row(self, row: int, also: list[int] | None = None) -> None:
        if not 0 <= row < len(self.doc.cues):
            return
        sm = self.table.selectionModel()
        index = self.model.index(row, 0)
        flags = sm.SelectionFlag.ClearAndSelect | sm.SelectionFlag.Rows
        sm.setCurrentIndex(index, flags)
        for r in also or []:
            if r != row and 0 <= r < len(self.doc.cues):
                sm.select(self.model.index(r, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
        self.table.scrollTo(index)
        if self._row != row:
            self._current_changed(index)
        else:
            self._fill_box()

    def _current_changed(self, index, _previous=None) -> None:
        row = index.row() if index is not None and index.isValid() else -1
        if row != self._row:
            self.history.end_merge()
        self._row = row
        self._fill_box()
        if 0 <= row < len(self.doc.cues) and self.preview.has_video and not self.preview.playing():
            self.preview.seek(self.doc.cues[row].start)
        self._update_buttons()

    def _fill_box(self) -> None:
        cues = self.doc.cues
        has = 0 <= self._row < len(cues)
        c = cues[self._row] if has else None
        text = c.text if c else ""
        same = self._box_row == self._row
        if self.text_box.toPlainText() != text or not same:
            self._loading_box = True
            pos = self.text_box.textCursor().position()
            self.text_box.setPlainText(text)
            cursor = self.text_box.textCursor()
            end = qt_pos(text, len(text))
            cursor.setPosition(min(pos, end) if same else end)  # another cue: caret at the end
            self.text_box.setTextCursor(cursor)
            self._loading_box = False
        self._box_row = self._row
        self.text_box.setEnabled(has)
        for box, value in (
            (self.start_box, c.start if c else None),
            (self.end_box, c.end if c else None),
            (self.length_box, (c.end - c.start) if c else None),
        ):
            box.setEnabled(has)
            box.show_time(clock(max(0, value)) if value is not None else "")
        self._show_note()

    def _show_note(self) -> None:
        notes = [p.message for p in self.problems if p.index == self._row]
        self.note.setText(" · ".join(notes))
        self.note.setVisible(bool(notes))

    def _update_title(self) -> None:
        name = self.source.name if self.source else "New subtitles"
        self.title.setText(("● " if self.dirty else "") + name)
        fmt = self.doc.format
        self.format_label.setText(FORMAT_NAMES.get(fmt, fmt.upper()))
        f = self.opened.file
        enc = self.opened.charset or f.encoding
        self.encoding_button.setText(enc.upper() + (" BOM" if f.bom else ""))
        self.encoding_button.setVisible(self.source is not None)

    def _update_buttons(self) -> None:
        has = 0 <= self._row < len(self.doc.cues)
        chosen = len(self.chosen_rows())
        many = len(self.doc.cues)
        idle = not self.busy
        self.delete_button.setEnabled(has)
        self.split_button.setEnabled(has)
        self.join_button.setEnabled(has and (chosen > 1 or self._row + 1 < many))
        self.sort_button.setVisible(not edits.is_sorted(self.doc.cues))
        video = self.preview.has_video
        self.set_start_button.setEnabled(has and video)
        self.set_end_button.setEnabled(has and video)
        self.shift_button.setEnabled(many > 0)
        self.sync_button.setEnabled(many > 1)
        self.save_button.setEnabled(idle)
        self.save_as_button.setEnabled(idle)
        self.video_button.setText("Change video…" if video else "Video…")
        if video and self.preview.isHidden():
            self.preview.show()

    def run_check(self) -> None:
        self.problems = check(self.doc.cues, self.doc.format)
        self.model.set_problems(self.problems)
        n = len(self.doc.cues)
        rows = len({p.index for p in self.problems})
        self.counts.setText(f"{n} subtitle{'s' if n != 1 else ''}")
        self.problem_button.setText(f"{rows} with problems" if rows else "")
        self.problem_button.setVisible(bool(rows))
        self._show_note()

    # ------------------------------------------------------------------ editing
    def _text_typed(self) -> None:
        if self._loading_box or not 0 <= self._row < len(self.doc.cues):
            return
        text = self.text_box.toPlainText()
        if text == self.doc.cues[self._row].text:
            return
        self._push(edits.set_text(self.doc.cues, self._row, text), "Edit text",
                   merge=f"text:{self._row}")  # fmt: skip

    def _typed_time(self, which: str, text: str) -> None:
        if not 0 <= self._row < len(self.doc.cues):
            return
        ms = parse_ms(text)
        if ms is None:
            self.show_message("Type a time like 1:05.250 or 65.25.", error=True)
            return
        cues = self.doc.cues
        if which == "start":
            new = edits.set_times(cues, self._row, start=ms)
        elif which == "end":
            new = edits.set_times(cues, self._row, end=ms)
        else:
            new = edits.set_length(cues, self._row, ms)
        self.show_message("")
        self._push(new, "Change time")
        self._fill_box()

    def add_cue(self) -> None:
        after = self._row if self.doc.cues else -1
        cues, at = edits.add(self.doc.cues, after)
        self._push(cues, "Add subtitle", row=at)
        self.text_box.setFocus()

    def delete(self) -> None:
        rows = self.chosen_rows()
        if not rows:
            return
        self._push(edits.delete(self.doc.cues, rows), "Delete", row=rows[0])

    def split(self) -> None:
        if not 0 <= self._row < len(self.doc.cues):
            return
        # the caret counts even after a click on Split (the button takes no focus)
        at = py_pos(self.doc.cues[self._row].text, self.text_box.textCursor().position())
        self._push(edits.split(self.doc.cues, self._row, at), "Split", row=self._row,
                   select=[self._row, self._row + 1])  # fmt: skip

    def join(self) -> None:
        rows = self.chosen_rows()
        if len(rows) == 1 and rows[0] + 1 < len(self.doc.cues):
            rows = [rows[0], rows[0] + 1]
        if len(rows) < 2:
            return
        cues, at = edits.join(self.doc.cues, rows)
        self._push(cues, "Join", row=at)

    def sort(self) -> None:
        current = self.doc.cues[self._row] if 0 <= self._row < len(self.doc.cues) else None
        cues = edits.sort(self.doc.cues)
        row = next((i for i, c in enumerate(cues) if c is current), 0)
        self._push(cues, "Sort by time", row=row)

    def shift_times(self, delta: int, chosen_only: bool) -> None:
        rows = self.chosen_rows() if chosen_only else None
        self._push(edits.shift(self.doc.cues, delta, rows), "Shift times", select=rows)

    def sync_times(self, a: int, a_time: int, b: int, b_time: int) -> bool:
        try:
            cues = edits.sync(self.doc.cues, a, a_time, b, b_time)
        except ValueError as exc:
            why = {
                "same": "Those two subtitles start at the same time: pick two further apart.",
                "order": "The second subtitle must start after the first.",
            }[str(exc)]
            self.show_message(why, error=True)
            return False
        self._push(cues, "Sync")
        self.show_message("Synced: every subtitle was moved and stretched.")
        return True

    def ask_shift(self) -> None:
        dialog = ShiftDialog(self, len(self.chosen_rows()))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            delta, only = dialog.values()
            if delta:
                self.shift_times(delta, only)

    def ask_sync(self) -> None:
        cues = self.doc.cues
        if len(cues) < 2:
            return
        rows = self.chosen_rows()
        first, last = (rows[0], rows[-1]) if len(rows) > 1 else (0, len(cues) - 1)
        play = self.preview.position if self.preview.has_video else None
        dialog = SyncDialog(self, cues, first, last, play)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.values():
            self.sync_times(*dialog.values())

    def undo(self) -> None:
        label = self.history.undo()
        if label:
            self._show_doc(reset=True)
            self.show_message(f"Undid: {label}")

    def redo(self) -> None:
        label = self.history.redo()
        if label:
            self._show_doc(reset=True)
            self.show_message(f"Redid: {label}")

    def step(self, delta: int) -> None:
        n = len(self.doc.cues)
        if n:
            self.select_row(min(max(self._row + delta, 0), n - 1))

    def next_problem(self) -> None:
        rows = sorted({p.index for p in self.problems})
        if rows:
            self.select_row(next((r for r in rows if r > self._row), rows[0]))

    # ------------------------------------------------------------------ find
    def _matches(self) -> list[tuple[int, int, int]] | None:
        o = self.find_bar.options()
        try:
            return edits.find(self.doc.cues, self.find_bar.query.text(), **o)
        except search.SearchError as exc:
            self.find_bar.count.setText(str(exc))
            return None

    def _count_matches(self) -> None:
        found = self._matches()
        if found is not None:
            q = self.find_bar.query.text()
            self.find_bar.count.setText(f"{len(found)} found" if q else "")

    def find(self, direction: int) -> bool:
        """Go to the next (1) / previous (-1) match after the cursor; False if there is none."""
        found = self._matches()
        if not found:
            return False
        cur = self.text_box.textCursor()
        here = self.text_box.toPlainText()
        at = (self._row, py_pos(here, cur.selectionStart()))  # the match shown now is skipped
        if direction > 0:
            after = [m for m in found if (m[0], m[1]) > at]
            hit = after[0] if after else found[0]
        else:
            before = [m for m in found if (m[0], m[1]) < at]
            hit = before[-1] if before else found[-1]
        row, a, b = hit
        self.select_row(row)
        text = self.doc.cues[row].text
        cursor = self.text_box.textCursor()
        cursor.setPosition(qt_pos(text, a))
        cursor.setPosition(qt_pos(text, b), cursor.MoveMode.KeepAnchor)
        self.text_box.setTextCursor(cursor)
        self.find_bar.count.setText(f"{found.index(hit) + 1} of {len(found)}")
        return True

    def replace_one(self) -> None:
        if not 0 <= self._row < len(self.doc.cues):
            return
        cur = self.text_box.textCursor()
        text = self.doc.cues[self._row].text
        start, end = py_pos(text, cur.selectionStart()), py_pos(text, cur.selectionEnd())
        o = self.find_bar.options()
        try:
            hit = search.replacement_at(
                text, self.find_bar.query.text(), self.find_bar.replacement.text(), start, **o,
            )  # fmt: skip
        except search.SearchError as exc:
            self.find_bar.count.setText(str(exc))
            return
        if hit is not None and hit[1] == end:
            a, b, new = hit
            changed = text[:a] + new + text[b:]
            self._push(edits.set_text(self.doc.cues, self._row, changed), "Replace")
            cursor = self.text_box.textCursor()
            cursor.setPosition(qt_pos(changed, a + len(new)))
            self.text_box.setTextCursor(cursor)
        self.find(1)
        self._count_matches()

    def replace_all(self) -> None:
        o = self.find_bar.options()
        try:
            cues, n = edits.replace_all(
                self.doc.cues, self.find_bar.query.text(), self.find_bar.replacement.text(), **o
            )
        except search.SearchError as exc:
            self.find_bar.count.setText(str(exc))
            return
        if n:
            self._push(cues, "Replace all")
        self.find_bar.count.setText(f"Replaced {n}")

    # ------------------------------------------------------------------ saving
    def save(self) -> bool:
        """Start saving; False if nothing was started (busy, or Save as was cancelled)."""
        if self.busy:
            return False
        if self.path is None:
            return self.save_as()
        return self._start_save(self.path, self.doc.format)

    def save_as(self) -> bool:
        if self.busy:
            return False
        fmts = formats.save_formats(self.doc)
        if self.source is not None:
            suggested = self.source.with_suffix("." + fmts[0])
        else:
            docs = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            suggested = Path(docs or Path.home()) / f"Subtitles.{fmts[0]}"
        path = self.choose_save_path(suggested, fmts)
        if path is None:
            return False
        fmt = path.suffix.lower().lstrip(".")
        if fmt not in fmts:
            self.show_message(f"Subtitles can't be saved as .{fmt} here.", error=True)
            return False
        lost = formats.lost_parts(self.doc, fmt)
        if lost:
            answer = self.ask(
                "Some parts will be left out",
                f"{FORMAT_NAMES[fmt]} files can't keep: {', '.join(lost)}. Save anyway?",
                ("yes", "no"),
            )
            if answer != "yes":
                return False
        return self._start_save(path, fmt)

    def _start_save(self, path: Path, fmt: str) -> bool:
        doc = self.doc
        try:
            saved = io.to_bytes(self.opened, doc, fmt)
        except SubtitleError as exc:
            self.show_message(str(exc), error=True)
            return False
        self._utf8_note = saved.utf8
        self._run({"kind": "save", "doc": doc, "fmt": fmt},
                  lambda: _write(path, saved.data, fmt))  # fmt: skip
        return True

    def _saved(self, doc: SubDoc, fmt: str, path: Path, opened: io.Opened) -> None:
        note = " (as UTF-8)" if self._utf8_note else ""
        if fmt != doc.format:  # now editing the new file: its own markup and fields
            later = self.history.doc
            self._load(path, opened)
            if later != doc:  # typed on while it saved: those edits come along, unsaved
                moved = formats.parse(formats.write(later, fmt), fmt)
                self._push(moved.cues, "Edits made while saving")
            self.show_message(f"Saved “{path.name}”{note}. Now editing the .{fmt} file.")
            return
        self.opened = opened
        self.path = self.source = path
        self.history.mark_saved(doc)  # typed on while it saved: those changes stay unsaved
        self._update_title()
        self.show_message(f"Saved “{path.name}”{note}.")

    def maybe_leave(self) -> bool:
        """True when the editor may close: nothing unsaved, or saved / discarded on request."""
        self.wait_idle()
        if not self._discard_ok():
            return False
        self.history.mark_saved()  # "Don't save": not asked again (like the website)
        self.preview.pause()
        return True

    def show_shortcuts(self) -> None:
        ShortcutsWindow(self).exec()

    def shutdown(self) -> None:
        self.wait_idle()
        self.preview.close_video(quiet=True)
