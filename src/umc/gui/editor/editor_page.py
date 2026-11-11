"""The image editor page: top bar, tool bar, canvas, settings / colours / layers, status line."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEventLoop,
    QMimeData,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.errors import ConversionError
from umc.editor import clipboard, effects, inpaint, layerfx, ops, selection
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
    live_text_layer,
    painted_over,
    placeholder_layer,
    text_layer_at,
)
from umc.gui import icons, theme
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
    StickersDialog,
    _AdjustDialog,
    tool_groups,
)
from umc.gui.editor.floating import PanelHost
from umc.gui.editor.fx_window import FxWindow
from umc.gui.editor.layers_panel import LayersPanel
from umc.gui.editor.shortcuts import SHORTCUTS
from umc.gui.editor.side_panel import ColorSwatches, ToolSettings
from umc.gui.editor.tool_rail import ToolRail

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


class _BackgroundWorker(QObject):
    """Downloads the model once (if needed), then finds the subject — off the UI thread."""

    progress = Signal(float, str)
    succeeded = Signal(object)  # the "L" mask
    failed = Signal(str)

    def __init__(self, image, cancel_event: threading.Event) -> None:
        super().__init__()
        self.image, self.cancel_event = image, cancel_event

    @Slot()
    def run(self) -> None:
        from umc.tools import bgremove

        try:
            if not bgremove.model_ready():
                bgremove.ensure_model(lambda f, m: self.progress.emit(f, m), self.cancel_event)
            self.progress.emit(-1, "Finding the subject…")
            self.succeeded.emit(bgremove.subject_mask(self.image))
        except ConversionError as exc:
            log.error("background removal failed: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("background removal failed")
            self.failed.emit("Couldn't remove the background. See the log for details.")


class TextGrip(QToolButton):
    """The handle next to the text being typed: drag it to move the text (the typing box keeps
    the keyboard)."""

    dragged = Signal(float, float)  # screen pixels

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TextGrip")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # clicking it mustn't end the typing
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Drag to move the text")
        self.setIcon(icons.icon("arrows-move", theme.palette()["text"], 16))
        self.setFixedSize(24, 24)
        self._last: QPointF | None = None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._last = e.globalPosition()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._last is not None:
            d = e.globalPosition() - self._last
            self._last = e.globalPosition()
            self.dragged.emit(d.x(), d.y())

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._last = None


class TextBox(QPlainTextEdit):
    """On-canvas text entry. Ctrl+Enter, Esc or clicking elsewhere commits. Its own letters are
    invisible — the text layer underneath shows the text live, in its real style; only the
    caret and the selection are drawn here."""

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

    keep_focus_in = None  # (widget) -> bool: style controls that may take focus while typing

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        # Qt has moved the focus by then; the box as context: no call once it is deleted
        QTimer.singleShot(0, self, self._after_focus_out)

    def _after_focus_out(self) -> None:
        if self.hasFocus() or not self.isVisible():
            return  # typing again, or already committed (a new box may have the focus now)
        w = QApplication.focusWidget()
        if w is None or w.window() is not self.window():
            return  # another window (the colour picker) or the app in the background: keep typing
        if self.keep_focus_in is not None and self.keep_focus_in(w):
            return  # the size, font, bold… controls or the colours: the text follows them live
        self.committed.emit()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self.hasFocus():  # the caret (the text itself is transparent)
            p = QPainter(self.viewport())
            r = self.cursorRect()
            p.fillRect(QRectF(r.x(), r.y(), 2, r.height()), QColor(theme.palette()["accent"]))
            p.end()


class EditorPage(QWidget):
    back_requested = Signal()
    save_done = Signal()
    bg_done = Signal()  # background removal finished (or failed)

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
        self._bg_thread: QThread | None = None
        self._bg_worker: _BackgroundWorker | None = None
        self._bg_cancel: threading.Event | None = None
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
        self.tool_bar = ToolRail()
        self.tool_bar.tool_selected.connect(self._rail_tool)
        self.tool_bar.action_triggered.connect(self.rail_action)
        middle.addWidget(self.tool_bar)

        self.canvas = EditorCanvas()
        self.canvas.context_provider = self.context
        self.canvas.changed.connect(self._on_changed)
        self.canvas.color_picked.connect(lambda c, b: self.swatches.set_color(c, b))
        self.canvas.zoom_changed.connect(lambda _z: self._update_info())
        self.canvas.view_changed.connect(self._place_text_box)  # the typing box follows
        self.canvas.before_press = self._commit_first  # painting etc. never meets the stand-in
        self.canvas.cursor_moved.connect(lambda x, y: self._update_info((x, y)))
        self.canvas.text_requested.connect(self._open_text_box)
        self.canvas.nudge_requested.connect(self._nudge)
        self.canvas.tool_notice.connect(self.show_message)
        self.crop_bar = CropBar(self.canvas)
        self.crop_bar.applied.connect(self._bar_apply)
        self.crop_bar.cancelled.connect(self._bar_cancel)
        self.canvas.bar = self.crop_bar
        middle.addWidget(self.canvas, 1)
        # every rail icon with settings opens a window floating over the canvas
        self.panels = PanelHost(self.canvas)
        self._preview_owner = None  # the adjustment window whose live preview is showing
        self.panels.state_changed.connect(self._panel_state)
        self.tool_settings = ToolSettings()
        self.tool_settings.changed.connect(self.canvas.update)
        self.tool_settings.changed.connect(self._update_live_text)  # size, bold… while typing
        self.tool_settings.setMinimumWidth(250)
        self.tool_settings.title.hide()  # the window's title already names the tool
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
        self.panels.open("tool", "Tool options", self.tool_settings, closable=False, right=True)
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
        lay.addWidget(QLabel("COLOURS", objectName="Section"))
        self.swatches = ColorSwatches()
        self.swatches.colors_changed.connect(self._update_live_text)  # recolour while typing
        lay.addWidget(self.swatches)
        self.layers = LayersPanel()
        self.layers.command.connect(self._push)
        self.layers.edit_text_requested.connect(self.edit_text)
        self.layers.fx_requested.connect(lambda key: self.run_op(f"fx_{key}"))
        self.layers.active_changed.connect(self._active_layer_changed)
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
                    s.activated.connect(
                        lambda f=fn: (
                            self._commit_first(),
                            self.canvas.cancel_gesture(corners=False),
                            f(),
                        )
                    )

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
        self._window("shortcuts", "Keyboard shortcuts", lambda: ShortcutsDialog(TOOLS, self))

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
        self._commit_first()
        self.panels.close_all(keep=("tool",))  # their settings belong to the old picture
        self.doc = doc
        self._last_size = (doc.width, doc.height)
        self.history = History(doc)
        self.sources = sources or []
        self._last_save = None
        self.canvas.set_document(doc, self.history)
        for tool in self.tools.values():
            if hasattr(tool, "offset") and hasattr(tool, "source"):  # clone: old picture's points
                tool.source, tool.offset = None, None
        self.layers.refresh(doc)
        self.show_message("")
        self._update_buttons()
        self._update_info()

    def add_paths(self, paths: list[Path]) -> None:
        """Dropped images become new layers above the selected one."""
        self._commit_first()
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
        if "tool" in self.panels.panels:
            self.panels.panels["tool"].set_title(self.tool.label)
            self.panels.fit("tool")
        if tool_id == "crop":
            self.show_message(CROP_HINT)
        elif self.message.text() == CROP_HINT:
            self.show_message("")

    def _rail_tool(self, tool_id: str) -> None:
        """A tool clicked on the rail: choose it and show its options; clicking the chosen tool
        again minimises its options window (and brings it back)."""
        if self.tool is not None and tool_id == self.tool.id:
            self.panels.toggle("tool")
        else:
            self.select_tool(tool_id)
            self.panels.restore("tool")
        self.canvas.setFocus()

    def _panel_state(self, key: str, state: str) -> None:
        self.tool_bar.set_panel_state(key, state)
        panel = self.panels.panels.get(key)
        dlg = panel.content if panel is not None else None
        if isinstance(dlg, _AdjustDialog):
            if state == "minimised" and self._preview_owner is dlg:
                self.canvas.set_preview_layer(None, None)  # parked: no preview
            elif state == "open":
                dlg.changed()

    def _window(self, key: str, title: str, make, on_accept=None) -> None:
        """Open the window for ``key`` (``make()`` builds its content), or minimise / bring
        back the one already open. ``on_accept`` runs when its OK button is pressed."""
        self._commit_first()
        if self.panels.toggle(key):
            return
        dlg = make()
        if on_accept is not None:
            dlg.accepted.connect(lambda: self._guarded(on_accept, dlg))
        self.panels.open(key, title, dlg)

    def _guarded(self, fn, dlg) -> None:
        if self.doc is None:
            return
        try:
            self._push(fn(dlg))
        except ConversionError as exc:
            self.show_message(str(exc), error=True)

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
        self._window(
            "feather",
            "Feather selection",
            lambda: FeatherDialog(self),
            lambda d: selection.feather(self.doc, d.radius.value()),
        )

    # ------------------------------------------------------------ changes
    def _push(self, cmd) -> None:
        if cmd is None or self.doc is None:
            return
        size = (self.doc.width, self.doc.height)
        self.canvas.push(cmd)
        self.canvas.refresh()
        if (self.doc.width, self.doc.height) != size:
            self.canvas.fit()

    def _active_layer_changed(self, _index: int) -> None:
        self._update_info()
        self._repreview()  # an open adjustment previews (and applies to) the chosen layer
        self._sync_fx_windows()

    def tweak_fx(self, key: str, values: dict) -> None:
        """An effect window changed: the selected layer's effects, live, one undo step a run."""
        doc = self.doc
        if doc is None or not doc.layers or self.history is None:
            return
        i = doc.active
        old = doc.layers[i]
        fx = layerfx.with_values(old.fx, values)
        if ops.tweak_layer_fx(doc, self.history, i, fx, layerfx.LABELS[key]):
            self.canvas.refresh(layerfx.bounds(old).union(layerfx.bounds(doc.layers[i])))
            self.canvas.changed.emit()

    def _sync_fx_windows(self) -> None:
        for key, panel in self.panels.panels.items():
            if key.startswith("fx_") and isinstance(panel.content, FxWindow):
                panel.content.sync()

    def _repreview(self) -> None:
        owner = self._preview_owner
        if owner is not None and not owner._parked():
            owner.changed()

    def _on_changed(self) -> None:
        if self.doc is None:
            return
        size = (self.doc.width, self.doc.height)
        if size != getattr(self, "_last_size", size):
            # their width / height fields describe the old size
            self.panels.close("resize")
            self.panels.close("canvas_size")
        self._last_size = size
        self._repreview()  # the picture changed under an open adjustment: preview again
        self._sync_fx_windows()  # an undo may have changed the effects shown in a window
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
    def rail_action(self, name: str) -> None:
        """An action button in the tool rail (the Select group's ones aren't run_op ops)."""
        self._commit_first()
        if self.doc is None:
            return
        select = {
            "select_all": lambda: self._push(selection.select_all(self.doc)),
            "deselect": lambda: self._push(selection.deselect(self.doc)),
            "invert_selection": lambda: self._push(selection.invert(self.doc)),
            "feather": self.feather,
        }
        if name in select:
            select[name]()
        else:
            self.run_op(name)
        self.canvas.setFocus()

    def run_op(self, name: str) -> None:
        self._commit_first()
        doc = self.doc
        if doc is None:
            return
        cmd = None
        try:
            if name == "resize":
                self._window(
                    name,
                    "Resize image",
                    lambda: ResizeDialog(self.doc, self),
                    lambda d: ops.resize_image(self.doc, *d.result_values()),
                )
                return
            if name == "canvas_size":
                self._window(
                    name,
                    "Canvas size",
                    lambda: CanvasSizeDialog(self.doc, self),
                    lambda d: ops.canvas_size(self.doc, *d.result_values()),
                )
                return
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
            elif name == "remove_background":
                self.remove_background()
                return
            elif name == "content_fill":
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    cmd = inpaint.content_aware_fill(doc)
                finally:
                    QApplication.restoreOverrideCursor()
            elif name == "stickers":
                self._window(
                    name,
                    "Stickers",
                    lambda: StickersDialog(self),
                    lambda d: (
                        ops.add_sticker(
                            self.doc,
                            d.chosen(),
                            d.size.value(),
                            self.view_center(),
                            self.swatches.primary,
                            self.swatches.secondary,
                        )
                        if d.chosen()
                        else None
                    ),
                )
                return
            elif name.startswith("fx_") and name[3:] in layerfx.LABELS:
                key = name[3:]
                self._window(name, layerfx.LABELS[key], lambda: FxWindow(self, key))
                return
            elif name in ADJUST_DIALOGS:
                cls, label = ADJUST_DIALOGS[name]

                def apply(d, label=label):
                    self.apply_effect(d.effect(), label)

                self._window(name, cls.title, lambda: cls(self), apply)
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
        if editing is not None and painted_over(self.doc, editing):
            answer = QMessageBox.question(
                self,
                "Edit the text again?",
                "This text layer was painted on or changed since it was typed. Editing the text "
                "draws it again, and those changes will be lost (Undo brings them back).",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                return
        self._editing = editing
        if editing is not None:
            self._load_text_style(self.doc.layers[editing].text)
        self._text_anchor = (x, y)
        # the text is a layer from the first moment: a live stand-in, swapped for the real
        # (undoable) change when typing ends
        doc = self.doc
        if editing is None:
            self._live = {
                "index": doc.active + 1,
                "old": None,
                "before": (list(doc.layers), doc.active, set(doc.selected)),
            }
            doc.layers = list(doc.layers)
            doc.layers.insert(doc.active + 1, placeholder_layer(doc, x, y))
            doc.active += 1
            doc.selected = set()
        else:
            self._live = {"index": editing, "old": doc.layers[editing], "before": None}
        box = TextBox(self.canvas)
        box.setStyleSheet(
            "QPlainTextEdit#TextBox { background: transparent; color: rgba(0, 0, 0, 0);"
            " border: 1px dashed #888; padding: 0;"
            " selection-background-color: rgba(91, 94, 240, 90); }"
        )
        box.committed.connect(self.commit_text)
        box.keep_focus_in = self._is_text_style_control
        if editing is not None:
            box.setPlainText(doc.layers[editing].text.text)
        box.textChanged.connect(self._update_live_text)
        grip = TextGrip(self.canvas)
        grip.dragged.connect(self._drag_text)
        self.text_box, self._text_grip = box, grip
        self._place_text_box()
        box.show()
        grip.show()
        box.setFocus()
        self._update_live_text()

    def _is_text_style_control(self, w: QWidget) -> bool:
        tool_panel = self.panels.panels.get("tool")
        return any(
            c is not None and (c is w or c.isAncestorOf(w)) for c in (tool_panel, self.swatches)
        )

    def _commit_first(self) -> None:
        """Finish the text being typed before another change (its stand-in layer must not end up
        in that change, and the change must survive the stand-in being swapped out)."""
        if self.text_box is not None and not self._committing:
            self.commit_text()

    def _place_text_box(self) -> None:
        """Keep the typing box (font scaled to the zoom) and its grip on the text's place."""
        box = self.text_box
        if box is None:
            return
        opts = self.tool_settings.options_for(self.tools["text"])
        font = QFont(self.tool_settings.font_family())
        font.setPixelSize(max(6, round(opts.size * self.canvas.zoom)))
        font.setBold(opts.bold)
        font.setItalic(opts.italic)
        box.setFont(font)
        box._fit()
        pos = self.canvas.image_to_widget(*self._text_anchor)
        box.move(pos)
        self._text_grip.move(pos.x() - self._text_grip.width() - 4, pos.y())

    def _drag_text(self, dx: float, dy: float) -> None:
        x, y = self._text_anchor
        z = self.canvas.zoom
        self._text_anchor = (x + dx / z, y + dy / z)
        live = getattr(self, "_live", None)
        if live is None or self.doc is None:
            return
        layer = self.doc.layers[live["index"]]
        spec = layer.text
        if spec is None or spec.watermark:
            self._place_text_box()  # nothing drawn yet (or a watermark, which covers it all)
            return
        nx = math.floor(self._text_anchor[0]) - spec.ox
        ny = math.floor(self._text_anchor[1]) - spec.oy
        self._show_live(replace(layer, x=nx, y=ny))  # moved, not drawn again

    def _update_live_text(self) -> None:
        """Draw the text being typed into its layer, in its real style (no undo step yet)."""
        live, box = getattr(self, "_live", None), self.text_box
        if live is None or box is None or self.doc is None:
            return
        spec = self._spec_from_settings(box.toPlainText(), live["old"])
        layer = live_text_layer(self.doc, spec, *self._text_anchor, old=live["old"])
        self._show_live(layer)

    def _show_live(self, layer) -> None:
        """Put the live text layer in place and repaint only what changed."""
        live = self._live
        before = self.doc.layers[live["index"]]
        self.doc.layers = list(self.doc.layers)
        self.doc.layers[live["index"]] = layer
        self._place_text_box()
        self.canvas.refresh(before.bounds.union(layer.bounds))
        if self.layers.in_sync(self.doc):
            self.layers.refresh_thumbnails()
        else:
            self.layers.refresh(self.doc)

    def _end_live_text(self) -> None:
        """Put the document back as it was before typing (the real change is pushed next)."""
        live, self._live = getattr(self, "_live", None), None
        if live is None or self.doc is None:
            return
        if live["before"] is not None:
            self.doc.layers, self.doc.active, self.doc.selected = live["before"]
        else:
            self.doc.layers = list(self.doc.layers)
            self.doc.layers[live["index"]] = live["old"]

    def _load_text_style(self, spec) -> None:
        """Put a text layer's style into the Text tool's settings and the colours."""
        values = {k: getattr(spec, k) for k in ("size", "bold", "italic", "align", "outline")}
        values.update(shadow=spec.shadow, box=spec.box, watermark=spec.watermark)
        self.tool_settings.set_values(self.tools["text"], values)
        self.tool_settings.font_box.setCurrentFont(QFont(spec.font))
        self.swatches.set_color(spec.color, "primary")
        self.swatches.set_color(spec.box_color if spec.box else spec.outline_color, "secondary")

    def _spec_from_settings(self, text: str, old) -> TextSpec:
        """The text in the Text tool's current style (``old``: the layer being edited)."""
        opts = self.tool_settings.options_for(self.tools["text"])
        style = {
            "align": opts.align,
            "outline": opts.outline,
            "outline_color": tuple(self.swatches.secondary),
            "shadow": opts.shadow,
            "box": opts.box,
            "box_color": tuple(self.swatches.secondary),
            "watermark": opts.watermark,
        }
        spec_old = old.text if old is not None else None
        if spec_old is not None:
            # the back swatch showed only one of the two colours: keep the other one
            if spec_old.box:
                style["outline_color"] = spec_old.outline_color
            else:
                style["box_color"] = spec_old.box_color
        return TextSpec(
            text,
            self.tool_settings.font_family(),
            opts.size,
            opts.bold,
            opts.italic,
            tuple(self.swatches.primary),
            **style,
        )

    def _text_command(self, text: str, x: float, y: float, opts):
        """A new text layer, or the edited one (style from the Text tool's settings)."""
        editing, self._editing = getattr(self, "_editing", None), None
        if editing is not None and editing < len(self.doc.layers):
            old_layer = self.doc.layers[editing]
            old = old_layer.text
            spec = self._spec_from_settings(text, old_layer)
            moved = (x, y) != anchor_of(old_layer)
            if old is not None and not moved and replace(spec, ox=old.ox, oy=old.oy) == old:
                return None  # opened and closed without a change: no undo step
            return edit_text_layer(self.doc, editing, spec, (x, y))
        spec = self._spec_from_settings(text, None)
        fields = {k: v for k, v in asdict(spec).items() if k not in ("ox", "oy")}
        text, family, size = fields.pop("text"), fields.pop("font"), fields.pop("size")
        bold, italic, color = fields.pop("bold"), fields.pop("italic"), fields.pop("color")
        return add_text_layer(self.doc, text, x, y, family, size, bold, italic, color, **fields)

    def commit_text(self) -> None:
        box = self.text_box
        if box is None or self._committing:
            return
        self._committing = True
        try:
            self.text_box = None
            text = box.toPlainText()
            box.committed.disconnect()  # a late focus-out must never commit the next box
            box.hide()
            box.deleteLater()
            grip, self._text_grip = getattr(self, "_text_grip", None), None
            if grip is not None:
                grip.hide()
                grip.deleteLater()
            self._end_live_text()
            if self.doc is None:
                return
            opts = self.tool_settings.options_for(self.tools["text"])
            x, y = self._text_anchor
            cmd = self._text_command(text, x, y, opts)
            if cmd is not None:
                self._push(cmd)
            else:  # nothing typed / nothing changed: show the document as it is again
                self.canvas.refresh()
                self.layers.refresh(self.doc)
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

    # ------------------------------------------------------------ background removal
    def remove_background(self) -> None:
        """Hide the background of the selected layer with a layer mask (the pixels are kept)."""
        self._commit_first()
        from umc.tools import bgremove

        doc = self.doc
        if doc is None or self._bg_thread is not None:
            return
        if not bgremove.model_ready():
            answer = QMessageBox.question(
                self,
                "Download the background-removal model?",
                f"Removing backgrounds needs a model file (about {bgremove.MODEL_MB} MB). "
                "It is downloaded once and then works offline.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        layer = doc.active_layer
        cancel = threading.Event()
        dlg = QProgressDialog("Removing the background…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Remove background")
        dlg.setMinimumDuration(300)
        dlg.setAutoReset(False)  # the download reaching 100 % mustn't close it before the analysis
        dlg.setAutoClose(False)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.canceled.connect(cancel.set)
        thread = QThread(self)
        worker = _BackgroundWorker(layer.image.copy(), cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def progress(f: float, text: str) -> None:
            dlg.setLabelText(text)
            if f < 0:
                dlg.setRange(0, 0)  # busy
            else:
                dlg.setValue(round(f * 100))

        def ok(mask) -> None:
            dlg.reset()
            if cancel.is_set() or self.doc is not doc or doc.active_layer is not layer:
                return
            self._push(ops.mask_from(doc, mask))
            self.show_message(
                "Background hidden with a layer mask. Paint the mask (K) to fix the edges."
            )

        def failed(msg: str) -> None:
            dlg.reset()
            if not cancel.is_set():
                self.show_message(msg, error=True)

        worker.progress.connect(progress)
        worker.succeeded.connect(ok)
        worker.failed.connect(failed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._bg_finished)
        self._bg_thread, self._bg_worker, self._bg_cancel = thread, worker, cancel
        thread.start()

    def stop_background_removal(self) -> None:
        """Cancel a running background removal and wait for its thread (before closing)."""
        thread = self._bg_thread
        if thread is None:
            return
        if self._bg_cancel is not None:
            self._bg_cancel.set()
        thread.quit()
        thread.wait(30_000)  # a download notices the cancel within a moment; inference ends

    def _bg_finished(self) -> None:
        # References are kept until QThread.finished (see CLAUDE.md).
        self._bg_thread, self._bg_worker = None, None
        self.bg_done.emit()

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
