"""The vector editor page: a tool rail, the drawing (``canvas.VectorCanvas``), and on the right
the Layers list and the Appearance panel. Top bar: Open, New, Save, Save as, Export.

The document is ``umc.vectoredit`` (immutable XML that keeps its source + snapshot undo), so a
save rewrites only what changed. Reading, saving and exporting run on a QThread (results come
back through bound-method slots, queued onto the GUI thread)."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.errors import ConversionCancelled, ConversionError
from umc.core.filemode import match_mode
from umc.gui import icons, theme
from umc.gui.vectoreditor.canvas import VectorCanvas
from umc.gui.vectoreditor.panels import AppearancePanel, LayersPanel
from umc.gui.vectoreditor.shortcuts import SHORTCUTS
from umc.vectoredit import doc as vdoc
from umc.vectoredit import io, pathdata, shapes, xmlsrc
from umc.vectoredit.doc import Path as ElPath
from umc.vectoredit.doc import VectorDoc, VectorError
from umc.vectoredit.geom import Box, num
from umc.vectoredit.history import History

log = logging.getLogger(__name__)

OPEN_EXTS = ("svg", "svgz")
NUDGE = 1.0
NUDGE_BIG = 10.0
PASTE_STEP = 10.0
NEW_SIZES = (  # (label, width, height, unit)
    ("A4 portrait (210 × 297 mm)", 210, 297, "mm"),
    ("A4 landscape (297 × 210 mm)", 297, 210, "mm"),
    ("Letter (8.5 × 11 in)", 8.5, 11, "in"),
    ("Full HD (1920 × 1080 px)", 1920, 1080, "px"),
    ("Square (1000 × 1000 px)", 1000, 1000, "px"),
    ("Icon (512 × 512 px)", 512, 512, "px"),
)
TOOL_BUTTONS = (  # (tool, icon, label, key)
    ("select", "pointer", "Select", "V"),
    ("node", "point", "Node", "A"),
    ("rect", "square", "Rectangle", "R"),
    ("ellipse", "circle", "Ellipse", "E"),
    ("line", "line", "Line", "L"),
    ("pen", "vector-bezier-2", "Pen", "P"),
    ("text", "typography", "Text", "T"),
)


class UserError(Exception):
    """A problem to show as it is."""


def _read(path: Path) -> tuple[Path, io.Opened]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UserError(f"“{path.name}” couldn't be opened: {exc.strerror or exc}") from exc
    try:
        return path, io.open_bytes(data)
    except VectorError as exc:
        raise UserError(f"“{path.name}”: {exc}") from exc


def _write(path: Path, data: bytes) -> tuple[Path, io.Opened]:
    """Temp file in the same folder + os.replace, so a failed save never leaves half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".umc-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        match_mode(tmp, path)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise UserError(f"“{path.name}” couldn't be saved: {exc.strerror or exc}") from exc
    return path, io.open_bytes(data)


def _export(doc: VectorDoc, path: Path, fmt: str) -> Path:
    from umc.vectoredit import export

    try:
        return export.export_file(doc, path, fmt)
    except ConversionCancelled:
        raise
    except ConversionError as exc:
        raise UserError(str(exc)) from exc


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
            log.exception("vector editor job failed")
            self.failed.emit(self.job, "Something went wrong. The details are in the log.")
        else:
            self.done.emit(self.job, result)


class NewDrawingDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New drawing")
        form = QFormLayout(self)
        self.preset = QComboBox()
        for label, *_rest in NEW_SIZES:
            self.preset.addItem(label)
        self.preset.addItem("Custom size")
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for box in (self.width, self.height):
            box.setRange(0.01, 100000)
            box.setDecimals(2)
        self.unit = QComboBox()
        self.unit.addItems(["px", "mm", "in"])
        form.addRow("Size", self.preset)
        form.addRow("Width", self.width)
        form.addRow("Height", self.height)
        form.addRow("Unit", self.unit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.preset.currentIndexChanged.connect(self._preset)
        self._preset(0)

    def _preset(self, i: int) -> None:
        if 0 <= i < len(NEW_SIZES):
            _label, w, h, unit = NEW_SIZES[i]
            self.width.setValue(w)
            self.height.setValue(h)
            self.unit.setCurrentText(unit)

    def values(self) -> tuple[float, float, str]:
        return self.width.value(), self.height.value(), self.unit.currentText()


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


class VectorEditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VectorEditorPage")
        self.opened: io.Opened = io.new_document(800, 600)
        self.history = History(self.opened.doc)
        self.path: Path | None = None  # where Save writes (None: ask first)
        self.source: Path | None = None
        self._jobs: dict[int, tuple[QThread, _Worker]] = {}
        self._next_job = 0
        self._paste_count = 0
        self._last_copy = ""
        self.actions: dict[str, Callable[[], object]] = {}

        self.canvas = VectorCanvas()
        self.canvas.edited.connect(self._canvas_edited)
        self.canvas.selection_changed.connect(self._selection_changed)
        self.canvas.view_changed.connect(self._update_status)
        self.canvas.tool_changed.connect(self._tool_changed)
        self.canvas.text_requested.connect(self._add_text_at)
        self.canvas.text_edit_requested.connect(self.edit_text)
        self.canvas.installEventFilter(self)

        self.layers = LayersPanel()
        self.layers.chosen.connect(self._layers_chosen)
        self.layers.visibility.connect(self._layer_visibility)
        self.layers.rename_requested.connect(self.ask_rename)
        self.layers.menu_requested.connect(self._layers_menu)
        self.appearance = AppearancePanel(self)

        right = QWidget(objectName="VectorSide")
        side = QVBoxLayout(right)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(QLabel("  Layers", objectName="Section"))
        side.addWidget(self.layers, 1)
        side.addWidget(self.appearance)
        right.setMinimumWidth(240)

        middle = QSplitter()
        middle.addWidget(self.canvas)
        middle.addWidget(right)
        middle.setStretchFactor(0, 1)
        middle.setSizes([900, 280])

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_tool_rail())
        body.addWidget(middle, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addLayout(body, 1)
        root.addWidget(self._build_status_bar())
        self._install_shortcuts()
        self._show_doc(fit=True)

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
        tb("Open…", "Open a drawing (Ctrl+O)", self.choose_files)
        tb("New…", "New drawing (Ctrl+N)", self.ask_new)
        self.title = QLabel()
        lay.addSpacing(8)
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.undo_button = tb("Undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_button = tb("Redo", "Redo (Ctrl+Y)", self.redo)
        tb("?", "Keyboard shortcuts (F1)", self.show_shortcuts)
        tb("Export…", "Export as PDF, PNG, JPG … (Ctrl+E)", self.export)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", self.save_as)
        self.save_button = tb("Save", "Save (Ctrl+S)", self.save, "Primary")
        return bar

    def _build_tool_rail(self) -> QWidget:
        rail = QFrame(objectName="ToolRail")
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(4)
        self.tool_buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        muted = theme.palette()["muted"]
        for tool, icon, label, key in TOOL_BUTTONS:
            b = QToolButton()
            b.setObjectName("RailTool")
            b.setCheckable(True)
            b.setIcon(icons.icon(icon, muted, 20, theme.palette()["accent"]))
            b.setToolTip(f"{label} ({key})")
            b.setAccessibleName(label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c=False, t=tool: self.set_tool(t))
            group.addButton(b)
            lay.addWidget(b)
            self.tool_buttons[tool] = b
        self.tool_buttons["select"].setChecked(True)
        lay.addStretch(1)
        return rail

    def _build_status_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorStatusBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.zoom_label = QLabel(objectName="Muted")
        lay.addWidget(self.message, 1)
        for text, tip, fn in (
            ("−", "Zoom out (Ctrl+-)", lambda: self.canvas.zoom_by(1 / 1.25)),
            ("Fit", "Fit the page (Ctrl+0)", self.canvas.fit),
            ("+", "Zoom in (Ctrl+=)", lambda: self.canvas.zoom_by(1.25)),
        ):
            b = QPushButton(text, objectName="Small")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c=False, f=fn: f())
            lay.addWidget(b)
        lay.addWidget(self.zoom_label)
        return bar

    def _install_shortcuts(self) -> None:
        self.actions = {
            "new": self.ask_new,
            "open": self.choose_files,
            "save": self.save,
            "save_as": self.save_as,
            "export": self.export,
            "undo": self.undo,
            "redo": self.redo,
            "copy": self.copy,
            "cut": self.cut,
            "paste": self.paste,
            "duplicate": self.duplicate,
            "delete": self.delete,
            "select_all": self.select_all,
            "deselect": self.escape,
            "group": self.group,
            "ungroup": self.ungroup,
            "forward": lambda: self.reorder("forward"),
            "backward": lambda: self.reorder("backward"),
            "front": lambda: self.reorder("front"),
            "back": lambda: self.reorder("back"),
            "to_path": self.to_path,
            "nudge_left": lambda: self.nudge(-1, 0),
            "nudge_right": lambda: self.nudge(1, 0),
            "nudge_up": lambda: self.nudge(0, -1),
            "nudge_down": lambda: self.nudge(0, 1),
            "finish": self.canvas.finish_pen,
            "fit": self.canvas.fit,
            "actual": lambda: self.canvas.set_zoom(1.0),
            "zoom_in": lambda: self.canvas.zoom_by(1.25),
            "zoom_out": lambda: self.canvas.zoom_by(1 / 1.25),
            "help": self.show_shortcuts,
        }
        for tool, *_rest in TOOL_BUTTONS:
            self.actions[f"tool_{tool}"] = lambda t=tool: self.set_tool(t)
        self._canvas_keys: list[tuple[QKeySequence, str]] = []
        for s in SHORTCUTS:
            for key in s.keys:
                seq = QKeySequence(key)
                if s.canvas:  # the drawing's own keys: see eventFilter
                    self._canvas_keys.append((seq, s.id))
                    continue
                sc = QShortcut(seq, self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda f=self.actions[s.id]: f())

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.canvas and e.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            combo = e.keyCombination()
            shift = bool(combo.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            pressed = QKeySequence(combo)
            for seq, sid in self._canvas_keys:
                if seq == pressed or (
                    sid.startswith("nudge_") and shift and self._plain(combo) == seq
                ):
                    if e.type() == QEvent.Type.ShortcutOverride:
                        e.accept()  # ours, not a window shortcut's
                        return True
                    if sid.startswith("nudge_"):
                        self.nudge(*self._nudge_dir(sid), big=shift)
                    else:
                        self.actions[sid]()
                    return True
        return super().eventFilter(obj, e)

    @staticmethod
    def _plain(combo) -> QKeySequence:
        return QKeySequence(combo.key())

    @staticmethod
    def _nudge_dir(sid: str) -> tuple[int, int]:
        return {
            "nudge_left": (-1, 0),
            "nudge_right": (1, 0),
            "nudge_up": (0, -1),
            "nudge_down": (0, 1),
        }[sid]

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

    def ask_colour(self, current: str | None) -> str | None:
        start = QColor(current) if current and QColor(current).isValid() else QColor("#808080")
        c = QColorDialog.getColor(start, self, "Choose a colour")
        return c.name() if c.isValid() else None

    def ask_size(self) -> tuple[float, float, str] | None:
        dialog = NewDrawingDialog(self)
        return dialog.values() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def choose_save_path(self, suggested: Path, filters: str) -> Path | None:
        path, _chosen = QFileDialog.getSaveFileName(self, "Save as", str(suggested), filters)
        return Path(path) if path else None

    def choose_open_paths(self) -> list[Path]:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open a drawing", "", "SVG drawings (*.svg *.svgz);;All files (*)"
        )
        return [Path(p) for p in paths]

    def exec_menu(self, menu: QMenu, pos) -> None:
        menu.exec(pos)

    # ------------------------------------------------------------------ state
    @property
    def doc(self) -> VectorDoc:
        return self.history.doc

    @property
    def selection(self) -> list[ElPath]:
        return self.canvas.selection

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    @property
    def dirty(self) -> bool:
        return self.history.dirty

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _show_doc(self, fit: bool = False) -> None:
        self.canvas.set_document(self.doc, fit=fit)
        self.layers.show_document(self.doc, self.selection)
        self.appearance.show_selection(self.doc, self.selection)
        self._update_title()
        self._update_buttons()
        self._update_status()

    def _update_title(self) -> None:
        name = self.source.name if self.source is not None else "Untitled drawing"
        self.title.setText(("• " if self.dirty else "") + name)

    def _update_buttons(self) -> None:
        idle = not self.busy
        self.save_button.setEnabled(idle)
        self.save_as_button.setEnabled(idle)
        self.undo_button.setEnabled(self.history.can_undo)
        self.redo_button.setEnabled(self.history.can_redo)

    def _update_status(self) -> None:
        self.zoom_label.setText(f"{round(self.canvas.scale * 100)} %")

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
        elif job["kind"] == "save":
            self._saved(job["doc"], *result)
        else:
            self.show_message(f"Exported to {result.name}.")

    @Slot(object, str)
    def _job_failed(self, _job: dict, message: str) -> None:
        self.show_message(message, error=True)

    def wait_idle(self) -> None:
        """Wait for running jobs (tests, leaving): their results are delivered first."""
        while self._jobs:
            for thread, _worker in list(self._jobs.values()):
                thread.wait(50)
            QGuiApplication.processEvents()

    # ------------------------------------------------------------------ opening
    def choose_files(self) -> None:
        paths = self.choose_open_paths()
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths: list[Path]) -> bool:
        """Open the first SVG in ``paths`` (asks about unsaved changes first)."""
        path = next(
            (Path(p) for p in paths if Path(p).suffix.lower().lstrip(".") in OPEN_EXTS), None
        )
        if path is None:
            self.show_message("Choose an SVG drawing (.svg or .svgz).", True)
            return False
        if self.busy or not self._discard_ok():
            return False
        self._run({"kind": "open"}, lambda: _read(path))
        return True

    def ask_new(self) -> bool:
        if self.busy or not self._discard_ok():
            return False
        size = self.ask_size()
        if size is None:
            return False
        return self.new_drawing(*size, asked=True)

    def open_empty(self) -> bool:
        """The start-screen tile: a new drawing of the size the user picks."""
        return self.ask_new()

    def new_drawing(
        self, width: float, height: float, unit: str = "px", asked: bool = False
    ) -> bool:
        if not asked and (self.busy or not self._discard_ok()):
            return False
        self._load(None, io.new_document(width, height, unit))
        return True

    def _discard_ok(self) -> bool:
        if not self.dirty:
            return True
        answer = self.ask(
            "Unsaved changes",
            "Save the changes to the drawing first?",
            ("save", "discard", "cancel"),
        )
        if answer == "discard":
            return True
        if answer == "save" and self.save():
            self.wait_idle()
            return not self.dirty
        return False

    def _load(self, path: Path | None, opened: io.Opened) -> None:
        self.opened = opened
        self.history = History(opened.doc)
        self.path = path
        self.source = path
        self.canvas.selection = []
        self._paste_count = 0
        self._show_doc(fit=True)
        self.show_message(f"Opened {path.name}." if path else "A new drawing.")

    # ------------------------------------------------------------------ editing
    def _push(self, doc: VectorDoc, label: str, merge: str | None = None, select=None) -> bool:
        if doc is self.doc:
            return False
        self.history.push(doc, label, merge)
        if select is not None:
            self.canvas.selection = [tuple(p) for p in select]
        self._show_doc()
        self.show_message(label)
        return True

    def _edit(
        self, fn: Callable[[VectorDoc], VectorDoc], label: str, merge: str | None = None
    ) -> bool:
        try:
            return self._push(fn(self.doc), label, merge)
        except VectorError as exc:
            self.show_message(str(exc), error=True)
            return False

    def _canvas_edited(self, doc: VectorDoc, label: str, merge) -> None:
        self._push(doc, label, merge)

    def _selection_changed(self) -> None:
        self.history.end_merge()
        self.layers.show_document(self.doc, self.selection)
        self.appearance.show_selection(self.doc, self.selection)

    def _layers_chosen(self, paths: list) -> None:
        self.canvas.set_selection([tuple(p) for p in paths])

    def _layer_visibility(self, path, shown: bool) -> None:
        self._edit(
            lambda d: vdoc.set_hidden(d, [tuple(path)], not shown), "Show" if shown else "Hide"
        )

    def set_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        if tool == "node" and self.selection:
            el = vdoc.get(self.doc, self.selection[0])
            if shapes.kind(el) in shapes.GEOMETRY:
                self.show_message(
                    "Double-click the shape (or press Ctrl+Shift+C) to turn it into a path."
                )
        self.canvas.setFocus()

    def _tool_changed(self, tool: str) -> None:
        self.tool_buttons[tool].setChecked(True)
        hints = {
            "select": "Click to choose, drag to move, Shift+click adds; double-click text to type.",
            "node": "Drag a path's points and handles. Double-click its line to add a point.",
            "rect": "Drag to draw a rectangle (Shift: square).",
            "ellipse": "Drag to draw an ellipse (Shift: circle).",
            "line": "Drag to draw a line.",
            "pen": "Click for corners, drag for curves; Enter ends, the first point closes.",
            "text": "Click where the text should start.",
        }
        self.show_message(hints[tool])

    def select_all(self) -> None:
        self.canvas.set_selection(vdoc.in_box(self.doc, Box(-1e12, -1e12, 1e12, 1e12)))

    def escape(self) -> None:
        if not self.canvas.cancel_gesture():
            self.canvas.set_selection([])

    def delete(self) -> bool:
        if self.canvas.tool == "node" and self.canvas.node is not None and self.selection:
            return self.delete_point()
        if not self.selection:
            return False
        paths = list(self.selection)
        self.canvas.selection = []
        return self._edit(lambda d: vdoc.delete(d, paths), "Delete")

    def delete_point(self) -> bool:
        path = self.selection[0]
        el = vdoc.get(self.doc, path)
        segs = pathdata.parse(el.get("d") or "")
        index = self.canvas.node[0]
        self.canvas.node = None
        new = pathdata.delete_point(segs, index)
        if not new:
            self.canvas.selection = []
            return self._edit(lambda d: vdoc.delete(d, [path]), "Delete")
        return self._edit(lambda d: vdoc.set_path(d, path, new), "Delete point")

    def duplicate(self) -> bool:
        if not self.selection:
            return False
        try:
            doc, paths = vdoc.duplicate(self.doc, self.selection)
        except VectorError as exc:
            self.show_message(str(exc), error=True)
            return False
        return self._push(doc, "Duplicate", select=paths)

    def group(self) -> bool:
        if len(self.selection) < 2:
            self.show_message("Choose two or more things to group.", error=True)
            return False
        try:
            doc, path = vdoc.group(self.doc, self.selection)
        except VectorError as exc:
            self.show_message(str(exc), error=True)
            return False
        return self._push(doc, "Group", select=[path])

    def ungroup(self) -> bool:
        if len(self.selection) != 1:
            return False
        try:
            doc, paths = vdoc.ungroup(self.doc, self.selection[0])
        except VectorError as exc:
            self.show_message(str(exc), error=True)
            return False
        return self._push(doc, "Ungroup", select=paths)

    def reorder(self, where: str) -> bool:
        if len(self.selection) != 1:
            return False
        doc, path = vdoc.reorder(self.doc, self.selection[0], where)
        labels = {
            "forward": "Bring forward",
            "backward": "Send backward",
            "front": "Bring to front",
            "back": "Send to back",
        }
        return self._push(doc, labels[where], select=[path])

    def to_path(self) -> bool:
        if not self.selection:
            return False
        return self._edit(lambda d: vdoc.to_path(d, self.selection), "Turn into path")

    def nudge(self, dx: int, dy: int, big: bool = False) -> bool:
        if not self.selection:
            return False
        step = NUDGE_BIG if big else NUDGE
        return self._edit(
            lambda d: vdoc.move(d, self.selection, dx * step, dy * step), "Move", merge="nudge"
        )

    # ------------------------------------------------------------------ appearance
    def choose_colour(self, which: str) -> bool:
        if not self.selection:
            return False
        current = shapes.style_get(vdoc.get(self.doc, self.selection[0]), which)
        value = self.ask_colour(current)
        return value is not None and self.set_colour(which, value)

    def set_colour(self, which: str, value: str) -> bool:
        self._remember_style(which, value)
        if not self.selection:
            return False
        label = "Fill" if which == "fill" else "Outline"
        return self._edit(lambda d: vdoc.set_style(d, self.selection, which, value), label)

    def set_fill(self, value: str) -> bool:
        return self.set_colour("fill", value)

    def set_stroke(self, value: str) -> bool:
        return self.set_colour("stroke", value)

    def _remember_style(self, name: str, value: str) -> None:
        """New shapes use the colours chosen last."""
        style = [(k, v) for k, v in self.canvas.style_for_new if k != name]
        self.canvas.style_for_new = [*style, (name, value)]

    def set_stroke_width(self, width: float) -> bool:
        if not self.selection:
            return False
        return self._edit(
            lambda d: vdoc.set_style(d, self.selection, "stroke-width", num(width)), "Outline width"
        )

    def set_opacity(self, value: float) -> bool:
        if not self.selection:
            return False
        v = None if value >= 1 else num(max(0.0, value))
        return self._edit(lambda d: vdoc.set_style(d, self.selection, "opacity", v), "Opacity")

    def set_geometry(self, x: float, y: float, w: float, h: float) -> bool:
        box = vdoc.selection_bounds(self.doc, self.selection) if self.selection else None
        if box is None:
            return False
        w = w if box.width > 0 else 0
        h = h if box.height > 0 else 0

        def change(d: VectorDoc) -> VectorDoc:
            if (w, h) != (box.width, box.height) and w > 0 and h > 0:
                return vdoc.resize(d, self.selection, Box(x, y, x + w, y + h))
            return vdoc.move(d, self.selection, x - box.x0, y - box.y0)

        return self._edit(change, "Position and size")

    # ------------------------------------------------------------------ text
    def _add_text_at(self, x: float, y: float) -> None:
        self.add_text(x, y)

    def add_text(self, x: float, y: float, text: str | None = None) -> bool:
        if text is None:
            text = self.ask_text("Text", "Type the text:", "")
        if not text:
            return False
        fill = next((v for k, v in self.canvas.style_for_new if k == "fill"), "#000000")
        attrs = [
            ("id", vdoc.unique_id(vdoc.ids(self.doc), "text")),
            ("x", num(x)),
            ("y", num(y)),
            (
                "style",
                shapes.write_style(
                    [("font-size", "24px"), ("font-family", "sans-serif"), ("fill", fill)]
                ),
            ),
        ]
        el = xmlsrc.element("text", attrs, [xmlsrc.Text(xmlsrc.escape_text(text))])
        doc, path = vdoc.add(self.doc, el)
        self.set_tool("select")
        return self._push(doc, "Text", select=[path])

    def edit_text(self, path) -> bool:
        el = vdoc.get(self.doc, tuple(path))
        text = self.ask_text("Text", "Change the text:", el.text())
        if text is None:
            return False
        return self.set_text_content(text, tuple(path))

    def set_text_content(self, text: str, path: ElPath | None = None) -> bool:
        path = path or (self.selection[0] if len(self.selection) == 1 else None)
        if path is None:
            return False
        return self._edit(lambda d: vdoc.set_text(d, path, text), "Text", merge=f"text:{path}")

    # ------------------------------------------------------------------ names, menus
    def ask_rename(self, path) -> bool:
        path = tuple(path)
        el = vdoc.get(self.doc, path)
        name = self.ask_text("Rename", "Name (the element's id):", el.get("id") or "")
        if name is None:
            return False
        return self._edit(lambda d: vdoc.rename(d, path, name), "Rename")

    def _layers_menu(self, pos) -> None:
        menu = QMenu(self)
        one = len(self.selection) == 1
        for text, fn, on in (
            ("Rename…", lambda: self.ask_rename(self.selection[0]), one),
            ("Duplicate", self.duplicate, bool(self.selection)),
            ("Delete", self.delete, bool(self.selection)),
            ("Group", self.group, len(self.selection) > 1),
            ("Ungroup", self.ungroup, one),
            ("Turn into path", self.to_path, bool(self.selection)),
            ("Bring to front", lambda: self.reorder("front"), one),
            ("Send to back", lambda: self.reorder("back"), one),
        ):
            act = menu.addAction(text, fn)
            act.setEnabled(on)
        self.exec_menu(menu, pos)

    # ------------------------------------------------------------------ clipboard
    def copy(self) -> bool:
        if not self.selection:
            return False
        text = vdoc.copy_text(self.doc, self.selection)
        QGuiApplication.clipboard().setText(text)  # plain text: Qt owns it (safe at exit)
        self._last_copy = text
        self._paste_count = 0
        self.show_message("Copied.")
        return True

    def cut(self) -> bool:
        return self.copy() and self.delete()

    def paste(self, text: str | None = None) -> bool:
        if text is None:
            text = QGuiApplication.clipboard().text()
        head = text.lstrip()[:200]
        if not (head.startswith("<svg") or head.startswith("<?xml") or head.startswith("<!--")):
            self.show_message("The clipboard holds no drawing.", error=True)
            return False
        if text == self._last_copy:
            self._paste_count += 1
        offset = PASTE_STEP * self._paste_count
        try:
            doc, paths = vdoc.paste_text(self.doc, text, offset, offset)
        except VectorError as exc:
            self.show_message(str(exc), error=True)
            return False
        return self._push(doc, "Paste", select=paths)

    # ------------------------------------------------------------------ undo
    def undo(self) -> None:
        label = self.history.undo()
        if label:
            self._show_doc()
            self.show_message(f"Undid: {label}")

    def redo(self) -> None:
        label = self.history.redo()
        if label:
            self._show_doc()
            self.show_message(f"Redid: {label}")

    # ------------------------------------------------------------------ saving
    def save(self) -> bool:
        if self.busy:
            return False
        if self.path is None:
            return self.save_as()
        return self._start_save(self.path)

    def save_as(self) -> bool:
        if self.busy:
            return False
        suggested = self._suggested("svgz" if self.opened.gz else "svg")
        path = self.choose_save_path(suggested, "SVG drawing (*.svg);;Compressed SVG (*.svgz)")
        if path is None:
            return False
        ext = path.suffix.lower().lstrip(".")
        if ext not in OPEN_EXTS:
            path = path.with_name(path.name + ".svg")
        return self._start_save(path)

    def _suggested(self, ext: str) -> Path:
        if self.source is not None:
            return self.source.with_suffix("." + ext)
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return Path(docs or Path.home()) / f"Drawing.{ext}"

    def _start_save(self, path: Path) -> bool:
        doc = self.doc
        want_gz = path.suffix.lower() == ".svgz"
        opened = self.opened
        if want_gz != opened.gz:  # the other kind: written fresh
            opened = dataclasses.replace(opened, gz=want_gz, new=True)
        try:
            data = io.to_bytes(opened, doc)
        except ValueError as exc:
            if str(exc) != "encoding":
                raise
            answer = self.ask(
                "Save as UTF-8?",
                f"Some characters can't be stored in this file's encoding "
                f"({opened.file.encoding}). Save it as UTF-8 instead?",
                ("yes", "no"),
            )
            if answer != "yes":
                return False
            data = io.to_bytes(opened, doc, utf8=True)
        self._run({"kind": "save", "doc": doc}, lambda: _write(path, data))
        return True

    def _saved(self, doc: VectorDoc, path: Path, opened: io.Opened) -> None:
        # The file now holds ``doc``: keep editing the same snapshots, read back from disk.
        self.opened = io.Opened(doc, opened.file, opened.gz, False, opened.data)
        self.history.mark_saved(doc)
        self.path = path
        self.source = path
        self._update_title()
        self.show_message(f"Saved {path.name}.")

    def export(self) -> bool:
        if self.busy:
            return False
        from umc.vectoredit import export

        filters = ";;".join(f"{export.NAMES[f]} (*.{f})" for f in export.FORMATS)
        path = self.choose_save_path(self._suggested("pdf"), filters)
        if path is None:
            return False
        fmt = path.suffix.lower().lstrip(".")
        fmt = "jpg" if fmt == "jpeg" else "tiff" if fmt == "tif" else fmt
        if fmt not in export.FORMATS:
            path = path.with_name(path.name + ".pdf")
            fmt = "pdf"
        doc = self.doc
        self._run({"kind": "export"}, lambda: _export(doc, path, fmt))
        self.show_message(f"Exporting {path.name}…")
        return True

    # ------------------------------------------------------------------ leaving
    def maybe_leave(self) -> bool:
        self.wait_idle()
        if not self._discard_ok():
            return False
        self.history.mark_saved()
        return True

    def show_shortcuts(self) -> None:
        ShortcutsWindow(self).exec()

    def shutdown(self) -> None:
        self.wait_idle()
