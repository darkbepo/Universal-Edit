"""The image editor page: top bar, tool bar, canvas, settings / colours / layers, status line."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.errors import ConversionError
from umc.editor import ops
from umc.editor.document import Document, check_size
from umc.editor.history import History
from umc.editor.io import load_layers, open_files
from umc.editor.save import SAVE_FORMATS, save_document
from umc.editor.tools import TOOLS
from umc.editor.tools.base import ToolContext
from umc.editor.tools.select import clear_selection, deselect, select_all
from umc.editor.tools.text import add_text_layer
from umc.gui import theme
from umc.gui.editor.canvas import EditorCanvas
from umc.gui.editor.dialogs import (
    BrightnessContrastDialog,
    CanvasSizeDialog,
    ResizeDialog,
    SaveOptionsDialog,
)
from umc.gui.editor.layers_panel import LayersPanel
from umc.gui.editor.side_panel import ColorSwatches, ToolBar, ToolSettings

log = logging.getLogger(__name__)

EXT_ALIASES = {"jpeg": "jpg", "tif": "tiff", "heif": "heic"}


class _SaveWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, doc: Document, path: Path, target: str, values: dict) -> None:
        super().__init__()
        self.doc, self.path, self.target, self.values = doc, path, target, values

    @Slot()
    def run(self) -> None:
        try:
            save_document(self.doc, self.path, self.target, self.values)
            self.succeeded.emit(str(self.path))
        except ConversionError as exc:
            log.error("editor save failed: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("editor save failed")
            self.failed.emit(f"Couldn't save “{self.path.name}”. See the log for details.")


class TextBox(QPlainTextEdit):
    """On-canvas text entry. Ctrl+Enter, Esc or clicking elsewhere commits."""

    committed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TextBox")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.textChanged.connect(self._fit)

    def _fit(self) -> None:
        fm = self.fontMetrics()
        lines = self.toPlainText().split("\n") or [""]
        w = max(fm.horizontalAdvance(line) for line in lines) + fm.averageCharWidth() * 2 + 12
        h = fm.lineSpacing() * len(lines) + 14
        self.resize(max(60, w), max(fm.lineSpacing() + 14, h))

    def keyPressEvent(self, e) -> None:  # noqa: N802
        enter = e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if e.key() == Qt.Key.Key_Escape or (
            enter and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.committed.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self.committed.emit()


class EditorPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorPage")
        self.doc: Document | None = None
        self.history: History | None = None
        self.sources: list[Path] = []
        self.tools = {cls.id: cls() for cls in TOOLS}
        self.tool = self.tools["brush"]
        self.text_box: TextBox | None = None
        self._text_anchor = (0.0, 0.0)
        self._committing = False
        self.saving = False
        self._save_thread: QThread | None = None
        self._save_worker: _SaveWorker | None = None
        self._last_save: tuple[Path, str, dict] | None = None
        self._last_save_ok = False
        self._save_values: dict[str, dict[str, Any]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        icon_color = theme.palette()["text"]
        self.tool_bar = ToolBar(icon_color)
        self.tool_bar.tool_selected.connect(self.select_tool)
        middle.addWidget(self.tool_bar)

        self.canvas = EditorCanvas()
        self.canvas.context_provider = self.context
        self.canvas.changed.connect(self._on_changed)
        self.canvas.color_picked.connect(lambda c, b: self.swatches.set_color(c, b))
        self.canvas.zoom_changed.connect(lambda _z: self._update_info())
        self.canvas.cursor_moved.connect(lambda x, y: self._update_info((x, y)))
        self.canvas.text_requested.connect(self._open_text_box)
        middle.addWidget(self.canvas, 1)
        middle.addWidget(self._build_side())
        root.addLayout(middle, 1)

        status = QFrame(objectName="EditorStatus")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.info = QLabel(objectName="Muted")
        sl.addWidget(self.message, 1)
        sl.addWidget(self.info)
        root.addWidget(status)

        self._install_shortcuts()
        self.select_tool("brush")
        self._update_buttons()

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

        tb("← Back to Convert", "Leave the editor", self.back_requested.emit, "Link")
        lay.addSpacing(12)
        self.undo_button = tb("Undo", "Undo (Ctrl+Z)", lambda: self.canvas.undo())
        self.redo_button = tb("Redo", "Redo (Ctrl+Y)", lambda: self.canvas.redo())
        lay.addSpacing(12)
        tb("−", "Zoom out", lambda: self.canvas.zoom_out())
        tb("+", "Zoom in", lambda: self.canvas.zoom_in())
        tb("Fit", "Fit to window (Ctrl+0)", lambda: self.canvas.fit())
        tb("100 %", "Actual pixels (Ctrl+1)", lambda: self.canvas.set_zoom(1.0))
        lay.addSpacing(12)
        image = QToolButton(text="Image ▾", objectName="SmallMenu")
        image.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(image)
        for label, op in (
            ("Resize image…", "resize"),
            ("Canvas size…", "canvas_size"),
            ("Crop to selection", "crop_selection"),
            (None, None),
            ("Rotate 90° right", "rotate_right"),
            ("Rotate 90° left", "rotate_left"),
            ("Rotate 180°", "rotate_180"),
            ("Flip horizontal", "flip_h"),
            ("Flip vertical", "flip_v"),
            (None, None),
            ("Brightness / contrast…", "brightness"),
        ):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label, lambda o=op: self.run_op(o))
        layer_menu = menu.addMenu("Layer only")
        for label, op in (
            ("Rotate 90° right", "layer_rotate_right"),
            ("Rotate 90° left", "layer_rotate_left"),
            ("Rotate 180°", "layer_rotate_180"),
            ("Flip horizontal", "layer_flip_h"),
            ("Flip vertical", "layer_flip_v"),
        ):
            layer_menu.addAction(label, lambda o=op: self.run_op(o))
        image.setMenu(menu)
        lay.addWidget(image)
        lay.addStretch(1)
        self.save_as_button = tb("Save as…", "Save as (Ctrl+Shift+S)", lambda: self.save_as())
        self.save_button = tb("Save", "Save (Ctrl+S)", lambda: self.save(), "Primary")
        return bar

    def _build_side(self) -> QWidget:
        scroll = QScrollArea(objectName="EditorSide")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(290)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(objectName="EditorSideContent")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        self.tool_settings = ToolSettings()
        self.tool_settings.changed.connect(self.canvas.update)
        lay.addWidget(self.tool_settings)
        lay.addWidget(QLabel("COLOURS", objectName="Section"))
        self.swatches = ColorSwatches()
        lay.addWidget(self.swatches)
        self.layers = LayersPanel()
        self.layers.command.connect(self._push)
        self.layers.active_changed.connect(lambda _i: self._update_info())
        lay.addWidget(self.layers, 1)
        scroll.setWidget(host)
        return scroll

    def _install_shortcuts(self) -> None:
        def sc(keys, fn) -> None:
            for k in keys if isinstance(keys, tuple) else (keys,):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                s.activated.connect(fn)

        for cls in TOOLS:
            if cls.shortcut != "U":
                sc(cls.shortcut, lambda tid=cls.id: self.select_tool(tid))
        sc("U", self._toggle_shape)
        sc("X", self.swatches.swap)
        sc("[", lambda: self.tool_settings.change_size(self.tool, 1 / 1.2))
        sc("]", lambda: self.tool_settings.change_size(self.tool, 1.2))
        sc(("Ctrl+Z",), lambda: self.canvas.undo())
        sc(("Ctrl+Y", "Ctrl+Shift+Z"), lambda: self.canvas.redo())
        sc("Ctrl+S", lambda: self.save())
        sc("Ctrl+Shift+S", lambda: self.save_as())
        sc("Ctrl+A", lambda: self._push(select_all(self.doc)) if self.doc else None)
        sc("Ctrl+D", lambda: self._push(deselect(self.doc)) if self.doc else None)
        sc(
            ("Delete", "Backspace"),
            lambda: self._push(clear_selection(self.doc)) if self.doc else None,
        )
        sc("Ctrl+0", lambda: self.canvas.fit())
        sc("Ctrl+1", lambda: self.canvas.set_zoom(1.0))
        sc(("Return", "Enter"), self._apply_crop)

    # ------------------------------------------------------------ document
    def open_paths(self, paths: list[Path]) -> bool:
        try:
            doc, errors = open_files([Path(p) for p in paths])
        except ConversionError as exc:
            self.show_message(str(exc), error=True)
            return False
        self.set_document(doc, [Path(p) for p in paths])
        if errors:
            self.show_message(" ".join(errors), error=True)
        return True

    def set_document(self, doc: Document, sources: list[Path] | None = None) -> None:
        self.doc = doc
        self.history = History(doc)
        self.sources = sources or []
        self._last_save = None
        self.canvas.set_document(doc, self.history)
        self.layers.refresh(doc)
        self.show_message("")
        self._update_buttons()
        self._update_info()

    def add_paths(self, paths: list[Path]) -> None:
        """Dropped images become new layers above the selected one."""
        if self.doc is None:
            self.open_paths(paths)
            return
        layers, (w, h), errors = load_layers([Path(p) for p in paths])
        try:
            check_size(max(w, 1), max(h, 1))
        except ConversionError as exc:
            self.show_message(str(exc), error=True)
            return
        self._push(ops.add_layers(self.doc, layers))
        if errors:
            self.show_message(" ".join(errors), error=True)

    def context(self) -> ToolContext:
        return ToolContext(
            primary=self.swatches.primary,
            secondary=self.swatches.secondary,
            options=self.tool_settings.options_for(self.tool),
            font_family=self.tool_settings.font_family(),
        )

    def select_tool(self, tool_id: str) -> None:
        if self.text_box is not None:
            self.commit_text()
        self.tool = self.tools[tool_id]
        self.canvas.set_tool(self.tool)
        self.tool_bar.select(tool_id)
        self.tool_settings.show_tool(self.tool)

    def _toggle_shape(self) -> None:
        self.select_tool("ellipse" if self.tool.id == "rect" else "rect")

    # ------------------------------------------------------------ changes
    def _push(self, cmd) -> None:
        if cmd is None or self.doc is None:
            return
        size = (self.doc.width, self.doc.height)
        self.canvas.push(cmd)
        self.canvas.refresh()
        if (self.doc.width, self.doc.height) != size:
            self.canvas.fit()

    def _on_changed(self) -> None:
        if self.doc is None:
            return
        if self.layers.list.count() != len(self.doc.layers) or self._layers_differ():
            self.layers.refresh(self.doc)
        else:
            self.layers.refresh_thumbnails()
        self._update_buttons()
        self._update_info()

    def _layers_differ(self) -> bool:
        for row in range(self.layers.list.count()):
            layer = self.doc.layers[len(self.doc.layers) - 1 - row]
            item = self.layers.list.item(row)
            visible = item.checkState() == Qt.CheckState.Checked
            if item.text() != layer.name or visible != layer.visible:
                return True
        return self.layers.list.currentRow() != len(self.doc.layers) - 1 - self.doc.active or (
            self.layers.opacity.value() != round(self.doc.active_layer.opacity * 100)
        )

    def _update_buttons(self) -> None:
        h = self.history
        self.undo_button.setEnabled(bool(h and h.can_undo))
        self.redo_button.setEnabled(bool(h and h.can_redo))
        self.undo_button.setToolTip(f"Undo {h.undo_label} (Ctrl+Z)" if h and h.can_undo else "Undo")
        self.redo_button.setToolTip(f"Redo {h.redo_label} (Ctrl+Y)" if h and h.can_redo else "Redo")
        enabled = self.doc is not None and not self.saving
        self.save_button.setEnabled(enabled)
        self.save_as_button.setEnabled(enabled)

    def _update_info(self, cursor: tuple[int, int] | None = None) -> None:
        if self.doc is None:
            self.info.setText("")
            return
        parts = []
        if cursor is not None and self.doc.bounds.contains(*cursor):
            parts.append(f"{cursor[0]}, {cursor[1]}")
        parts.append(f"{self.doc.width} × {self.doc.height} px")
        parts.append(f"{round(self.canvas.zoom * 100)} %")
        self.info.setText("   ·   ".join(parts))

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    # ------------------------------------------------------------ operations
    def run_op(self, name: str) -> None:
        doc = self.doc
        if doc is None:
            return
        cmd = None
        try:
            if name == "resize":
                dlg = ResizeDialog(doc, self)
                if dlg.exec():
                    cmd = ops.resize_image(doc, *dlg.result_values())
            elif name == "canvas_size":
                dlg = CanvasSizeDialog(doc, self)
                if dlg.exec():
                    cmd = ops.canvas_size(doc, *dlg.result_values())
            elif name == "crop_selection":
                if doc.selection is None:
                    self.show_message("Select an area first (rectangle select, M).")
                    return
                cmd = ops.crop(doc, doc.selection)
            elif name == "brightness":
                dlg = BrightnessContrastDialog(self)
                if dlg.exec():
                    self.apply_brightness_contrast(*dlg.result_values())
                return
            else:
                layer_only = name.startswith("layer_")
                key = name.removeprefix("layer_")
                rotations = {"rotate_right": 90, "rotate_left": 270, "rotate_180": 180}
                if key in rotations:
                    cmd = ops.rotate(doc, rotations[key], layer_only)
                elif key in ("flip_h", "flip_v"):
                    cmd = ops.flip(doc, key == "flip_h", layer_only)
        except ConversionError as exc:
            self.show_message(str(exc), error=True)
            return
        self._push(cmd)

    def apply_brightness_contrast(self, brightness: int, contrast: int) -> None:
        if self.doc is not None:
            self._push(ops.apply_brightness_contrast(self.doc, brightness, contrast))

    def _apply_crop(self) -> None:
        pending = getattr(self.tool, "pending", None)
        if self.doc is None or pending is None:
            return
        self.tool.pending = None
        self._push(ops.crop(self.doc, pending))
        self.canvas.update()

    # ------------------------------------------------------------ text
    def _open_text_box(self, x: float, y: float) -> None:
        if self.text_box is not None:
            self.commit_text()
        self._text_anchor = (x, y)
        opts = self.tool_settings.options_for(self.tools["text"])
        box = TextBox(self.canvas)
        font = QFont(self.tool_settings.font_family())
        font.setPixelSize(max(6, round(opts.size * self.canvas.zoom)))
        font.setBold(opts.bold)
        font.setItalic(opts.italic)
        box.setFont(font)
        color = QColor(*self.swatches.primary)
        box.setStyleSheet(
            f"QPlainTextEdit#TextBox {{ background: transparent; color: {color.name()};"
            " border: 1px dashed #888; padding: 0; }"
        )
        box.move(self.canvas.image_to_widget(x, y))
        box.committed.connect(self.commit_text)
        box._fit()
        box.show()
        box.setFocus()
        self.text_box = box

    def commit_text(self) -> None:
        box = self.text_box
        if box is None or self._committing:
            return
        self._committing = True
        try:
            self.text_box = None
            text = box.toPlainText()
            box.hide()
            box.deleteLater()
            if self.doc is None:
                return
            opts = self.tool_settings.options_for(self.tools["text"])
            x, y = self._text_anchor
            self._push(
                add_text_layer(
                    self.doc,
                    text,
                    x,
                    y,
                    self.tool_settings.font_family(),
                    opts.size,
                    opts.bold,
                    opts.italic,
                    self.swatches.primary,
                )
            )
            self.canvas.setFocus()
        finally:
            self._committing = False

    # ------------------------------------------------------------ saving
    def _default_path(self) -> Path:
        folder = self.sources[0].parent if self.sources else Path.home()
        stem = self.sources[0].stem if self.sources else "image"
        ext = "ora" if self.doc is not None and len(self.doc.layers) > 1 else "png"
        if self._last_save is not None:
            return self._last_save[0]
        return folder / f"{stem} edited.{ext}"

    def save_as(self) -> bool:
        """Ask for a file name and start saving. False if the user cancelled."""
        if self.doc is None or self.saving:
            return False
        if self.text_box is not None:
            self.commit_text()
        default = self._default_path()
        filters = [f"{label} (*.{fid})" for fid, label in SAVE_FORMATS]
        ids = [fid for fid, _ in SAVE_FORMATS]
        ext = default.suffix.lstrip(".").lower()
        selected = filters[ids.index(ext)] if ext in ids else filters[0]
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save image as", str(default), ";;".join(filters), selected
        )
        if not path:
            return False
        path = Path(path)
        ext = EXT_ALIASES.get(path.suffix.lstrip(".").lower(), path.suffix.lstrip(".").lower())
        if ext not in ids:
            ext = ids[filters.index(chosen)] if chosen in filters else "png"
            path = path.with_name(f"{path.name}.{ext}")
        values = self._save_options(ext)
        if values is None:
            return False
        self._start_save(path, ext, values)
        return True

    def save(self) -> bool:
        if self._last_save is None:
            return self.save_as()
        if self.doc is None or self.saving:
            return False
        if self.text_box is not None:
            self.commit_text()
        self._start_save(*self._last_save)
        return True

    def _save_options(self, target_id: str) -> dict | None:
        """Ask for the format's options (quality etc.); None = cancelled."""
        if target_id == "ora":
            return {}
        dlg = SaveOptionsDialog(target_id, self._save_values.get(target_id, {}), self)
        if not dlg.panel.has_visible_fields():
            return {}
        if not dlg.exec():
            return None
        self._save_values[target_id] = dlg.values()
        return dlg.values()

    def _start_save(self, path: Path, target: str, values: dict) -> None:
        doc = self.doc
        snapshot = Document(doc.width, doc.height, [la.copy() for la in doc.layers], doc.active)
        position = self.history.position
        self.saving = True
        self._last_save_ok = False
        self._update_buttons()
        self.show_message(f"Saving “{path.name}”…")
        thread = QThread(self)
        worker = _SaveWorker(snapshot, path, target, values)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def ok(p: str) -> None:
            self._last_save = (path, target, values)
            self._last_save_ok = True
            if self.history is not None and self.doc is doc:
                self.history.mark_saved(position)
            self.show_message(f"Saved “{Path(p).name}”.")

        worker.succeeded.connect(ok)
        worker.failed.connect(lambda msg: self.show_message(msg, error=True))
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._save_finished)
        self._save_thread, self._save_worker = thread, worker
        thread.start()

    def _save_finished(self) -> None:
        # References are kept until QThread.finished (see CLAUDE.md).
        self._save_thread, self._save_worker = None, None
        self.saving = False
        self._update_buttons()

    def _wait_for_save(self) -> bool:
        if self.saving and self._save_thread is not None:
            loop = QEventLoop()
            self._save_thread.finished.connect(loop.quit)
            if self.saving:
                loop.exec()
        return self._last_save_ok

    # ------------------------------------------------------------ leaving
    def _ask_save_changes(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes to this image?")
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
        """True when it's fine to close the editor (saved, discarded, or nothing changed)."""
        if self.text_box is not None:
            self.commit_text()
        if self.saving:
            self._wait_for_save()
        if self.doc is None or not self.doc.dirty:
            return True
        answer = self._ask_save_changes()
        if answer == "discard":
            return True
        if answer == "save":
            started = self.save()
            return started and self._wait_for_save() and not self.doc.dirty
        return False
