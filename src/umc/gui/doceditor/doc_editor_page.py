"""The document editor page: top bar, formatting bar, find bar, banner, page, status line."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import detect
from umc.core.errors import ConversionError, ToolMissingError
from umc.doceditor.io import open_document
from umc.doceditor.save import save_document, save_formats
from umc.doceditor.types import EDITABLE_DOCUMENT_EXTS, OpenedDocument, warning_text
from umc.gui import theme
from umc.gui.doceditor import formatting as fm
from umc.gui.doceditor.find_bar import FindBar
from umc.gui.doceditor.format_bar import FormatBar
from umc.gui.doceditor.text_view import DocTextView
from umc.gui.tool_dialog import ToolMissingDialog

log = logging.getLogger(__name__)

__all__ = ["DocEditorPage", "OpenedDocument"]

ONLY_ONE = "Only one document can be edited at a time."
EXT_ALIASES = {"htm": "html", "markdown": "md"}
# The HTML normaliser raises the recursion limit for deeply nested pages: give workers room.
STACK_SIZE = 64 * 1024 * 1024
STATUS_DELAY_MS = 150


class _Worker(QObject):
    """Runs ``fn()`` on a QThread and reports the result."""

    done = Signal(object)
    failed = Signal(str)
    tool_missing = Signal(str, str, str)

    def __init__(self, fn: Callable[[], object], fallback: str) -> None:
        super().__init__()
        self.fn, self.fallback = fn, fallback

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn()
        except ToolMissingError as exc:
            self.tool_missing.emit(exc.tool, exc.download_url, str(exc))
        except ConversionError as exc:
            log.error("document editor: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("document editor job failed")
            self.failed.emit(self.fallback)
        else:
            self.done.emit(result)


class DocEditorPage(QWidget):
    back_requested = Signal()
    opened = Signal()
    save_done = Signal()
    _open_ended = Signal()  # the open thread finished (also after a cancelled open)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DocEditorPage")
        self.source: Path | None = None
        self.source_format: str | None = None
        self.warnings: list[str] = []
        self.saving = False
        self._opening: Path | None = None
        self._retry_path: Path | None = None
        self._open_ok = False
        self._open_cancel = threading.Event()
        self._discard_open = False  # the running open was cancelled: ignore its result
        self._next_open: Path | None = None  # opened when the running open has finished
        self._open_after_save: Path | None = None  # opened when the running save has finished
        self._jobs: dict[str, tuple[QThread, _Worker]] = {}
        self._pending_tool: tuple[str, str, str, Callable[[], object]] | None = None
        self._save_target: tuple[Path, str] | None = None  # set by Save as / a finished save
        self._saving_target: tuple[Path, str] | None = None
        self._save_revision = -1
        self._last_save_ok = False
        self._warned = False
        self._leaving = False  # maybe_leave() is running

        self.view = DocTextView()
        self.format_bar = FormatBar(self.view)
        self.find_bar = FindBar(self.view)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        bars = QVBoxLayout()
        bars.setContentsMargins(12, 8, 12, 8)
        bars.setSpacing(6)
        bars.addWidget(self.format_bar)
        bars.addWidget(self.find_bar)
        bars.addWidget(self._build_banner())
        root.addLayout(bars)
        root.addWidget(self.view, 1)

        status = QFrame(objectName="EditorStatus")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.status = QLabel(objectName="Muted")
        sl.addWidget(self.message, 1)
        sl.addWidget(self.status)
        root.addWidget(status)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(STATUS_DELAY_MS)
        self._status_timer.timeout.connect(self._update_status)
        doc = self.view.document()
        doc.contentsChanged.connect(self._status_timer.start)
        doc.modificationChanged.connect(lambda _m: self._update_status())
        self.view.undoAvailable.connect(lambda _a: self._update_buttons())
        self.view.redoAvailable.connect(lambda _a: self._update_buttons())

        self._install_shortcuts()
        self._update_buttons()
        self._update_status()

    # ------------------------------------------------------------ layout
    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorTopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        def tb(text: str, tip: str, fn, name: str = "Small") -> QPushButton:
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            lay.addWidget(b)
            return b

        self.back_button = tb(
            "← Back to Convert", "Leave the editor", self.back_requested.emit, "Link"
        )
        lay.addSpacing(12)
        self.undo_button = tb("Undo", "Undo (Ctrl+Z)", lambda: self.view.undo())
        self.redo_button = tb("Redo", "Redo (Ctrl+Y)", lambda: self.view.redo())
        lay.addStretch(1)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", lambda: self.save_as())
        self.save_button = tb("Save", "Save (Ctrl+S)", lambda: self.save(), "Primary")
        return bar

    def _build_banner(self) -> QFrame:
        p = theme.palette()
        self.banner = QFrame(objectName="DocBanner")
        self.banner.setStyleSheet(
            f"QFrame#DocBanner {{ background: {p['accent_soft']};"
            f" border: 1px solid {p['border']}; border-radius: 8px; }}"
            f" QFrame#DocBanner QLabel {{ color: {p['text']}; }}"
        )
        lay = QHBoxLayout(self.banner)
        lay.setContentsMargins(12, 8, 8, 8)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.banner_close = QToolButton(text="×")
        self.banner_close.setToolTip("Close this notice")
        self.banner_close.setAutoRaise(True)
        self.banner_close.clicked.connect(self.banner.hide)
        lay.addWidget(self.banner_label, 1)
        lay.addWidget(self.banner_close, 0, Qt.AlignmentFlag.AlignTop)
        self.banner.setVisible(False)
        return self.banner

    def _install_shortcuts(self) -> None:
        bar = self.format_bar

        def sc(keys, fn) -> None:
            for k in keys if isinstance(keys, tuple) else (keys,):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                s.activated.connect(lambda f=fn: f())

        for key, prop in (
            ("Ctrl+B", "bold"),
            ("Ctrl+I", "italic"),
            ("Ctrl+U", "underline"),
            ("Ctrl+Shift+X", "strike"),
        ):
            sc(key, lambda p=prop: bar._apply(fm.toggle, p))
        for key, style in (
            ("Ctrl+Alt+1", "h1"),
            ("Ctrl+Alt+2", "h2"),
            ("Ctrl+Alt+3", "h3"),
            ("Ctrl+Alt+0", "normal"),
        ):
            sc(key, lambda s=style: bar._apply(fm.set_block_style, s))
        for key, align in (
            ("Ctrl+L", "left"),
            ("Ctrl+E", "center"),
            ("Ctrl+R", "right"),
            ("Ctrl+J", "justify"),
        ):
            sc(key, lambda a=align: bar._apply(fm.set_alignment, a))
        sc("Ctrl+Shift+L", lambda: bar._apply(fm.toggle_list, "bullet"))
        sc("Ctrl+K", lambda: bar._insert_link())
        sc(("Ctrl+Return", "Ctrl+Enter"), lambda: bar._apply(fm.insert_page_break))
        sc("Ctrl+Space", lambda: bar._apply(fm.clear_formatting))
        sc("Ctrl+Z", lambda: self.view.undo())
        sc(("Ctrl+Y", "Ctrl+Shift+Z"), lambda: self.view.redo())
        sc("Ctrl+F", lambda: self.find_bar.open(False))
        sc("Ctrl+H", lambda: self.find_bar.open(True))
        sc("Ctrl+S", lambda: self.save())
        sc("Ctrl+Shift+S", lambda: self.save_as())

    # ------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        """An open or save is running (or its thread hasn't finished yet)."""
        return bool(self._jobs) or self._opening is not None or self.saving

    def _update_buttons(self) -> None:
        doc = self.view.document()
        self.undo_button.setEnabled(doc.isUndoAvailable())
        self.redo_button.setEnabled(doc.isRedoAvailable())
        enabled = self.source is not None and not self.busy
        self.save_button.setEnabled(enabled)
        self.save_as_button.setEnabled(enabled)

    def _update_status(self) -> None:
        self._status_timer.stop()
        if self.source is None:
            self.status.setText("")
            return
        doc = self.view.document()
        text = doc.toPlainText().replace("￼", " ")
        words = len(text.split())
        chars = len(text) - sum(text.count(ch) for ch in ("\n", " ", " ", "￼"))
        line = f"{words} words · {chars} characters"
        if doc.isModified():
            line += " · Unsaved changes"
        self.status.setText(line)

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    # ------------------------------------------------------------ workers
    def _start(
        self,
        kind: str,
        fn: Callable[[], object],
        fallback: str,
        done: Callable,
        failed: Callable,
        tool_missing: Callable,
        finished: Callable,
    ) -> None:
        thread = QThread(self)
        thread.setStackSize(STACK_SIZE)
        worker = _Worker(fn, fallback)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Bound methods of this page: Qt queues them onto the GUI thread.
        worker.done.connect(done)
        worker.failed.connect(failed)
        worker.tool_missing.connect(tool_missing)
        for sig in (worker.done, worker.failed, worker.tool_missing):
            sig.connect(thread.quit)
        thread.finished.connect(finished)
        self._jobs[kind] = (thread, worker)  # kept until QThread.finished (CLAUDE.md)
        thread.start()

    def _finish_job(self, kind: str) -> None:
        thread, worker = self._jobs.pop(kind)
        worker.deleteLater()
        thread.deleteLater()

    @Slot(str, str, str)
    def _on_tool_missing(self, tool: str, url: str, message: str) -> None:
        if self._opening is not None and self._discard_open:
            return
        self.show_message(message, error=True)
        retry = self._retry_open if self._opening is not None else self._retry_save
        self._pending_tool = (tool, url, message, retry)

    def _show_tool_dialog(self) -> None:
        pending, self._pending_tool = self._pending_tool, None
        if pending is None:
            return
        tool, url, message, retry = pending
        dialog = ToolMissingDialog(tool, url, message, parent=self)
        if dialog.exec():
            retry()

    # ------------------------------------------------------------ opening
    def open_path(self, path: Path) -> None:
        """Open ``path`` on a worker thread; ``opened`` is emitted when it's shown.

        While an open runs, it is cancelled and ``path`` opens next (the latest request wins);
        while a save runs, ``path`` opens after it (with the usual "Save changes?" check).
        """
        path = Path(path)
        if self._opening is not None:
            self._cancel_open()
            self._next_open = path
            self.show_message(f"Opening “{path.name}”…")
            return
        if self.saving or "save" in self._jobs:
            self._open_after_save = path
            self.show_message("Saving… the document will open when it's done.")
            return
        self._opening = path
        self._open_ok = False
        self._discard_open = False
        self._open_cancel = cancel = threading.Event()
        self.view.setEnabled(False)
        self._update_buttons()
        self.show_message(f"Opening “{path.name}”…")
        self._start(
            "open",
            lambda: open_document(path, cancel),
            f"Couldn't open “{path.name}”. See the log for details.",
            self._opened_ok,
            self._open_failed,
            self._on_tool_missing,
            self._open_finished,
        )

    def _cancel_open(self) -> None:
        self._discard_open = True
        self._open_cancel.set()

    def _wait_for_open(self) -> None:
        if self._opening is not None:
            loop = QEventLoop()
            self._open_ended.connect(loop.quit)  # connected first, then checked
            if self._opening is not None:
                loop.exec()
            self._open_ended.disconnect(loop.quit)

    def _retry_open(self) -> None:
        if self._retry_path is not None:
            self.open_path(self._retry_path)

    @Slot(object)
    def _opened_ok(self, opened: OpenedDocument) -> None:
        if self._discard_open:
            return
        path = self._opening
        self.view.load(opened)
        self.source, self.source_format = path, opened.source_format
        self.warnings = list(opened.warnings)
        self._save_target, self._warned = None, False
        self.banner_label.setText(warning_text(self.warnings))
        self.banner.setVisible(bool(self.warnings))
        self.find_bar.setVisible(False)
        self.show_message("")
        self._update_status()
        self._open_ok = True

    @Slot(str)
    def _open_failed(self, message: str) -> None:
        if not self._discard_open:
            self.show_message(message, error=True)

    @Slot()
    def _open_finished(self) -> None:
        self._finish_job("open")
        self._retry_path, self._opening = self._opening, None
        self.view.setEnabled(True)
        self._update_buttons()
        discarded, self._discard_open = self._discard_open, False
        self._open_ended.emit()
        if discarded:
            self.show_message("")
            nxt, self._next_open = self._next_open, None
            if nxt is not None:
                self.open_path(nxt)
            return
        if self._open_ok:  # emitted once the page is ready to save
            self.view.setFocus()
            self.opened.emit()
        self._show_tool_dialog()

    def drop_paths(self, paths: list[Path]) -> None:
        """Dropped files: one editable document replaces the current one."""
        paths = [Path(p) for p in paths]
        found = detect(paths[0]) if len(paths) == 1 else None
        if found is None or found.ext not in EDITABLE_DOCUMENT_EXTS:
            self.show_message(ONLY_ONE, error=True)
            return
        if self.saving or self._opening is not None or self.maybe_leave():
            self.open_path(paths[0])  # remembered / replaces the running open

    # ------------------------------------------------------------ saving
    def _ask_lose_parts(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Save over the original?")
        box.setText(
            f"Parts of this document will be lost ({', '.join(self.warnings)}). Save anyway?"
        )
        box.setInformativeText("Use Save as to keep the original file as it is.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.SaveAll
            | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.SaveAll).setText("Save as…")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        answer = box.exec()
        return {
            QMessageBox.StandardButton.Save: "save",
            QMessageBox.StandardButton.SaveAll: "save_as",
        }.get(QMessageBox.StandardButton(answer), "cancel")

    def _ask_replace(self, path: Path) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Replace file?")
        box.setText(f"“{path.name}” already exists. Replace it?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Replace")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return QMessageBox.StandardButton(box.exec()) == QMessageBox.StandardButton.Yes

    def save(self) -> bool:
        """Save to the opened file (or the last Save as target). False if nothing started."""
        if self.source is None or self.busy:
            return False
        if self._save_target is None:
            if self.warnings and not self._warned:
                answer = self._ask_lose_parts()
                if answer == "save_as":
                    return self.save_as()
                if answer != "save":
                    return False
                self._warned = True
            target = (self.source, self.source_format or "docx")
        else:
            target = self._save_target
        self._start_save(*target)
        return True

    def save_as(self) -> bool:
        """Ask for a file name and format and start saving. False if the user cancelled."""
        if self.source is None or self.busy:
            return False
        formats = save_formats()
        ids = [ext for ext, _ in formats]
        filters = [f"{label} (*.{ext})" for ext, label in formats]
        current = self.source_format if self.source_format in ids else ids[0]
        path, chosen = QFileDialog.getSaveFileName(
            self,
            "Save document as",
            str(self.source),
            ";;".join(filters),
            filters[ids.index(current)],
        )
        if not path:
            return False
        path = confirmed = Path(path)
        ext = path.suffix.lstrip(".").lower()
        ext = EXT_ALIASES.get(ext, ext)
        if ext not in ids:
            ext = ids[filters.index(chosen)] if chosen in filters else current
            path = path.with_name(f"{path.name}.{ext}")
        # The dialog only asked about the name it returned: ask about the one we will write.
        if path != confirmed and path.exists() and not self._ask_replace(path):
            return False
        self._start_save(path, ext)
        return True

    def _start_save(self, path: Path, fmt: str) -> None:
        content = self.view.content()
        if self.source_format == "docx":  # keep the original's styles, page setup, headers
            content.template = self.source
        self._save_revision = self.view.document().revision()
        self._saving_target = (path, fmt)
        self.saving = True
        self._last_save_ok = False
        self._update_buttons()
        self.show_message(f"Saving “{path.name}”…")
        self._start(
            "save",
            lambda: save_document(content, path, fmt),
            f"Couldn't save “{path.name}”. See the log for details.",
            self._saved_ok,
            self._save_failed,
            self._on_tool_missing,
            self._save_finished,
        )

    def _retry_save(self) -> None:
        if self._saving_target is not None and not self.busy:
            self._start_save(*self._saving_target)

    @Slot(object)
    def _saved_ok(self, _result: object) -> None:
        path, fmt = self._saving_target
        self._save_target = (path, fmt)
        self._last_save_ok = True
        doc = self.view.document()
        if doc.revision() == self._save_revision:  # edits made while saving stay unsaved
            doc.setModified(False)
        self.show_message(f"Saved “{path.name}”.")

    @Slot(str)
    def _save_failed(self, message: str) -> None:
        self.show_message(message, error=True)

    @Slot()
    def _save_finished(self) -> None:
        self._finish_job("save")
        self.saving = False
        self._update_buttons()
        self._update_status()
        self.save_done.emit()
        self._show_tool_dialog()
        path, self._open_after_save = self._open_after_save, None
        if path is not None:
            if self.saving:  # a retried save is running: wait for that one
                self._open_after_save = path
            elif self.maybe_leave():
                self.open_path(path)

    def _wait_for_save(self) -> bool:
        if self.saving:
            loop = QEventLoop()
            self.save_done.connect(loop.quit)  # connected first, then checked: no missed signal
            if self.saving:
                loop.exec()
            self.save_done.disconnect(loop.quit)
        return self._last_save_ok

    # ------------------------------------------------------------ leaving
    def _ask_save_changes(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes to this document?")
        box.setInformativeText("Your edits will be lost if you don't save them.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        return {
            QMessageBox.StandardButton.Save: "save",
            QMessageBox.StandardButton.Discard: "discard",
        }.get(QMessageBox.StandardButton(answer), "cancel")

    def maybe_leave(self) -> bool:
        """True when it's fine to close the editor (saved, discarded, or nothing changed).

        A call made while one is already waiting (in a nested event loop) returns False at
        once: the outer call decides."""
        if self._leaving:
            return False
        self._leaving = True
        try:
            return self._maybe_leave()
        finally:
            self._leaving = False

    def _maybe_leave(self) -> bool:
        self._open_after_save = self._next_open = None
        if self._opening is not None:  # cancel it, wait for its thread, drop its result
            self._cancel_open()
            self._wait_for_open()
        if self.saving:
            self._wait_for_save()
        doc = self.view.document()
        if self.source is None or not doc.isModified():
            return True
        answer = self._ask_save_changes()
        if answer == "discard":
            return True
        if answer == "save":
            started = self.save()
            return started and self._wait_for_save() and not doc.isModified()
        return False
