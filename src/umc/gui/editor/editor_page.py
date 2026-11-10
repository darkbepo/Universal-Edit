"""The image editor page: top bar, tool bar, canvas, settings / colours / layers, status line."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEventLoop, QMimeData, QObject, QPointF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
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
from umc.editor import clipboard, effects, ops, selection
from umc.editor.document import Document, check_size
from umc.editor.history import History
from umc.editor.io import load_layers, open_files
from umc.editor.save import SAVE_FORMATS, save_document
from umc.editor.tools import TOOLS
from umc.editor.tools.base import ToolContext
from umc.editor.tools.select import clear_selection, deselect, select_all
from umc.editor.tools.text import (
    TextSpec,
    add_text_layer,
    anchor_of,
    edit_text_layer,
    text_layer_at,
)
from umc.gui import theme
from umc.gui.editor.canvas import EditorCanvas, pil_to_qimage, qimage_to_pil
from umc.gui.editor.crop_bar import CropBar
from umc.gui.editor.dialogs import (
    BackgroundBlurDialog,
    BlurDialog,
    CanvasSizeDialog,
    CurvesDialog,
    FeatherDialog,
    LevelsDialog,
    LooksDialog,
    QuickFixDialog,
    ResizeDialog,
    SaveOptionsDialog,
    SharpenDialog,
    ShortcutsDialog,
    tool_groups,
)
from umc.gui.editor.layers_panel import LayersPanel
from umc.gui.editor.shortcuts import SHORTCUTS
from umc.gui.editor.side_panel import ColorSwatches, ToolBar, ToolSettings

log = logging.getLogger(__name__)

CROP_HINT = "Drag a rectangle on the picture, then apply it with the button next to it (or Enter)."
# Adjust menu: run_op name → (dialog, undo label).
ADJUST_DIALOGS = {
    "quick_fix": (QuickFixDialog, "Quick fix"),
    "levels": (LevelsDialog, "Levels"),
    "curves": (CurvesDialog, "Curves"),
    "blur": (BlurDialog, "Blur"),
    "sharpen": (SharpenDialog, "Sharpen"),
    "looks": (LooksDialog, "Look"),
    "background_blur": (BackgroundBlurDialog, "Background blur"),
}
# Marks a clipboard image as ours and remembers where on the canvas it was copied from.
CLIP_MIME = "application/x-umc-editor-clip"
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
    save_done = Signal()

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
        self.canvas.nudge_requested.connect(self._nudge)
        self.crop_bar = CropBar(self.canvas)
        self.crop_bar.applied.connect(self._bar_apply)
        self.crop_bar.cancelled.connect(self._bar_cancel)
        self.canvas.bar = self.crop_bar
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
        select_button = QToolButton(text="Select ▾", objectName="SmallMenu")
        select_button.setToolTip("Select all, deselect, invert, feather")
        select_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        select_menu = QMenu(select_button)
        for label, fn in (
            (
                "Select all",
                lambda: self._push(selection.select_all(self.doc)) if self.doc else None,
            ),
            ("Deselect", lambda: self._push(selection.deselect(self.doc)) if self.doc else None),
            ("Invert", lambda: self._push(selection.invert(self.doc)) if self.doc else None),
            ("Feather…", self.feather),
        ):
            select_menu.addAction(label, fn)
        select_button.setMenu(select_menu)
        lay.addWidget(select_button)
        adjust_button = QToolButton(text="Adjust ▾", objectName="SmallMenu")
        adjust_button.setToolTip("Fix the tones and colours of the selected layer")
        adjust_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        adjust_menu = QMenu(adjust_button)
        for label, op in (
            ("Quick fix… (exposure, contrast, colours)", "quick_fix"),
            ("Levels…", "levels"),
            ("Curves…", "curves"),
        ):
            adjust_menu.addAction(label, lambda o=op: self.run_op(o))
        adjust_button.setMenu(adjust_menu)
        lay.addWidget(adjust_button)
        filter_button = QToolButton(text="Filters ▾", objectName="SmallMenu")
        filter_button.setToolTip("Blur, sharpen and one-click looks for the selected layer")
        filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filter_menu = QMenu(filter_button)
        for label, op in (
            ("Looks…", "looks"),
            ("Blur…", "blur"),
            ("Background blur… (portraits)", "background_blur"),
            ("Sharpen…", "sharpen"),
        ):
            filter_menu.addAction(label, lambda o=op: self.run_op(o))
        filter_button.setMenu(filter_menu)
        lay.addWidget(filter_button)
        lay.addStretch(1)
        tb("?", "Keyboard shortcuts (?)", lambda: self.show_shortcuts())
        lay.addSpacing(6)
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
        self.layers.edit_text_requested.connect(self.edit_text)
        self.layers.active_changed.connect(lambda _i: self._update_info())
        lay.addWidget(self.layers, 1)
        scroll.setWidget(host)
        return scroll

    def _install_shortcuts(self) -> None:
        """Bind every key from ``shortcuts.SHORTCUTS`` and the tool letters."""

        def sc(keys, fn) -> None:
            for k in keys:
                # Windows reports AltGr as Ctrl+Alt, so a page-wide Ctrl+Alt+A would swallow
                # AltGr+A ("ą") in text fields: Ctrl+Alt keys only work on the canvas / layers.
                if "Alt" in k:
                    owners, context = (
                        (self.canvas, self.layers.list),
                        Qt.ShortcutContext.WidgetShortcut,
                    )
                else:
                    owners, context = (self,), Qt.ShortcutContext.WidgetWithChildrenShortcut
                for owner in owners:
                    s = QShortcut(QKeySequence(k), owner)
                    s.setContext(context)
                    s.activated.connect(lambda f=fn: (self.canvas.cancel_gesture(), f()))

        def with_doc(fn):
            return lambda: fn(self.doc) if self.doc is not None else None

        lp = self.layers
        actions = {
            "undo": self.canvas.undo,
            "redo": self.canvas.redo,
            "copy": lambda: self.copy(),
            "copy_merged": lambda: self.copy(merged=True),
            "cut": self.cut,
            "paste": self.paste,
            "delete": self._delete,
            "save": self.save,
            "save_as": self.save_as,
            "select_all": with_doc(lambda d: self._push(select_all(d))),
            "deselect": with_doc(lambda d: self._push(deselect(d))),
            "apply": self._apply,
            "invert_selection": with_doc(lambda d: self._push(selection.invert(d))),
            "feather": self.feather,
            "new_layer": lp.add,
            "duplicate": lp.duplicate,
            "merge": lp.merge,
            "layer_up": lambda: lp.move(1),
            "layer_down": lambda: lp.move(-1),
            "layer_top": lambda: lp.move_to_end(True),
            "layer_bottom": lambda: lp.move_to_end(False),
            "select_all_layers": lp.select_all,
            "zoom_in": self.canvas.zoom_in,
            "zoom_out": self.canvas.zoom_out,
            "fit": self.canvas.fit,
            "actual": lambda: self.canvas.set_zoom(1.0),
            "help": self.show_shortcuts,
            "swap_colours": self.swatches.swap,
            "smaller": lambda: self.tool_settings.change_size(self.tool, 1 / 1.2),
            "bigger": lambda: self.tool_settings.change_size(self.tool, 1.2),
        }
        missing = {s.id for s in SHORTCUTS} ^ set(actions)
        assert not missing, f"shortcut table and actions differ: {missing}"
        for shortcut in SHORTCUTS:
            sc(shortcut.keys, actions[shortcut.id])
        for letter, group in tool_groups(TOOLS).items():  # a shared letter cycles its tools
            sc((letter,), lambda ids=[t.id for t in group]: self._cycle_tool(ids))

    def show_shortcuts(self) -> None:
        ShortcutsDialog(TOOLS, self).exec()

    def _delete(self) -> None:
        """Delete: the selected layers when the layers list has focus, else the selected area."""
        if self.doc is None:
            return
        if self.layers.list.hasFocus():
            self.layers.delete()
        else:
            self._push(clear_selection(self.doc))

    # ------------------------------------------------------------ clipboard
    def _put_on_clipboard(self, clip: clipboard.Clip) -> None:
        mime = QMimeData()
        mime.setImageData(pil_to_qimage(clip.image))
        w, h = clip.image.size
        mime.setData(CLIP_MIME, f"{clip.x},{clip.y},{w},{h}".encode())
        QApplication.clipboard().setMimeData(mime)

    def copy(self, merged: bool = False) -> bool:
        if self.doc is None:
            return False
        clip = clipboard.copy(self.doc, merged)
        if clip is None:
            self.show_message("Nothing to copy: the selection doesn't touch this layer.")
            return False
        self._put_on_clipboard(clip)
        w, h = clip.image.size
        self.show_message(f"Copied {w} × {h} px.")
        return True

    def cut(self) -> None:
        if self.doc is None:
            return
        clip, cmd = clipboard.cut(self.doc)
        if clip is None:
            self.show_message("Nothing to cut: the selection doesn't touch this layer.")
            return
        self._put_on_clipboard(clip)
        self._push(cmd)

    def view_center(self) -> tuple[float, float]:
        """The canvas point in the middle of what the user is looking at."""
        return self.canvas.widget_to_image(
            QPointF(self.canvas.width() / 2, self.canvas.height() / 2)
        )

    def paste(self) -> None:
        """Clipboard image → new layer (back in place if copied here); image files → layers."""
        if self.doc is None:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.hasImage():
            data = mime.imageData()
            qimg = data.toImage() if hasattr(data, "toImage") else data
            image = qimage_to_pil(qimg)
            x = y = None
            if mime.hasFormat(CLIP_MIME):
                try:
                    cx, cy, w, h = (int(v) for v in bytes(mime.data(CLIP_MIME)).decode().split(","))
                    if (w, h) == image.size:
                        x, y = cx, cy
                except ValueError:
                    pass
            try:
                self._push(
                    clipboard.paste(self.doc, clipboard.Clip(image, x, y), self.view_center())
                )
            except ConversionError as exc:
                self.show_message(str(exc), error=True)
                return
            self.show_message("Pasted as a new layer. Move it with the Move tool (V).")
            return
        if mime is not None and mime.hasUrls():
            paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
            if paths:
                self.add_paths(paths)
                return
        self.show_message("There's no picture on the clipboard to paste.")

    def _nudge(self, dx: int, dy: int) -> None:
        if self.doc is not None:
            self._push(ops.nudge_layers(self.doc, self.doc.selected_indices, dx, dy))

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
        if tool_id == "crop":
            self.show_message(CROP_HINT)
        elif self.message.text() == CROP_HINT:
            self.show_message("")

    def _cycle_tool(self, ids: list[str]) -> None:
        """The first tool of the group, or the next one if a tool of it is already chosen."""
        if self.tool.id in ids:
            self.select_tool(ids[(ids.index(self.tool.id) + 1) % len(ids)])
        else:
            self.select_tool(ids[0])

    def _apply(self) -> None:
        """Enter: finish a polygon lasso, or apply the crop."""
        if not self.canvas.finish_tool():
            self._apply_crop()

    def feather(self) -> None:
        if self.doc is None or self.doc.selection is None:
            self.show_message("Select an area first.")
            return
        dlg = FeatherDialog(self)
        if dlg.exec():
            self._push(selection.feather(self.doc, dlg.radius.value()))

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
        if not self.layers.in_sync(self.doc):
            self.layers.refresh(self.doc)
        else:
            self.layers.refresh_thumbnails()
        self._update_buttons()
        self._update_info()

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
            elif name == "background_blur" and doc.selection is None:
                self.show_message(
                    "Select the subject first (lasso, magic wand…): what is around it gets blurred."
                )
                return
            elif name in ADJUST_DIALOGS:
                dlg = ADJUST_DIALOGS[name][0](self)
                if dlg.exec():
                    self.apply_effect(dlg.effect(), ADJUST_DIALOGS[name][1])
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

    def apply_effect(self, effect: effects.Effect, label: str = "Adjust") -> None:
        if self.doc is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)  # a few seconds on 24 MP photos
        try:
            cmd = effects.apply_to_document(self.doc, effect, label)
        finally:
            QApplication.restoreOverrideCursor()
        self._push(cmd)

    def _bar_apply(self, mode: str) -> None:
        if mode == "crop":
            self._apply_crop()
        else:
            self.run_op("crop_selection")
        self.canvas.setFocus()

    def _bar_cancel(self, mode: str) -> None:
        if self.doc is None:
            return
        if mode == "crop":
            self.tool.cancel(self.doc)
            self.canvas.update()
        else:
            self._push(deselect(self.doc))
        self.canvas.setFocus()

    def _apply_crop(self) -> None:
        pending = getattr(self.tool, "pending", None)
        if self.doc is None or pending is None:
            return
        self.tool.pending = None
        self._push(ops.crop(self.doc, pending))
        self.canvas.update()

    # ------------------------------------------------------------ text
    def edit_text(self, index: int) -> None:
        """Open text layer ``index`` again (layer menu → Edit text…)."""
        if self.doc is not None and self.doc.layers[index].text is not None:
            self.select_tool("text")
            self._open_text_box(*anchor_of(self.doc.layers[index]), editing=index)

    def _open_text_box(self, x: float, y: float, editing: int | None = None) -> None:
        if self.text_box is not None:
            self.commit_text()
        if editing is None and self.doc is not None:  # a click on a text layer edits it
            editing = text_layer_at(self.doc, x, y)
            if editing is not None:
                x, y = anchor_of(self.doc.layers[editing])
        self._editing = editing
        if editing is not None:
            self._load_text_style(self.doc.layers[editing].text)
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
        if editing is not None:
            box.setPlainText(self.doc.layers[editing].text.text)
        box._fit()
        box.show()
        box.setFocus()
        self.text_box = box

    def _load_text_style(self, spec) -> None:
        """Put a text layer's style into the Text tool's settings and the colours."""
        values = {k: getattr(spec, k) for k in ("size", "bold", "italic", "align", "outline")}
        values.update(shadow=spec.shadow, box=spec.box, watermark=spec.watermark)
        self.tool_settings.set_values(self.tools["text"], values)
        self.tool_settings.font_box.setCurrentFont(QFont(spec.font))
        self.swatches.set_color(spec.color, "primary")
        self.swatches.set_color(spec.box_color if spec.box else spec.outline_color, "secondary")

    def _text_command(self, text: str, x: float, y: float, opts):
        """A new text layer, or the edited one (style from the Text tool's settings)."""
        style = {
            "align": opts.align,
            "outline": opts.outline,
            "outline_color": tuple(self.swatches.secondary),
            "shadow": opts.shadow,
            "box": opts.box,
            "box_color": tuple(self.swatches.secondary),
            "watermark": opts.watermark,
        }
        family = self.tool_settings.font_family()
        editing, self._editing = getattr(self, "_editing", None), None
        if editing is not None and editing < len(self.doc.layers):
            spec = TextSpec(
                text,
                family,
                opts.size,
                opts.bold,
                opts.italic,
                tuple(self.swatches.primary),
                **style,
            )
            return edit_text_layer(self.doc, editing, spec)
        return add_text_layer(
            self.doc,
            text,
            x,
            y,
            family,
            opts.size,
            opts.bold,
            opts.italic,
            self.swatches.primary,
            **style,
        )

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
            self._push(self._text_command(text, x, y, opts))
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
        self.save_done.emit()

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
