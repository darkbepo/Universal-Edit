"""The viewer's archive page: what a ZIP / 7Z / RAR / TAR holds, like a small file manager.

Folders open with a double-click (the breadcrumb and Backspace go back up); a file opens as a
preview in the viewer (unpacked into the viewer's temporary folder first). Extract here /
Extract to… / Add files… / Delete act on the selection, or on everything when nothing is
selected. Files can be dragged out to Explorer / the desktop (unpacked first) and dropped in
(added where you are, or into the folder dropped on). RAR can only be read. A password is
asked when needed and kept while the archive is open. Slow work runs on a thread with
progress and Cancel; changing the archive writes a new file that replaces it only when done."""

from __future__ import annotations

import contextlib
import itertools
import logging
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEventLoop, QMimeData, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from umc.core.errors import ConversionCancelled, ConversionError
from umc.gui import branding, icons, theme
from umc.tools import archive as engine
from umc.viewer import archive as model
from umc.viewer import files as vf

log = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole  # the entry's full name inside the archive
DIR_ROLE = Qt.ItemDataRole.UserRole + 1
_counter = itertools.count(1)


def fmt_size(n: int) -> str:
    from umc.gui.widgets.file_queue import fmt_size as _fmt

    return _fmt(n)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


class Job(QObject):
    """Runs ``fn(progress, cancel)`` on a thread; reports back on the GUI thread."""

    progress = Signal(float)
    finished = Signal(bool, object)  # ok, the result or the exception

    def __init__(
        self, fn: Callable[[Callable[[float], None], threading.Event], Any], daemon: bool = True
    ) -> None:
        """``daemon`` False: the program waits for it when quitting (a change to the archive
        then still removes its half-written file)."""
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()
        self.fraction = 0.0
        self.outcome: tuple[bool, object] | None = None  # set by the thread when it ends
        self.thread = threading.Thread(target=self._run, name="umc-archive", daemon=daemon)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            result = self.fn(self._report, self.cancel)
        except BaseException as exc:  # noqa: BLE001 - handed to the page, which explains it
            self.outcome = (False, exc)
        else:
            self.outcome = (True, result)
        with contextlib.suppress(RuntimeError):
            self.finished.emit(*self.outcome)

    def _report(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        with contextlib.suppress(RuntimeError):
            self.progress.emit(self.fraction)


class EntryList(QTreeWidget):
    """The files and folders of one folder inside the archive, draggable both ways."""

    def __init__(self, page: ArchiveView) -> None:
        super().__init__()
        self.page = page
        self.setObjectName("ArchiveList")
        self.setColumnCount(4)
        self.setHeaderLabels(["Name", "Size", "Packed", "Modified"])
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setIconSize(QSize(20, 20))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

    # dragging out
    def startDrag(self, _actions) -> None:  # noqa: N802
        paths = self.page.selected()
        if not paths:
            return
        files = self.page.drag_files(paths)
        if not files:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in files])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    # dropping in
    def _local_files(self, event) -> list[Path]:
        if event.source() is self or not event.mimeData().hasUrls():
            return []
        urls = event.mimeData().urls()
        if not urls or not all(u.isLocalFile() for u in urls):
            return []
        return [Path(u.toLocalFile()) for u in urls]

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.page.writable() and not self.page.busy() and self._local_files(event):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.page.writable() and not self.page.busy() and self._local_files(event):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        files = self._local_files(event)
        if not files:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        item = self.itemAt(event.position().toPoint())
        folder = self.page.folder
        if item is not None and item.data(0, DIR_ROLE):
            folder = item.data(0, PATH_ROLE)
        self.page.add_files(files, folder)


class ArchiveView(QWidget):
    preview_requested = Signal(object, str)  # file on disk (unpacked), its name inside
    show_folder = Signal(object)  # Path: "Show" after extracting (the viewer goes there)
    summary_changed = Signal(str)  # "12 files · 3.4 MB" for the bottom bar
    folder_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArchiveView")
        self.path: Path | None = None
        self.ext = ""
        self.tree: model.Tree | None = None
        self.folder = model.ROOT
        self.password = ""
        self.job: Job | None = None
        self.job_quiet = False  # reading / opening a preview: stopped without asking
        # Jobs done with, kept until their thread has ended: the last reference to a QObject
        # must never go away on the worker thread.
        self._old_jobs: list[Job] = []
        self._job_done: Callable[[bool, Any], None] | None = None
        self._unpacked: dict[str, Path] = {}  # name inside → file previewed (this archive)
        self._last_output: Path | None = None
        # set by the viewer: questions go through its ask (tests replace that)
        self.asker: Callable[[str, tuple[str, ...]], str] | None = None
        self.temp_root: Callable[[], Path] = lambda: Path(tempfile.gettempdir())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 12)
        lay.setSpacing(10)

        crumbs_row = QHBoxLayout()
        crumbs_row.setSpacing(4)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        crumbs_row.addLayout(self.crumbs)
        crumbs_row.addStretch(1)
        self.selection_label = QLabel(objectName="Muted")
        crumbs_row.addWidget(self.selection_label)
        lay.addLayout(crumbs_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.extract_here_button = QPushButton("Extract all here", objectName="Primary")
        self.extract_here_button.clicked.connect(self.extract_here)
        self.extract_to_button = QPushButton("Extract all to…", objectName="Secondary")
        self.extract_to_button.clicked.connect(self.extract_to)
        self.add_button = QPushButton("Add files…", objectName="Secondary")
        self.add_button.clicked.connect(self._add_clicked)
        self.delete_button = QPushButton("Delete", objectName="Secondary")
        self.delete_button.clicked.connect(self.delete_selected)
        for b in (
            self.extract_here_button,
            self.extract_to_button,
            self.add_button,
            self.delete_button,
        ):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(b)
        actions.addStretch(1)
        self.note = QLabel(objectName="Muted")
        self.note.setWordWrap(True)
        actions.addWidget(self.note, 2)
        self.show_button = QPushButton("Show", objectName="Secondary")
        self.show_button.setToolTip("Go to the folder the files went to")
        self.show_button.clicked.connect(self._show_output)
        self.show_button.hide()
        actions.addWidget(self.show_button)
        lay.addLayout(actions)

        self.list = EntryList(self)
        self.list.itemActivated.connect(self._activated)
        self.list.itemSelectionChanged.connect(self._update_actions)
        self.list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.list, 1)
        delete = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.list)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.delete_selected)

        busy = QFrame()
        bl = QHBoxLayout(busy)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.busy_label = QLabel(objectName="Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.cancel_button = QPushButton("Cancel", objectName="Secondary")
        self.cancel_button.clicked.connect(self.cancel)
        bl.addWidget(self.busy_label)
        bl.addWidget(self.bar, 1)
        bl.addWidget(self.cancel_button)
        self.busy_row = busy
        busy.hide()
        lay.addWidget(busy)
        theme.notifier().changed.connect(self._retint)

    # ------------------------------------------------------------ questions (tests replace)
    def ask(self, text: str, buttons: tuple[str, ...]) -> str:
        if self.asker is not None:
            return self.asker(text, buttons)
        return buttons[-1]

    def ask_password(self, text: str) -> str | None:
        value, ok = QInputDialog.getText(self, branding.APP_NAME, text, QLineEdit.EchoMode.Password)
        return value if ok and value else None

    def choose_folder(self, start: Path) -> Path | None:
        chosen = QFileDialog.getExistingDirectory(self, "Extract to", str(start))
        return Path(chosen) if chosen else None

    def choose_files(self) -> list[Path]:
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Add to the archive", str(self.path.parent if self.path else Path.home())
        )
        return [Path(p) for p in chosen]

    # ------------------------------------------------------------ opening
    def open(self, path: Path, ext: str) -> None:
        """Show what ``path`` holds (its top folder)."""
        self.shutdown()
        if self.path != path:
            self.password = ""
        self.path = path
        self.ext = ext
        self.tree = None
        self.folder = model.ROOT
        self._unpacked = {}
        self._set_note("")
        self.list.clear()
        self._fill_crumbs()
        self._update_actions()
        self.summary_changed.emit("")
        self._load()

    def _load(self, keep_folder: str = model.ROOT) -> None:
        path, ext, password = self.path, self.ext, self.password
        assert path is not None

        def done(contents: engine.Contents) -> None:
            self.tree = model.Tree(contents)
            folder = keep_folder
            while folder and not self.tree.is_folder(folder):
                folder = model.parent_of(folder)
            self.enter(folder)
            self._emit_summary()

        self._run(
            "Reading the archive…",
            lambda _p, _c: engine.entries(path, ext, password),
            done,
            retry=lambda: self._load(keep_folder),
            show_progress=False,
            quiet=True,
        )

    def _emit_summary(self) -> None:
        if self.tree is None:
            self.summary_changed.emit("")
            return
        parts = [_plural(self.tree.file_count(), "file")]
        parts.append(f"{fmt_size(self.tree.total_size())} unpacked")
        if self.tree.encrypted:
            parts.append("password-protected")
        if not self.writable():
            parts.append("read only")
        self.summary_changed.emit(" · ".join(parts))

    def writable(self) -> bool:
        return self.tree is not None and engine.can_change(self.ext)

    # ------------------------------------------------------------ folders
    def enter(self, folder: str) -> None:
        """Show what ``folder`` (inside the archive) holds."""
        if self.tree is None:
            return
        self.folder = folder
        pal = theme.palette()
        self.list.clear()
        for node in self.tree.listing(folder):
            if node.is_dir:
                count = node.files()
                size = _plural(count, "file")
                packed = ""
            else:
                size = fmt_size(node.size)
                packed = fmt_size(node.packed) if node.packed is not None else ""
            modified = node.modified.strftime("%Y-%m-%d %H:%M") if node.modified else ""
            item = QTreeWidgetItem(self.list, [node.name, size, packed, modified])
            item.setData(0, PATH_ROLE, node.path)
            item.setData(0, DIR_ROLE, node.is_dir)
            icon = "folder" if node.is_dir else vf.ICON_OF_KIND[model.kind_of_entry(node.path)]
            if node.encrypted:
                icon = "lock"
            item.setIcon(0, icons.icon(icon, pal["accent" if node.is_dir else "muted"], 20))
            item.setToolTip(0, node.path + (" (password-protected)" if node.encrypted else ""))
            for col in (1, 2):
                item.setTextAlignment(
                    col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
        if self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0), 0)
            self.list.clearSelection()
        elif not folder:
            self._set_note("This archive is empty.")
        self._fill_crumbs()
        self._update_actions()
        self.folder_changed.emit()

    def go_up(self) -> bool:
        """Up one folder inside the archive; False at its top."""
        if not self.folder:
            return False
        self.enter(model.parent_of(self.folder))
        return True

    def _fill_crumbs(self) -> None:
        while self.crumbs.count():
            w = self.crumbs.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if self.path is None:
            return
        trail = model.breadcrumb(self.path.name, self.folder)
        for n, (label, folder) in enumerate(trail):
            if n:
                self.crumbs.addWidget(QLabel("›", objectName="Muted"))
            b = QPushButton(label, objectName="Link")
            b.setFlat(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            last = n == len(trail) - 1
            b.setEnabled(not last)
            if not last:
                b.clicked.connect(lambda _c=False, f=folder: self.enter(f))
            b.setToolTip(folder or "The archive's top")
            self.crumbs.addWidget(b)

    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        name = item.data(0, PATH_ROLE)
        if item.data(0, DIR_ROLE):
            self.enter(name)
        else:
            self.open_entry(name)

    def selected(self) -> list[str]:
        return [it.data(0, PATH_ROLE) for it in self.list.selectedItems()]

    def _scope(self) -> list[str] | None:
        """The selection's names, or None (= everything)."""
        return self.selected() or None

    def _update_actions(self) -> None:
        loaded = self.tree is not None and not self.busy()
        chosen = self.selected()
        n = len(chosen)
        if n:
            self.extract_here_button.setText("Extract here")
            self.extract_to_button.setText("Extract to…")
            covered = self.tree.covered(chosen) if self.tree else []
            self.selection_label.setText(
                f"{_plural(n, 'item')} selected · {_plural(len(covered), 'file')}"
            )
        else:
            self.extract_here_button.setText("Extract all here")
            self.extract_to_button.setText("Extract all to…")
            self.selection_label.setText("")
        stem = model.archive_stem(self.path) if self.path else ""
        self.extract_here_button.setToolTip(f"Into a new folder “{stem}” next to the archive")
        self.extract_here_button.setEnabled(loaded)
        self.extract_to_button.setEnabled(loaded)
        writable = loaded and self.writable()
        self.add_button.setEnabled(writable)
        self.delete_button.setEnabled(writable and n > 0)
        tip = "" if self.writable() or self.tree is None else "RAR archives can only be read"
        for b in (self.add_button, self.delete_button):
            b.setToolTip(
                tip
                or (
                    "Add files here (or drop them on the list)"
                    if b is self.add_button
                    else "Delete the selected items from the archive (Delete)"
                )
            )

    def _retint(self) -> None:
        if self.tree is not None:
            chosen = set(self.selected())
            self.enter(self.folder)
            for i in range(self.list.topLevelItemCount()):
                it = self.list.topLevelItem(i)
                it.setSelected(it.data(0, PATH_ROLE) in chosen)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or self.busy():
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._activated(item))
        menu.addAction("Extract here", self.extract_here)
        menu.addAction("Extract to…", self.extract_to)
        if self.writable():
            menu.addSeparator()
            menu.addAction("Delete", self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ work on a thread
    def busy(self) -> bool:
        return self.job is not None

    def _run(
        self,
        text: str,
        fn: Callable[[Callable[[float], None], threading.Event], Any],
        on_done: Callable[[Any], None],
        retry: Callable[[], None] | None = None,
        show_progress: bool = True,
        quiet: bool = False,
    ) -> None:
        """Run ``fn`` on a thread with progress and Cancel; a password problem asks for it and
        runs ``retry``; other problems are shown in plain words."""
        if self.job is not None:
            return
        job = Job(fn, daemon=quiet)
        self.job = job
        self.job_quiet = quiet
        self._pending = (on_done, retry)
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        self.busy_label.setText(text)
        self.bar.setRange(0, 1000 if show_progress else 0)
        self.bar.setValue(0)
        self.busy_row.show()
        self.show_button.hide()
        self._update_actions()
        job.start()

    def _on_progress(self, fraction: float) -> None:
        if self.bar.maximum():
            self.bar.setValue(round(fraction * 1000))

    def _on_finished(self, ok: bool, result: object) -> None:
        on_done, retry = self._pending
        self._forget(self.job)
        self.job = None
        self.busy_row.hide()
        self._update_actions()
        if ok:
            on_done(result)
            return
        if isinstance(result, ConversionCancelled):
            self._set_note("Stopped.")
        elif isinstance(result, engine.PasswordNeeded):
            again = bool(self.password)
            text = (
                "That password didn't work. Try again:"
                if again
                else f"“{self.path.name if self.path else ''}” is password-protected. Password:"
            )
            password = self.ask_password(text)
            if password and retry is not None:
                self.password = password
                retry()
            else:
                self._set_note("This needs the archive's password.")
        elif isinstance(result, ConversionError):
            log.info("archive view: %s (%s)", result, getattr(result, "details", ""))
            self._set_note(str(result))
        else:
            log.error("archive view job failed", exc_info=result)
            self._set_note("Something went wrong. The details are in the log.")
        if self.tree is None and not isinstance(result, engine.PasswordNeeded):
            self.summary_changed.emit("")

    def cancel(self) -> None:
        if self.job is not None:
            self.job.cancel.set()
            self.busy_label.setText("Stopping…")

    def shutdown(self) -> None:
        """The window closes: stop what runs (a change in progress leaves the archive as it
        was)."""
        if self.job is not None:
            job = self.job
            job.cancel.set()
            with contextlib.suppress(RuntimeError, TypeError):
                job.finished.disconnect(self._on_finished)
                job.progress.disconnect(self._on_progress)
            if not self.job_quiet:  # a change stops at its next file and tidies up
                job.thread.join(5)
            self._forget(job)
            self.job = None
            self.busy_row.hide()
            self._update_actions()

    def _forget(self, job: Job | None) -> None:
        self._old_jobs = [j for j in self._old_jobs if j.thread.is_alive()]
        if job is not None and job.thread.is_alive():
            self._old_jobs.append(job)

    def wait(self, ms: int = 10_000) -> bool:
        """Tests: wait for the running job's thread (its result still arrives as a signal)."""
        job = self.job
        if job is None:
            return True
        job.thread.join(ms / 1000)
        return not job.thread.is_alive()

    def _set_note(self, text: str) -> None:
        self.note.setText(text)
        extracted = text.startswith("Extracted") and self._last_output is not None
        self.show_button.setVisible(extracted)

    # ------------------------------------------------------------ extracting
    def _extract(self, dest: Path, only: list[str] | None) -> None:
        path, ext, password, strip = self.path, self.ext, self.password, self.folder
        assert path is not None and self.tree is not None
        names = only if only is not None else ([self.folder] if self.folder else None)

        def work(progress, cancel) -> list[Path]:
            return engine.extract(
                path, ext, dest, password, progress, cancel, only=names, strip=strip, unique=True
            )

        def done(written: list[Path]) -> None:
            self._last_output = dest
            self._set_note(f"Extracted {_plural(len(written), 'file')} to {dest}")

        self._run(
            "Extracting…",
            work,
            done,
            retry=lambda: self._extract(dest, only),
        )

    def extract_here(self) -> None:
        """Into a new folder next to the archive, named after it."""
        if self.path is None or self.tree is None or self.busy():
            return
        self._extract(model.extract_here_folder(self.path), self._scope())

    def extract_to(self) -> None:
        if self.path is None or self.tree is None or self.busy():
            return
        dest = self.choose_folder(self.path.parent)
        if dest is not None:
            self._extract(dest, self._scope())

    def _show_output(self) -> None:
        if self._last_output is not None:
            self.show_folder.emit(self._last_output)

    def open_entry(self, name: str) -> None:
        """Preview a file inside: unpacked once into the viewer's temporary folder."""
        if self.path is None or self.busy():
            return
        if name in self._unpacked and self._unpacked[name].is_file():
            self.preview_requested.emit(self._unpacked[name], name)
            return
        path, ext, password = self.path, self.ext, self.password
        dest = self.temp_root() / f"a{next(_counter)}"

        def work(progress, cancel) -> Path:
            written = engine.extract(
                path,
                ext,
                dest,
                password,
                progress,
                cancel,
                only=[name],
                strip=model.parent_of(name),
            )
            if not written:
                raise ConversionError("That file couldn't be unpacked.")
            return written[0]

        def done(file: Path) -> None:
            self._unpacked[name] = file
            self.preview_requested.emit(file, name)

        self._run(
            f"Opening {name.rsplit('/', 1)[-1]}…",
            work,
            done,
            retry=lambda: self.open_entry(name),
            quiet=True,
        )

    def drag_files(self, names: list[str]) -> list[Path]:
        """Unpack ``names`` (selected in the folder shown) for a drag out; returns the top-level
        files / folders to hand over. Waits here (with a progress window for big ones)."""
        if self.path is None or self.tree is None or self.busy():
            return []
        path, ext, password, strip = self.path, self.ext, self.password, self.folder
        dest = self.temp_root() / f"d{next(_counter)}"
        while True:
            job = Job(
                lambda progress, cancel, password=password: engine.extract(
                    path, ext, dest, password, progress, cancel, only=names, strip=strip
                )
            )
            ok, result = self._wait_for(job)
            if ok:
                break
            if isinstance(result, engine.PasswordNeeded):
                password_given = self.ask_password(
                    "That password didn't work. Try again:"
                    if password
                    else f"“{path.name}” is password-protected. Password:"
                )
                if not password_given:
                    return []
                self.password = password = password_given
                continue
            if not isinstance(result, ConversionCancelled):
                plain = isinstance(result, ConversionError)
                self._set_note(str(result) if plain else "Couldn't get the files ready.")
            return []
        return [p for n in names if (p := dest / n.rsplit("/", 1)[-1]).exists()]

    def _wait_for(self, job: Job) -> tuple[bool, object]:
        """Run ``job`` and wait for it here, the window still repainting; a progress window
        with Cancel appears when it takes a moment."""
        dialog = QProgressDialog("Getting the files ready…", "Cancel", 0, 1000, self)
        dialog.setWindowTitle(branding.APP_NAME)
        dialog.setMinimumDuration(400)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(job.cancel.set)
        loop = QEventLoop()
        timer = QTimer(interval=30)

        def poll() -> None:
            dialog.setValue(round(job.fraction * 1000))
            if job.outcome is not None:
                loop.quit()

        timer.timeout.connect(poll)
        job.start()
        timer.start()
        if job.outcome is None:
            loop.exec()
        timer.stop()
        dialog.close()
        dialog.deleteLater()
        job.thread.join(5)
        self._forget(job)
        return job.outcome or (False, ConversionError("The files couldn't be got ready."))

    # ------------------------------------------------------------ changing the archive
    def _add_clicked(self) -> None:
        if self.writable() and not self.busy():
            files = self.choose_files()
            if files:
                self.add_files(files, self.folder)

    def add_files(self, files: list[Path], folder: str | None = None) -> None:
        """Put ``files`` (folders too) into the archive, in ``folder`` (default: the one shown)."""
        if self.path is None or self.tree is None or self.busy():
            return
        if not engine.can_change(self.ext):
            self._set_note("RAR archives can only be read: files can't be added.")
            return
        folder = self.folder if folder is None else folder
        adds = model.files_to_add(folder, files)
        if not adds:
            self._set_note("There were no files to add.")
            return
        inside = (folder + "/") if folder else ""
        if (bad := model.folder_clash(self.tree, adds)) is not None:
            self._set_note(f"“{bad}” can't be added: there's a file or folder of that name.")
            return
        taken = model.clashes(self.tree, adds)
        remove: list[str] = []
        if taken:
            what = f"“{taken[0]}”" if len(taken) == 1 else _plural(len(taken), "file")
            answer = self.ask(
                f"{what} already in the archive. Replace or keep both?",
                ("Replace", "Keep both", "Cancel"),
            )
            if answer == "Replace":
                remove = taken
            elif answer == "Keep both":
                adds = model.keep_both(self.tree, adds)
            else:
                return
        else:
            answer = self.ask(
                f"Add {_plural(len(adds), 'file')} to {self.path.name}"
                + (f" (in {inside.rstrip('/')})" if inside else "")
                + "?",
                ("Add", "Cancel"),
            )
            if answer != "Add":
                return
        self._change(adds, remove, f"Added {_plural(len(adds), 'file')}.")

    def delete_selected(self) -> None:
        names = self.selected()
        if not names or not self.writable() or self.busy() or self.path is None:
            return
        what = (
            f"“{names[0].rsplit('/', 1)[-1]}”" if len(names) == 1 else _plural(len(names), "item")
        )
        answer = self.ask(
            f"Delete {what} from {self.path.name}? This can't be undone.", ("Delete", "Cancel")
        )
        if answer != "Delete":
            return
        self._change([], names, f"Deleted {what}.")

    def _change(self, adds: list[tuple[Path, str]], remove: list[str], message: str) -> None:
        path, ext, password = self.path, self.ext, self.password
        assert path is not None and self.tree is not None
        if self.tree.encrypted and not password:
            given = self.ask_password(f"“{path.name}” is password-protected. Password:")
            if not given:
                return
            self.password = password = given
        folder = self.folder

        def done(_r) -> None:
            self._unpacked = {}
            self._load(keep_folder=folder)
            self._last_output = None
            self._set_note(message)

        self._run(
            "Saving the archive…",
            lambda progress, cancel: engine.rewrite(
                path, ext, password, adds, remove, progress, cancel
            ),
            done,
            retry=lambda: self._change(adds, remove, message),
        )
