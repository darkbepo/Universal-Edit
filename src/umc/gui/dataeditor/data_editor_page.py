"""The data editor page: a grid of one sheet (tabs for a workbook's sheets), an edit box above it
for the chosen cell, rows / columns / sort / header tools, find & replace, copy / paste, undo.

The document is ``umc.dataedit`` (frozen sheets + snapshot undo). Reading and writing files run
on a QThread (results come back through bound-method slots, queued onto the GUI thread)."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from umc.codeedit import search
from umc.core.errors import ConversionError
from umc.core.filemode import match_mode
from umc.dataedit import csvtext, export, io, table
from umc.dataedit.cells import display, edit_text
from umc.dataedit.history import History
from umc.dataedit.table import DataDoc, EditError, Sheet, letter
from umc.gui.dataeditor.sheet_model import SheetModel, column_title
from umc.gui.dataeditor.shortcuts import SHORTCUTS
from umc.gui.widgets.find_bar import FindBar

log = logging.getLogger(__name__)

ROW_HEIGHT = 24
MAX_COLUMN_WIDTH = 320
WIDTH_SAMPLE_ROWS = 200  # columns are sized from the first rows only (big files open fast)
HEADER_FORMATS = ("csv", "tsv", "xlsx")  # the "First row is the header" switch


class UserError(Exception):
    """A problem to show as it is."""


def _read(path: Path) -> tuple[Path, io.Opened]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserError(f"“{path.name}” couldn't be opened: {exc.strerror or exc}") from exc
    try:
        return path, io.open_bytes(data, path.suffix)
    except io.DataError as exc:
        raise UserError(f"“{path.name}”: {exc}") from exc


def _write(path: Path, data: bytes) -> tuple[Path, io.Opened]:
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
    try:
        return path, io.open_bytes(data, path.suffix)
    except io.DataError as exc:  # written, but can't be read back as a table
        raise UserError(f"“{path.name}” was saved, but can't be opened again: {exc}") from exc


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
            log.exception("data editor job failed")
            self.failed.emit(self.job, "Something went wrong. The details are in the log.")
        else:
            self.done.emit(self.job, result)


class _CellBox(QPlainTextEdit):
    """The chosen cell's whole text. Enter keeps it (Shift+Enter starts a new line), Esc puts it
    back, leaving keeps it too. Undo / redo are the page's (one history for everything)."""

    committed = Signal(str)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("CellBox")
        self.setAccessibleName("Cell")
        self.setToolTip(
            "The chosen cell — Enter keeps it, Shift+Enter starts a new line, Esc puts it back"
        )
        self.setTabChangesFocus(True)
        self.setFixedHeight(56)
        self._shown = ""

    def show_text(self, text: str) -> None:
        self._shown = text
        self.setPlainText(text)

    def _commit(self) -> None:
        if self.toPlainText() != self._shown:
            self.committed.emit(self.toPlainText())

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ShortcutOverride and (
            e.matches(QKeySequence.StandardKey.Undo) or e.matches(QKeySequence.StandardKey.Redo)
        ):
            e.ignore()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        enter = e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._commit()
            self.cancelled.emit()  # back to the grid
            return
        if e.key() == Qt.Key.Key_Escape:
            self.setPlainText(self._shown)
            self.cancelled.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self._commit()
        super().focusOutEvent(e)


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
            keys = " / ".join(s.keys) + (" (in the table)" if s.table else "")
            grid.addWidget(QLabel(keys, objectName="ShortcutKey"), row, 0)
            grid.addWidget(QLabel(s.label), row, 1)
            row += 1


class DataEditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DataEditorPage")
        self.opened: io.Opened = io.new_document("csv")
        self.history = History(self.opened.doc)
        self.path: Path | None = None  # where Save writes (None: ask first)
        self.source: Path | None = None  # the file opened (shown as the title)
        self.sheet_index = 0
        self._asked_lost = False  # "can't keep comments" asked once per file
        self._jobs: dict[int, tuple[QThread, _Worker]] = {}
        self._next_job = 0
        self.actions: dict[str, Callable[[], object]] = {}

        self.model = SheetModel(self)
        self.model.edited.connect(self._typed_in_cell)
        self.table = QTableView()
        self.table.setObjectName("DataTable")
        self.table.setAccessibleName("Table")
        self.table.setModel(self.model)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(ROW_HEIGHT)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionsClickable(True)
        head.sectionDoubleClicked.connect(self.ask_rename)
        head.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        head.customContextMenuRequested.connect(self._column_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)
        self.table.selectionModel().currentChanged.connect(lambda *_a: self._fill_box())
        self.table.selectionModel().selectionChanged.connect(lambda *_a: self._update_buttons())
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.installEventFilter(self)

        self.find_bar = FindBar(placeholder="Find in the table")
        self.find_bar.find_next.connect(lambda: self.find(1))
        self.find_bar.find_prev.connect(lambda: self.find(-1))
        self.find_bar.replace_one.connect(self.replace_one)
        self.find_bar.replace_all.connect(self.replace_all)
        self.find_bar.changed.connect(self._count_matches)

        self.tabs = QTabBar()
        self.tabs.setObjectName("SheetTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_tool_row())
        root.addWidget(self.find_bar)
        root.addWidget(self.table, 1)
        root.addWidget(self.tabs)
        root.addWidget(self._build_status_bar())
        self._install_shortcuts()
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
        tb("Open…", "Open a table (Ctrl+O)", self.choose_files)
        tb("New", "New table (Ctrl+N)", self.open_empty)
        self.title = QLabel()
        lay.addSpacing(8)
        lay.addWidget(self.title)
        lay.addStretch(1)
        tb("Find", "Find and replace (Ctrl+F, Ctrl+H)", lambda: self.find_bar.open(False))
        tb("?", "Keyboard shortcuts (F1)", self.show_shortcuts)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", self.save_as)
        self.save_button = tb("Save", "Save (Ctrl+S)", self.save, "Primary")
        return bar

    def _build_tool_row(self) -> QWidget:
        panel = QFrame(objectName="DataToolRow")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)

        def b(text: str, tip: str, fn) -> QPushButton:
            btn = QPushButton(text, objectName="Small")
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the grid keeps its place
            btn.clicked.connect(lambda _c=False: fn())
            row.addWidget(btn)
            return btn

        self.add_row_button = b("+ Row", "Add a row below (Ctrl+Enter)", self.add_row)
        self.add_column_button = b(
            "+ Column", "Add a column to the right (Ctrl+Shift+Enter)", self.add_column
        )
        self.delete_rows_button = b(
            "Delete rows", "Delete the chosen rows (Ctrl+-)", self.delete_rows
        )
        self.delete_column_button = b(
            "Delete column", "Delete the chosen columns", self.delete_columns
        )
        self.rename_button = b(
            "Rename column…",
            "Rename this column (or double-click its name)",
            lambda: self.ask_rename(self.current()[1]),
        )
        self.sort_up_button = b(
            "Sort A→Z", "Sort the rows by this column (numbers by value)", lambda: self.sort(False)
        )
        self.sort_down_button = b(
            "Sort Z→A", "Sort the rows by this column, largest first", lambda: self.sort(True)
        )
        self.header_box = QCheckBox("First row is the header")
        self.header_box.setToolTip("Use the first row as the column names (CSV, TSV and Excel)")
        self.header_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header_box.toggled.connect(self.set_header)
        row.addWidget(self.header_box)
        row.addStretch(1)
        outer.addLayout(row)
        edit = QHBoxLayout()
        edit.setSpacing(8)
        self.cell_name = QLabel(objectName="Muted")
        self.cell_name.setMinimumWidth(48)
        self.cell_box = _CellBox()
        self.cell_box.committed.connect(self._typed_in_box)
        self.cell_box.cancelled.connect(self.table.setFocus)
        edit.addWidget(self.cell_name)
        edit.addWidget(self.cell_box, 1)
        outer.addLayout(edit)
        return panel

    def _build_status_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorStatusBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.size_label = QLabel(objectName="Muted")
        lay.addWidget(self.message, 1)
        lay.addWidget(self.size_label)
        return bar

    def _install_shortcuts(self) -> None:
        self.actions = {
            "new": self.open_empty,
            "open": self.choose_files,
            "save": self.save,
            "save_as": self.save_as,
            "undo": self.undo,
            "redo": self.redo,
            "add_row": self.add_row,
            "add_column": self.add_column,
            "delete_rows": self.delete_rows,
            "edit_box": self.cell_box.setFocus,
            "edit_cell": lambda: self.table.edit(self.table.currentIndex()),
            "clear": self.clear_cells,
            "copy": self.copy,
            "cut": self.cut,
            "paste": self.paste,
            "find": lambda: self.find_bar.open(False),
            "replace": lambda: self.find_bar.open(True),
            "find_next": lambda: self.find(1),
            "find_prev": lambda: self.find(-1),
            "help": self.show_shortcuts,
        }
        self._table_keys = []
        for s in SHORTCUTS:
            for key in s.keys:
                seq = QKeySequence(key)
                if s.table:  # the grid's own keys: see eventFilter
                    self._table_keys.append((seq, self.actions[s.id]))
                    continue
                sc = QShortcut(seq, self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda f=self.actions[s.id]: f())

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if (
            obj is self.table
            and e.type() == QEvent.Type.KeyPress
            and self.table.state() != (QAbstractItemView.State.EditingState)
        ):
            pressed = QKeySequence(e.keyCombination())
            for seq, fn in self._table_keys:
                if seq == pressed and seq != QKeySequence("F2"):
                    fn()
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

    def ask_text(self, title: str, label: str, text: str) -> str | None:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value if ok else None

    def choose_save_path(self, suggested: Path, fmts: tuple[str, ...]) -> Path | None:
        filters = ";;".join(f"{io.NAMES[f]} (*.{f})" for f in fmts)
        path, chosen = QFileDialog.getSaveFileName(self, "Save as", str(suggested), filters)
        if not path:
            return None
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        if ext == "yml":
            return p
        if ext not in fmts:  # no / another extension: the chosen filter's
            ext = next((f for f in fmts if f"*.{f})" in chosen), fmts[0])
            p = p.with_name(p.name + "." + ext)
        return p

    def choose_open_paths(self) -> list[Path]:
        exts = " ".join(f"*.{e}" for e in (*io.FORMATS, "yml"))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open a table", "", f"Tables and data ({exts});;All files (*)"
        )
        return [Path(p) for p in paths]

    def exec_menu(self, menu: QMenu, pos) -> None:
        menu.exec(pos)

    # ------------------------------------------------------------------ state
    @property
    def doc(self) -> DataDoc:
        return self.history.doc

    @property
    def sheet(self) -> Sheet:
        return self.doc.sheets[self.sheet_index]

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    @property
    def dirty(self) -> bool:
        return self.history.dirty

    def current(self) -> tuple[int, int]:
        """(row, column) of the chosen cell; (-1, -1) when none."""
        i = self.table.currentIndex()
        return (i.row(), i.column()) if i.isValid() else (-1, -1)

    def chosen_cells(self) -> list[tuple[int, int]]:
        cells = sorted(
            {(i.row(), i.column()) for i in self.table.selectionModel().selectedIndexes()}
        )
        r, c = self.current()
        return cells or ([(r, c)] if r >= 0 else [])

    def chosen_rows(self) -> list[int]:
        return sorted({r for r, _c in self.chosen_cells()})

    def chosen_columns(self) -> list[int]:
        return sorted({c for _r, c in self.chosen_cells()})

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

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

    def open_paths(self, paths: list[Path]) -> bool:
        """Open the first table in ``paths``. Asks about unsaved changes first; False if
        nothing was started."""
        wanted = (*io.FORMATS, "yml")
        path = next((Path(p) for p in paths if Path(p).suffix.lower().lstrip(".") in wanted), None)
        if path is None:
            self.show_message("Choose a CSV, TSV, JSON, YAML, XML or Excel (XLSX) file.", True)
            return False
        if self.busy or not self._discard_ok():
            return False
        self._run({"kind": "open"}, lambda: _read(path))
        return True

    def open_empty(self) -> bool:
        if self.busy or not self._discard_ok():
            return False
        self._load(None, io.new_document("csv"))
        return True

    def _discard_ok(self) -> bool:
        if not self.dirty:
            return True
        answer = self.ask(
            "Unsaved changes", "Save the changes to the table first?", ("save", "discard", "cancel")
        )
        if answer == "discard":
            return True
        if answer == "save" and self.save():
            self.wait_idle()
            return not self.dirty
        return False

    def _load(self, path: Path | None, opened: io.Opened) -> None:
        self.opened = opened
        self.path = self.source = path
        self.history = History(opened.doc)
        self.sheet_index = 0
        self._asked_lost = False
        self._show_doc(reset=True)
        self._fit_columns()
        if self.sheet.rows and self.sheet.columns:
            self.table.setCurrentIndex(self.model.index(0, 0))
        if path is None:
            self.show_message("A new table: type into the cells, then Save.")
            return
        rows, cols = len(self.sheet.rows), len(self.sheet.columns)
        note = f"Opened “{path.name}”: {rows:,} row{'s' if rows != 1 else ''}, {cols} column"
        note += "s." if cols != 1 else "."
        if len(self.doc.sheets) > 1:
            note += f" {len(self.doc.sheets)} sheets: pick one below the table."
        self.show_message(note)

    # ------------------------------------------------------------------ showing the document
    def _show_doc(self, reset: bool = False, select: tuple[int, int] | None = None) -> None:
        doc = self.doc
        self.sheet_index = min(self.sheet_index, len(doc.sheets) - 1)
        names = [s.name for s in doc.sheets]
        if [self.tabs.tabText(i) for i in range(self.tabs.count())] != names:
            self.tabs.blockSignals(True)
            while self.tabs.count():
                self.tabs.removeTab(0)
            for n in names:
                self.tabs.addTab(n)
            self.tabs.blockSignals(False)
        self.tabs.setCurrentIndex(self.sheet_index)
        self.tabs.setVisible(len(names) > 1)
        keep = select or self.current()
        reset = self.model.set_sheet(self.sheet) or reset
        sheet = self.sheet
        if sheet.rows and sheet.columns and (reset or select is not None):
            r = min(max(keep[0], 0), len(sheet.rows) - 1)
            c = min(max(keep[1], 0), len(sheet.columns) - 1)
            self.table.setCurrentIndex(self.model.index(r, c))
        self.header_box.blockSignals(True)
        self.header_box.setChecked(sheet.header)
        self.header_box.blockSignals(False)
        self.header_box.setVisible(doc.format in HEADER_FORMATS)
        self.size_label.setText(f"{len(sheet.rows):,} rows × {len(sheet.columns)} columns")
        self._fill_box()
        self._update_title()
        self._update_buttons()

    def _fit_columns(self) -> None:
        """Columns as wide as their name and the first rows need (up to a limit)."""
        sheet, fm = self.sheet, self.table.fontMetrics()
        for c in range(len(sheet.columns)):
            texts = [column_title(sheet, c)]
            texts += [display(r[c])[:60] for r in sheet.rows[:WIDTH_SAMPLE_ROWS]]
            width = max(fm.horizontalAdvance(t) for t in texts) + 24
            self.table.setColumnWidth(c, min(max(width, 60), MAX_COLUMN_WIDTH))

    def _fill_box(self) -> None:
        r, c = self.current()
        sheet = self.sheet
        if 0 <= r < len(sheet.rows) and 0 <= c < len(sheet.columns):
            self.cell_name.setText(f"{letter(c)}{r + 1}")
            self.cell_box.setEnabled(True)
            self.cell_box.show_text(edit_text(sheet.rows[r][c], sheet.typed))
        else:
            self.cell_name.setText("")
            self.cell_box.show_text("")
            self.cell_box.setEnabled(False)

    def _update_title(self) -> None:
        name = self.source.name if self.source else "New table"
        self.title.setText(("• " if self.dirty else "") + name)

    def _update_buttons(self) -> None:
        sheet = self.sheet
        has_cell = self.current()[0] >= 0
        idle = not self.busy
        self.save_button.setEnabled(idle)
        self.save_as_button.setEnabled(idle)
        self.add_row_button.setEnabled(bool(sheet.columns))
        self.delete_rows_button.setEnabled(has_cell)
        self.delete_column_button.setEnabled(has_cell)
        self.rename_button.setEnabled(sheet.header and bool(sheet.columns))
        self.sort_up_button.setEnabled(has_cell and len(sheet.rows) > 1)
        self.sort_down_button.setEnabled(has_cell and len(sheet.rows) > 1)

    def _tab_changed(self, i: int) -> None:
        if 0 <= i < len(self.doc.sheets) and i != self.sheet_index:
            self.sheet_index = i
            self.history.end_merge()
            self._show_doc(reset=True, select=(0, 0))
            self._fit_columns()

    # ------------------------------------------------------------------ editing
    def _push(
        self, sheet: Sheet, label: str, merge: object = None, select: tuple[int, int] | None = None
    ) -> None:
        """Make ``sheet`` the current sheet (one undo step) and show it."""
        if sheet is self.sheet:
            return
        before = self.sheet
        self.history.push(table.with_sheet(self.doc, self.sheet_index, sheet), label, merge)
        reset = (len(before.rows), len(before.columns)) != (len(sheet.rows), len(sheet.columns))
        self._show_doc(reset=reset, select=select)

    def _edit(self, fn: Callable[[Sheet], Sheet], label: str, **kw) -> bool:
        try:
            sheet = fn(self.sheet)
        except EditError as exc:
            self.show_message(str(exc), error=True)
            return False
        self._push(sheet, label, **kw)
        return True

    def set_cell(self, r: int, c: int, text: str) -> bool:
        return self._edit(
            lambda s: table.set_cell(s, r, c, text),
            "Type",
            merge=f"cell:{self.sheet_index}:{r}:{c}",
        )

    def _typed_in_cell(self, r: int, c: int, text: str) -> None:
        self.set_cell(r, c, text)

    def _typed_in_box(self, text: str) -> None:
        r, c = self.current()
        if r >= 0:
            self.set_cell(r, c, text)

    def _double_clicked(self, index) -> None:
        if not self.model.flags(index) & Qt.ItemFlag.ItemIsEditable:
            self.cell_box.setFocus()  # several lines: edited in the box

    def add_row(self) -> bool:
        if not self.sheet.columns:
            return False
        r = self.current()[0]
        at = r + 1 if r >= 0 else len(self.sheet.rows)
        c = max(self.current()[1], 0)
        return self._edit(lambda s: table.insert_rows(s, at), "Add row", select=(at, c))

    def add_column(self, name: str | None = None) -> bool:
        c = self.current()[1]
        at = c + 1 if c >= 0 else len(self.sheet.columns)
        r = max(self.current()[0], 0)
        return self._edit(lambda s: table.insert_column(s, at, name), "Add column", select=(r, at))

    def delete_rows(self) -> bool:
        rows = self.chosen_rows()
        if not rows:
            return False
        r, c = self.current()
        n = len(rows)
        return self._edit(
            lambda s: table.delete_rows(s, rows),
            f"Delete {n} row{'s' if n != 1 else ''}",
            select=(rows[0], c),
        )

    def delete_columns(self) -> bool:
        cols = self.chosen_columns()
        if not cols:
            return False
        r = max(self.current()[0], 0)
        return self._edit(
            lambda s: table.delete_columns(s, cols), "Delete column", select=(r, cols[0])
        )

    def ask_rename(self, c: int) -> bool:
        if not 0 <= c < len(self.sheet.columns):
            return False
        if not self.sheet.header:
            self.show_message("Turn on “First row is the header” to name the columns.")
            return False
        name = self.ask_text("Rename column", "Column name:", self.sheet.columns[c])
        if name is None:
            return False
        return self.rename_column(c, name)

    def rename_column(self, c: int, name: str) -> bool:
        return self._edit(lambda s: table.rename_column(s, c, name), "Rename column")

    def sort(self, descending: bool) -> bool:
        c = self.current()[1]
        if c < 0:
            return False
        label = "Sort Z→A" if descending else "Sort A→Z"
        return self._edit(lambda s: table.sort_rows(s, c, descending), label, select=(0, c))

    def set_header(self, on: bool) -> bool:
        if self.doc.format not in HEADER_FORMATS:
            return False
        label = "First row is the header" if on else "First row is data"
        done = self._edit(lambda s: table.set_header(s, on), label)
        self._fit_columns()
        return done

    def clear_cells(self) -> bool:
        cells = self.chosen_cells()
        changes = [(r, c, "") for r, c in cells]
        return self._edit(lambda s: table.set_cells(s, changes), "Clear")

    # ------------------------------------------------------------------ copy / paste
    def _block(self) -> tuple[int, int, int, int] | None:
        """The rectangle around the chosen cells (top, left, bottom, right)."""
        cells = self.chosen_cells()
        if not cells:
            return None
        rows, cols = [r for r, _ in cells], [c for _, c in cells]
        return min(rows), min(cols), max(rows), max(cols)

    def copy(self) -> bool:
        box = self._block()
        if box is None:
            return False
        top, left, bottom, right = box
        rows = [
            [display(self.sheet.rows[r][c]) for c in range(left, right + 1)]
            for r in range(top, bottom + 1)
        ]
        QApplication.clipboard().setText(csvtext.write(rows, "\t", False, False))
        n = (bottom - top + 1) * (right - left + 1)
        self.show_message(f"Copied {n} cell{'s' if n != 1 else ''}.")
        return True

    def cut(self) -> bool:
        return self.copy() and self.clear_cells()

    def paste(self, text: str | None = None) -> bool:
        """Paste tab-separated text (from a spreadsheet, or a copy here) at the chosen cell;
        rows and columns are added when it doesn't fit. One undo step."""
        text = QApplication.clipboard().text() if text is None else text
        r0, c0 = self.current()
        if not text or r0 < 0:
            return False
        rows = csvtext.parse(text.replace("\r\n", "\n").replace("\r", "\n"), "\t").rows
        if not rows:
            return False
        width = max(len(r) for r in rows)

        def apply(s: Sheet) -> Sheet:
            extra_rows = r0 + len(rows) - len(s.rows)
            if extra_rows > 0:
                s = table.insert_rows(s, len(s.rows), extra_rows)
            for _ in range(c0 + width - len(s.columns)):
                s = table.insert_column(s, len(s.columns))
            changes = [(r0 + i, c0 + j, v) for i, row in enumerate(rows) for j, v in enumerate(row)]
            return table.set_cells(s, changes)

        return self._edit(apply, "Paste", select=(r0, c0))

    def _cell_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Copy", self.copy)
        menu.addAction("Cut", self.cut)
        menu.addAction("Paste", self.paste)
        menu.addAction("Clear", self.clear_cells)
        menu.addSeparator()
        menu.addAction("Add a row below", self.add_row)
        menu.addAction("Delete the chosen rows", self.delete_rows)
        self.exec_menu(menu, self.table.viewport().mapToGlobal(pos))

    def _column_menu(self, pos) -> None:
        c = self.table.horizontalHeader().logicalIndexAt(pos)
        if c < 0:
            return
        if self.sheet.rows:
            self.table.setCurrentIndex(self.model.index(max(self.current()[0], 0), c))
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self.ask_rename(c))
        menu.addAction("Sort A→Z", lambda: self.sort(False))
        menu.addAction("Sort Z→A", lambda: self.sort(True))
        menu.addSeparator()
        menu.addAction("Add a column to the right", self.add_column)
        menu.addAction("Delete column", self.delete_columns)
        self.exec_menu(menu, self.table.horizontalHeader().mapToGlobal(pos))

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

    # ------------------------------------------------------------------ find
    def _matches(self) -> list[tuple[int, int]] | None:
        try:
            return table.find(self.sheet, self.find_bar.query.text(), **self.find_bar.options())
        except search.SearchError as exc:
            self.find_bar.count.setText(str(exc))
            return None

    def _count_matches(self) -> None:
        found = self._matches()
        if found is not None:
            q = self.find_bar.query.text()
            self.find_bar.count.setText(f"{len(found)} found" if q else "")

    def find(self, direction: int) -> bool:
        """Go to the next (1) / previous (-1) cell that matches; False if none does."""
        found = self._matches()
        if not found:
            return False
        at = self.current()
        if direction > 0:
            hit = next((m for m in found if m > at), found[0])
        else:
            hit = next((m for m in reversed(found) if m < at), found[-1])
        self.table.setCurrentIndex(self.model.index(*hit))
        self.find_bar.count.setText(f"{found.index(hit) + 1} of {len(found)}")
        return True

    def replace_one(self) -> None:
        r, c = self.current()
        o = self.find_bar.options()
        query, repl = self.find_bar.query.text(), self.find_bar.replacement.text()
        if r >= 0 and (r, c) in (self._matches() or []):
            cell = self.sheet.rows[r][c]
            try:
                new, _n = search.replace_all(display(cell), query, repl, **o)
            except search.SearchError as exc:
                self.find_bar.count.setText(str(exc))
                return
            # text stays text (the ' keeps "12" from turning into a number); others are retyped
            text = edit_text(new, self.sheet.typed) if isinstance(cell, str) else new
            self._edit(lambda s: table.set_cells(s, [(r, c, text)]), "Replace")
        self.find(1)
        self._count_matches()

    def replace_all(self) -> None:
        o = self.find_bar.options()
        try:
            sheet, n = table.replace_all(
                self.sheet, self.find_bar.query.text(), self.find_bar.replacement.text(), **o
            )
        except search.SearchError as exc:
            self.find_bar.count.setText(str(exc))
            return
        if n:
            self._push(sheet, "Replace all")
        self.find_bar.count.setText(f"Replaced {n}")

    # ------------------------------------------------------------------ saving
    def save(self) -> bool:
        """Start saving; False if nothing was started (busy, refused, or Save as cancelled)."""
        if self.busy:
            return False
        if self.path is None:
            return self.save_as()
        return self._start_save(self.path, self.doc.format)

    def save_as(self) -> bool:
        if self.busy:
            return False
        fmt = self.doc.format
        fmts = (fmt, *(f for f in io.FORMATS if f != fmt))
        if self.source is not None:
            suggested = self.source.with_suffix("." + fmt)
        else:
            docs = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            suggested = Path(docs or Path.home()) / f"Table.{fmt}"
        path = self.choose_save_path(suggested, fmts)
        if path is None:
            return False
        ext = path.suffix.lower().lstrip(".")
        ext = "yaml" if ext == "yml" else ext
        if ext not in io.FORMATS:
            self.show_message(f"Tables can't be saved as .{ext} here.", error=True)
            return False
        return self._start_save(path, ext)

    def _start_save(self, path: Path, fmt: str) -> bool:
        doc = self.doc
        try:
            if fmt == doc.format:
                data = self._same_format_bytes(doc)
                if data is None:
                    return False
            else:
                data = export.export_bytes(self.opened, doc, fmt)
        except (ConversionError, ValueError) as exc:
            log.warning("saving as %s failed: %s", fmt, exc)
            self.show_message(f"The table couldn't be saved as {io.NAMES[fmt]}: {exc}", True)
            return False
        self._run({"kind": "save", "doc": doc, "fmt": fmt}, lambda: _write(path, data))
        return True

    def _same_format_bytes(self, doc: DataDoc) -> bytes | None:
        if self.opened.lost and doc != self.opened.doc and not self._asked_lost:
            answer = self.ask(
                "Some parts will be left out",
                f"Saving this {io.NAMES[doc.format]} file can't keep its "
                f"{' and '.join(self.opened.lost)}. Save anyway?",
                ("yes", "no"),
            )
            if answer != "yes":
                return None
            self._asked_lost = True
        try:
            return io.to_bytes(self.opened, doc)
        except ValueError as exc:
            if str(exc) != "encoding":
                raise
        answer = self.ask(
            "Save as UTF-8?",
            f"Some characters can't be stored in this file's encoding "
            f"({self.opened.file.encoding}). Save it as UTF-8 instead?",
            ("yes", "no"),
        )
        return io.to_bytes(self.opened, doc, utf8=True) if answer == "yes" else None

    def _saved(self, doc: DataDoc, fmt: str, path: Path, opened: io.Opened) -> None:
        if fmt != doc.format:  # now editing the new file
            self._load(path, opened)
            self.show_message(f"Saved “{path.name}”. Now editing the {io.NAMES[fmt]} file.")
            return
        self.opened = opened
        self.path = self.source = path
        self.history.mark_saved(doc)  # typed on while it saved: those changes stay unsaved
        self._update_title()
        self.show_message(f"Saved “{path.name}”.")

    def maybe_leave(self) -> bool:
        """True when the editor may close: nothing unsaved, or saved / discarded on request."""
        self.wait_idle()
        if not self._discard_ok():
            return False
        self.history.mark_saved()  # "Don't save": not asked again
        return True

    def show_shortcuts(self) -> None:
        ShortcutsWindow(self).exec()

    def shutdown(self) -> None:
        self.wait_idle()
