"""The code editor page: a tab per file, byte-exact saving, find & replace, a status bar with the
position, language, encoding and line endings.

Reading and writing files run on a QThread (results come back through bound-method slots, which
Qt queues onto the GUI thread); colouring is chunked (``highlight.ChunkedHighlighter``)."""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextDocumentLayout,
    QPushButton,
    QSplitter,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.codeedit import editing
from umc.codeedit.check import CHECKED, Problem, check
from umc.codeedit.languages import LANGUAGES, language_for
from umc.codeedit.textfile import Encoding, Newline, TextFile, decode, encode
from umc.codeedit.tidy import TIDY, TidyError, tidy
from umc.gui.codeeditor.code_view import CodeView, raw_text
from umc.gui.codeeditor.find_bar import FindBar
from umc.gui.codeeditor.highlight import ChunkedHighlighter
from umc.gui.codeeditor.preview import MarkdownPane, html_preview_file, open_in_browser
from umc.gui.codeeditor.shortcuts import SHORTCUTS
from umc.gui.codeeditor.tab import CodeTab

log = logging.getLogger(__name__)

MAX_OPEN_BYTES = 50_000_000
MAX_HIGHLIGHT_BYTES = 2_000_000
MAX_CHECK_BYTES = 2_000_000  # bigger files are only checked when Tidy is pressed
PREVIEWS = frozenset({"html", "markdown"})
ENCODINGS: tuple[tuple[str, Encoding, bool], ...] = (
    ("UTF-8", "utf-8", False),
    ("UTF-8 with BOM", "utf-8", True),
    ("UTF-16 LE", "utf-16-le", True),
    ("UTF-16 BE", "utf-16-be", True),
    ("Windows-1252", "windows-1252", False),
)
NEWLINES: tuple[tuple[str, Newline], ...] = (
    ("Windows (CRLF)", "\r\n"),
    ("Unix (LF)", "\n"),
    ("Old Mac (CR)", "\r"),
)


class UserError(Exception):
    """A problem shown to the user as it is."""


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _read(path: Path) -> tuple[Path, TextFile, tuple[int, int] | None]:
    try:
        size = path.stat().st_size
        if size > MAX_OPEN_BYTES:
            raise UserError(
                f"“{path.name}” is too big for the code editor "
                f"({size / 1e6:.0f} MB; up to {MAX_OPEN_BYTES / 1e6:.0f} MB)."
            )
        data = path.read_bytes()
    except OSError as exc:
        raise UserError(f"“{path.name}” couldn't be read: {exc.strerror or exc}") from exc
    try:
        file = decode(data)
    except ValueError as exc:
        raise UserError(f"“{path.name}” looks like a binary file, not text.") from exc
    return path, file, _stamp(path)


def _read_or_message(path: Path) -> tuple[Path, TextFile, tuple[int, int] | None] | str:
    """``_read``, or the plain message when the file can't be opened (the others still are)."""
    try:
        return _read(path)
    except UserError as exc:
        return str(exc)


def _write(path: Path, data: bytes) -> tuple[Path, TextFile, tuple[int, int] | None]:
    """Write next to the target first, then swap it in (a crash never leaves half a file)."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".umc-save-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise UserError(f"“{path.name}” couldn't be saved: {exc.strerror or exc}") from exc
    return path, decode(data), _stamp(path)


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
            log.exception("code editor job failed")
            self.failed.emit(self.job, "Something went wrong. The details are in the log.")
        else:
            self.done.emit(self.job, result)


class ShortcutsWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(16)
        row = 0
        group = None
        for s in SHORTCUTS:
            if s.group != group:
                group = s.group
                grid.addWidget(QLabel(group, objectName="Section"), row, 0, 1, 2)
                row += 1
            grid.addWidget(QLabel(" / ".join(s.keys), objectName="ShortcutKey"), row, 0)
            grid.addWidget(QLabel(s.label), row, 1)
            row += 1


class CodeEditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CodeEditorPage")
        self._tabs: list[CodeTab] = []
        self._jobs: dict[int, tuple[QThread, _Worker]] = {}
        self._next_job = 0
        self._switching = False
        self.actions: dict[str, Callable[[], object]] = {}

        self.view = CodeView()
        self.view.setDocument(self._new_document(""))
        self.find_bar = FindBar(self.view)
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabMoved.connect(self._tab_moved)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self.tabs)
        root.addWidget(self.find_bar)
        self.preview_pane = MarkdownPane()
        self.preview_pane.setVisible(False)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.view)
        self.splitter.addWidget(self.preview_pane)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)
        root.addWidget(self._build_status_bar())

        self.problems: list[Problem] = []
        self._tidy_note = ""  # why Tidy refused, shown until the text changes
        self._check_timer = QTimer(self, singleShot=True, interval=400)
        self._check_timer.timeout.connect(self.run_check)
        self._preview_timer = QTimer(self, singleShot=True, interval=500)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self.view.cursor_moved.connect(self._show_position)
        self.view.viewport().setAcceptDrops(True)
        self.view.viewport().installEventFilter(self)
        self._install_shortcuts()
        self._update_status()

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
        tb("Open…", "Open files (Ctrl+O)", self.choose_files)
        tb("New", "New file (Ctrl+N)", self.open_empty)
        lay.addStretch(1)
        self.tidy_button = tb("Tidy", "Lay out the JSON or XML neatly (one undo step)", self.tidy)
        self.preview_button = tb("Preview", "Preview the HTML or Markdown", self.toggle_preview)
        tb("Find", "Find and replace (Ctrl+F, Ctrl+H)", lambda: self.find_bar.open(False))
        tb("?", "Keyboard shortcuts (F1)", self.show_shortcuts)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", self.save_as)
        self.save_button = tb("Save", "Save (Ctrl+S)", self.save, "Primary")
        return bar

    def _build_status_bar(self) -> QWidget:
        status = QFrame(objectName="EditorStatus")
        lay = QHBoxLayout(status)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(10)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.note = QLabel(objectName="Muted")
        self.note.setTextFormat(Qt.TextFormat.RichText)
        self.note.linkActivated.connect(self._note_link)
        self.position = QLabel(objectName="Muted")

        def menu_button(tip: str) -> QToolButton:
            b = QToolButton()
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            b.setMenu(QMenu(b))
            return b

        self.language_button = menu_button("Language (for colours and comments)")
        for lang in sorted(LANGUAGES, key=lambda lang: lang.name.lower()):
            act = QAction(lang.name, self.language_button)
            act.triggered.connect(lambda _c=False, i=lang.id: self.set_language(i))
            self.language_button.menu().addAction(act)
        self.encoding_button = menu_button("Encoding used when saving")
        for label, enc, bom in ENCODINGS:
            act = QAction(label, self.encoding_button)
            act.triggered.connect(lambda _c=False, e=enc, b=bom: self.set_encoding(e, b))
            self.encoding_button.menu().addAction(act)
        self.newline_button = menu_button("Line endings used when saving")
        for label, nl in NEWLINES:
            act = QAction(label, self.newline_button)
            act.triggered.connect(lambda _c=False, n=nl: self.set_newline(n))
            self.newline_button.menu().addAction(act)
        self.indent_label = QLabel(objectName="Muted")
        lay.addWidget(self.message, 1)
        lay.addWidget(self.note)
        for w in (
            self.position,
            self.language_button,
            self.encoding_button,
            self.newline_button,
            self.indent_label,
        ):
            lay.addWidget(w)
        return status

    def _install_shortcuts(self) -> None:
        self.actions = {
            "new": self.open_empty,
            "open": self.choose_files,
            "save": self.save,
            "save_as": self.save_as,
            "close": lambda: self.close_tab(self.tabs.currentIndex()),
            "next_tab": lambda: self._step_tab(1),
            "prev_tab": lambda: self._step_tab(-1),
            "find": lambda: self.find_bar.open(False),
            "replace": lambda: self.find_bar.open(True),
            "find_next": self.find_bar.find_next,
            "find_prev": self.find_bar.find_prev,
            "go_to_line": self.ask_go_to_line,
            "undo": self.view.undo,
            "redo": self.view.redo,
            "comment": self.view.toggle_comment,
            "wrap": self.toggle_wrap,
            "zoom_in": lambda: self.view.zoom(1),
            "zoom_out": lambda: self.view.zoom(-1),
            "zoom_reset": lambda: self.view.zoom(0),
            "help": self.show_shortcuts,
        }
        for s in SHORTCUTS:
            if not s.bound:
                continue
            for key in s.keys:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda f=self.actions[s.id]: f())

    # ------------------------------------------------------------------ questions
    def ask(self, title: str, text: str, buttons: tuple[str, ...]) -> str:
        """A question with the given answers ("save", "discard", "cancel", "yes", "no");
        returns the one clicked ("cancel" / "no" when the window is closed). Tests replace it."""
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

    def choose_save_path(self, suggested: Path) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self, "Save as", str(suggested))
        return Path(path) if path else None

    # ------------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    @property
    def current(self) -> CodeTab | None:
        i = self.tabs.currentIndex()
        return self._tabs[i] if 0 <= i < len(self._tabs) else None

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _new_document(self, text: str) -> QTextDocument:
        doc = QTextDocument(self)
        doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
        doc.setPlainText(text)
        doc.setModified(False)
        doc.setUndoRedoEnabled(True)
        return doc

    def _refresh_tab_title(self, tab: CodeTab) -> None:
        if tab in self._tabs:
            i = self._tabs.index(tab)
            self.tabs.setTabText(i, ("● " if tab.dirty else "") + tab.title)
            self.tabs.setTabToolTip(i, str(tab.path) if tab.path else "Not saved yet")

    def _show_position(self, line: int, column: int) -> None:
        self.position.setText(f"Ln {line}, Col {column}")

    def _update_status(self) -> None:
        tab = self.current
        has = tab is not None
        for w in (self.language_button, self.encoding_button, self.newline_button):
            w.setVisible(has)
        self.save_button.setEnabled(has and not self.busy)
        self.save_as_button.setEnabled(has and not self.busy)
        self.tidy_button.setVisible(has and tab.language_id in TIDY)
        self.preview_button.setVisible(has and tab.language_id in PREVIEWS)
        if not has:
            self.position.setText("")
            self.indent_label.setText("")
            self.note.setText("")
            return
        lang = next((lang for lang in LANGUAGES if lang.id == tab.language_id), None)
        self.language_button.setText(lang.name if lang else "Plain text")
        enc = next(
            (label for label, e, b in ENCODINGS if (e, b) == (tab.encoding, tab.bom)),
            tab.encoding,
        )
        self.encoding_button.setText(enc)
        self.newline_button.setText({"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[tab.newline])
        self.indent_label.setText("Tab" if tab.indent == "\t" else f"Spaces: {len(tab.indent)}")
        self._update_note()
        cursor = self.view.textCursor()
        self._show_position(cursor.blockNumber() + 1, cursor.positionInBlock() + 1)
        self._refresh_tab_title(tab)

    # ------------------------------------------------------------------ jobs
    def _run(self, job: dict, fn: Callable[[], object]) -> None:
        job["id"] = self._next_job
        self._next_job += 1
        thread = QThread(self)
        worker = _Worker(job, fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._job_done)  # bound methods: queued onto the GUI thread
        worker.failed.connect(self._job_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._job_finished)
        self._jobs[job["id"]] = (thread, worker)
        thread.start()
        self._update_status()

    @Slot()
    def _job_finished(self) -> None:
        for key, (thread, _worker) in list(self._jobs.items()):
            if thread.isFinished():
                del self._jobs[key]
                thread.deleteLater()
        self._update_status()

    @Slot(object, object)
    def _job_done(self, job: dict, result: object) -> None:
        if job["kind"] == "open":
            self._opened_all(result)
        else:
            self._saved(job["tab"], job["text"], *result)

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
        paths, _ = QFileDialog.getOpenFileNames(self, "Open files")
        if paths:
            self.open_paths([Path(p) for p in paths])

    def drop_paths(self, paths: list[Path]) -> None:
        self.open_paths(paths)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        dragging = event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove)
        if dragging and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return True
        if event.type() == QEvent.Type.Drop and event.mimeData().hasUrls():
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
            event.acceptProposedAction()
            self.open_paths(paths)
            return True
        return super().eventFilter(obj, event)

    def open_paths(self, paths: list[Path]) -> bool:
        """A tab per file (an open one is just shown). False if nothing could be started."""
        started = False
        to_read: list[Path] = []
        for path in paths:
            path = Path(path)
            existing = next((t for t in self._tabs if t.path and _same(t.path, path)), None)
            if existing is not None:
                self.tabs.setCurrentIndex(self._tabs.index(existing))
                started = True
            elif path.is_dir():
                self.show_message(f"“{path.name}” is a folder.", error=True)
            else:
                to_read.append(path)
        if to_read:
            # one job reads them in turn, so the tabs come in the order the files did
            self._run({"kind": "open"}, lambda: [_read_or_message(p) for p in to_read])
            self.show_message("")
            started = True
        return started

    def _opened_all(self, results: list) -> None:
        problems = [r for r in results if isinstance(r, str)]
        for r in results:
            if not isinstance(r, str):
                self._opened(*r)
        if problems:
            self.show_message(" ".join(problems), error=True)

    def _opened(self, path: Path, file: TextFile, stamp: tuple[int, int] | None) -> None:
        if any(t.path and _same(t.path, path) for t in self._tabs):
            return
        lang = language_for(path.name)
        tab = self._add_tab(path, file, file.text, lang.id)
        tab.encoding, tab.newline, tab.bom = file.encoding, file.newline, file.bom
        tab.indent = editing.detect_indent(file.text)
        tab.disk_stamp = stamp
        if len(file.raw) > MAX_HIGHLIGHT_BYTES:
            tab.colours = False
            tab.highlighter.set_enabled(False)
        self._drop_spare_untitled(keep=tab)
        self._select(tab)

    def open_empty(self) -> bool:
        tab = self._add_tab(None, None, "", "text")
        self._select(tab)
        self.view.setFocus()
        return True

    def _add_tab(self, path: Path | None, file: TextFile | None, text: str, lang: str) -> CodeTab:
        doc = self._new_document(text)
        tab = CodeTab(path, file, doc, ChunkedHighlighter(doc, lang), lang)
        doc.modificationChanged.connect(lambda _m, t=tab: self._refresh_tab_title(t))
        doc.contentsChanged.connect(self.find_bar.document_changed)
        doc.contentsChanged.connect(self._text_changed)
        self._tabs.append(tab)
        self.tabs.addTab(tab.title)
        return tab

    def _drop_spare_untitled(self, keep: CodeTab) -> None:
        """An empty, untouched Untitled tab gives way to the first real file."""
        for t in list(self._tabs):
            if t is not keep and t.path is None and not t.dirty and t.document.isEmpty():
                self._remove_tab(self._tabs.index(t))

    def _select(self, tab: CodeTab) -> None:
        self.tabs.setCurrentIndex(self._tabs.index(tab))
        self._tab_changed(self.tabs.currentIndex())

    def _tab_changed(self, index: int) -> None:
        tab = self.current
        if tab is None:
            self._update_status()
            return
        if self.view.document() is not tab.document:
            self.view.setDocument(tab.document)
        self.view.set_language(tab.language_id)
        self.view.set_indent_unit(tab.indent)
        self.find_bar.document_changed()
        self._tidy_note = ""
        self.run_check()
        if self.preview_pane.isVisible():
            if tab.language_id == "markdown":
                self._refresh_preview()
            else:
                self.preview_pane.setVisible(False)
        self._update_status()

    def _tab_moved(self, src: int, dst: int) -> None:
        self._tabs.insert(dst, self._tabs.pop(src))

    def _step_tab(self, step: int) -> None:
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def _remove_tab(self, index: int) -> None:
        tab = self._tabs.pop(index)
        self.tabs.removeTab(index)
        if self.view.document() is tab.document:
            self.view.setDocument(self._new_document(""))
        tab.highlighter.setDocument(None)
        tab.document.deleteLater()

    # ------------------------------------------------------------------ checks, Tidy, preview
    def _text_changed(self) -> None:
        tab = self.current
        if tab is None or self.sender() is not tab.document:
            return
        if self._tidy_note:
            self._tidy_note = ""
            self._update_note()
        self._check_timer.start()
        if self.preview_pane.isVisible():
            self._preview_timer.start()

    def run_check(self) -> None:
        """Check the current tab now (JSON / YAML / XML under ``MAX_CHECK_BYTES``)."""
        self._check_timer.stop()
        tab = self.current
        problems: list[Problem] = []
        if tab is not None and tab.language_id in CHECKED:
            text = raw_text(tab.document)
            if len(text) <= MAX_CHECK_BYTES:
                problems = check(tab.language_id, text)
        self.problems = problems
        self.view.set_problems(problems)
        self._update_note()

    def _update_note(self) -> None:
        tab = self.current
        if tab is None:
            self.note.setText("")
            return
        if self._tidy_note:
            self.note.setText(_html_escape(self._tidy_note))
        elif self.problems:
            p = self.problems[0]
            self.note.setText(
                f'<a href="line:{p.line}" style="color: inherit">'
                f"Line {p.line}: {_html_escape(p.message)}</a>"
            )
        else:
            self.note.setText("" if tab.colours else "Colours are off for files this big")

    def _note_link(self, link: str) -> None:
        if link.startswith("line:"):
            self.view.go_to_line(int(link[5:]))
            self.view.setFocus()

    def tidy(self) -> None:
        """Lay out the current JSON / XML neatly, as one undo step (refused while it has a
        mistake: the note says where)."""
        tab = self.current
        if tab is None or tab.language_id not in TIDY:
            return
        old = raw_text(tab.document)
        try:
            new = tidy(tab.language_id, old)
        except TidyError as exc:
            self._tidy_note = str(exc)
            self._update_note()
            return
        if new == old:
            return
        cursor = self.view.textCursor()
        cursor.beginEditBlock()
        cursor.select(cursor.SelectionType.Document)
        cursor.insertText(new)
        cursor.endEditBlock()
        self.run_check()

    def toggle_preview(self) -> None:
        """Markdown: the side pane on / off. HTML: open a copy in the default browser."""
        tab = self.current
        if tab is None or tab.language_id not in PREVIEWS:
            return
        if tab.language_id == "markdown":
            show = not self.preview_pane.isVisible()
            self.preview_pane.setVisible(show)
            if show:
                self._refresh_preview()
                self.splitter.setSizes([self.width() // 2, self.width() // 2])
            return
        path = html_preview_file(raw_text(tab.document), tab.path)
        if not open_in_browser(path):
            self.show_message("The browser couldn't be opened for the preview.", error=True)

    def _refresh_preview(self) -> None:
        tab = self.current
        if tab is not None and tab.language_id == "markdown" and self.preview_pane.isVisible():
            self.preview_pane.set_markdown(raw_text(tab.document))

    # ------------------------------------------------------------------ tab settings
    def set_language(self, language_id: str) -> None:
        tab = self.current
        if tab is None:
            return
        tab.language_id = language_id
        if tab.colours:
            tab.highlighter.set_language(language_id)
        self.view.set_language(language_id)
        self._update_status()

    def set_encoding(self, encoding: Encoding, bom: bool) -> None:
        tab = self.current
        if tab is not None:
            tab.encoding, tab.bom = encoding, bom
            self._update_status()

    def set_newline(self, newline: Newline) -> None:
        tab = self.current
        if tab is not None:
            tab.newline = newline
            self._update_status()

    def toggle_wrap(self) -> None:
        on = self.view.lineWrapMode() == self.view.LineWrapMode.NoWrap
        self.view.set_wrap(on)

    def ask_go_to_line(self) -> None:
        count = self.view.document().blockCount()
        line, ok = QInputDialog.getInt(
            self,
            "Go to line",
            f"Line (1–{count}):",
            self.view.textCursor().blockNumber() + 1,
            1,
            count,
        )
        if ok:
            self.view.go_to_line(line)

    def show_shortcuts(self) -> None:
        ShortcutsWindow(self).exec()

    # ------------------------------------------------------------------ saving
    def save(self, tab: CodeTab | None = None) -> bool:
        """Start saving ``tab`` (the current one by default). False if nothing was started."""
        tab = tab or self.current
        if tab is None or self.busy:
            return False
        if tab.path is None:
            return self.save_as(tab)
        if tab.disk_stamp is not None and _stamp(tab.path) not in (None, tab.disk_stamp):
            answer = self.ask(
                "Changed on disk",
                f"“{tab.path.name}” was changed by another program since you opened it. "
                "Overwrite it with your version?",
                ("no", "yes"),
            )
            if answer != "yes":
                return False
        return self._start_save(tab, tab.path)

    def save_as(self, tab: CodeTab | None = None) -> bool:
        tab = tab or self.current
        if tab is None or self.busy:
            return False
        if tab.path is not None:
            suggested = tab.path
        else:
            docs = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            suggested = Path(docs or Path.home()) / "Untitled.txt"
        path = self.choose_save_path(suggested)
        if path is None:
            return False
        return self._start_save(tab, path)

    def _encoded(self, tab: CodeTab, text: str) -> bytes | None:
        base = tab.file if tab.file is not None else decode(b"")
        try:
            return encode(
                base,
                text,
                encoding=tab.encoding,
                newline=tab.newline if tab.newline != base.newline else None,
                bom=tab.bom,
            )
        except ValueError:
            answer = self.ask(
                "Can't save in this encoding",
                f"This text has characters {tab.encoding.upper()} can't store. "
                "Save as UTF-8 instead?",
                ("yes", "no"),
            )
            if answer != "yes":
                return None
            tab.encoding, tab.bom = "utf-8", False
            self._update_status()
            return encode(
                base,
                text,
                encoding="utf-8",
                newline=tab.newline if tab.newline != base.newline else None,
                bom=False,
            )

    def _start_save(self, tab: CodeTab, path: Path) -> bool:
        text = raw_text(tab.document)
        data = self._encoded(tab, text)
        if data is None:
            return False
        self._run({"kind": "save", "tab": tab, "text": text}, lambda: _write(path, data))
        return True

    def _saved(
        self, tab: CodeTab, text: str, path: Path, file: TextFile, stamp: tuple[int, int] | None
    ) -> None:
        if tab not in self._tabs:
            return
        renamed = tab.path is None or not _same(tab.path, path)
        # keep what was chosen: reading the bytes back can't tell Windows-1252 from plain ASCII,
        # or CRLF from LF in a one-line file
        file = dataclasses.replace(
            file, encoding=tab.encoding, newline=tab.newline, bom=tab.bom, mixed=False
        )
        tab.path, tab.file, tab.disk_stamp = path, file, stamp
        if raw_text(tab.document) == text:
            tab.document.setModified(False)
        if renamed and tab.language_id == "text":
            self.set_language(language_for(path.name).id)
        self.show_message(f"Saved “{path.name}”.")
        self._refresh_tab_title(tab)
        self._update_status()

    # ------------------------------------------------------------------ closing
    def close_tab(self, index: int) -> bool:
        if not 0 <= index < len(self._tabs) or self.busy:
            return False
        tab = self._tabs[index]
        if tab.dirty:
            answer = self.ask(
                "Unsaved changes",
                f"Save the changes to “{tab.title}”?",
                ("save", "discard", "cancel"),
            )
            if answer == "cancel":
                return False
            if answer == "save":
                if not self.save(tab):
                    return False
                self.wait_idle()
                if tab.dirty:
                    return False
        self._remove_tab(index)
        if not self._tabs:
            self.open_empty()
        else:
            self._tab_changed(self.tabs.currentIndex())
        return True

    def maybe_leave(self) -> bool:
        """True when the editor may close: nothing unsaved, or saved / discarded on request."""
        self.wait_idle()
        dirty = [t for t in self._tabs if t.dirty]
        if not dirty:
            return True
        names = ", ".join(f"“{t.title}”" for t in dirty[:3]) + ("…" if len(dirty) > 3 else "")
        answer = self.ask(
            "Unsaved changes",
            f"Save the changes to {names}?",
            ("save", "discard", "cancel"),
        )
        if answer == "discard":
            while self._tabs:  # really gone: the next visit starts with only its own files
                self._remove_tab(0)
            self._update_status()
            return True
        if answer != "save":
            return False
        for tab in dirty:
            if not self.save(tab):
                return False
            self.wait_idle()
            if tab.dirty:
                return False
        return True


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
