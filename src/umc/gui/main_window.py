"""The single application window: files (drop zone + queue) → output format → settings → Convert.

Wide layout: files on the left, "Convert to" / "Settings" sideways tabs on the right, a bottom
bar with the status, the destination, the overall progress and Convert. Below ``NARROW`` px of
width everything stacks in one column, in the same order.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import options as opts
from umc.core.detect import DetectedFile, MediaFamily, detect
from umc.core.job import ConversionJob, file_fractions
from umc.core.registry import MixedInputError, Target, targets_for
from umc.doceditor.types import EDITABLE_DOCUMENT_EXTS
from umc.gui import icons, theme
from umc.gui.tool_dialog import ToolMissingDialog
from umc.gui.widgets.drop_zone import DropZone
from umc.gui.widgets.file_queue import FileQueue, fmt_size
from umc.gui.widgets.format_picker import FormatPicker
from umc.gui.widgets.settings_panel import SettingsPanel
from umc.gui.widgets.side_tabs import SideTabs
from umc.gui.worker import start_worker

FAMILY_NOUN = {
    MediaFamily.IMAGE: ("image", "images"),
    MediaFamily.VIDEO: ("video", "videos"),
    MediaFamily.AUDIO: ("audio file", "audio files"),
    MediaFamily.MODEL: ("3D model", "3D models"),
    MediaFamily.DOCUMENT: ("document", "documents"),
    MediaFamily.ARCHIVE: ("archive", "archives"),
    MediaFamily.SUBTITLE: ("subtitle file", "subtitle files"),
    MediaFamily.FONT: ("font", "fonts"),
    MediaFamily.VECTOR: ("vector graphic", "vector graphics"),
    MediaFamily.CAD: ("CAD drawing", "CAD drawings"),
    MediaFamily.PROJECT: ("video project", "video projects"),
}

NARROW = 900  # px of window width below which the layout becomes one column
DEFAULT_SIZE = QSize(1280, 800)
MAX_RESULTS = 200


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Universal Media Converter")
        self.setMinimumSize(600, 560)
        self.settings = QSettings()
        self.files: list[DetectedFile] = []
        self.target: Target | None = None
        self.option_values: dict[str, dict] = {}  # per options class, remembered across targets
        self.last_outputs: list[str] = []
        self._thread = None
        self._worker = None
        self._last_job: ConversionJob | None = None
        self._pending_tool: tuple[str, str, str] | None = None
        self._narrow: bool | None = None

        root = QWidget(objectName="Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------------------------------------------------- top bar
        top = QFrame(objectName="TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(22, 12, 22, 12)
        tl.setSpacing(10)
        self.logo = QLabel(objectName="Logo")
        self.logo.setFixedSize(30, 30)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Universal Media Converter", objectName="Title")
        tl.addWidget(self.logo)
        tl.addWidget(title)
        tl.addStretch(1)

        # Convert | Edit — Edit is enabled when the files can be edited: images,
        # exactly one editable document, or 3D models (the 3D viewer)
        self.mode_bar = QFrame(objectName="ModeBar")
        mb = QHBoxLayout(self.mode_bar)
        mb.setContentsMargins(3, 3, 3, 3)
        mb.setSpacing(2)
        self.convert_mode = QPushButton("Convert", objectName="Segment")
        self.edit_mode = QPushButton("Edit", objectName="Segment")
        self._mode_group = QButtonGroup(self)
        for b in (self.convert_mode, self.edit_mode):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._mode_group.addButton(b)
            mb.addWidget(b)
        self.convert_mode.setChecked(True)
        self.convert_mode.clicked.connect(self.leave_edit)
        self.edit_mode.clicked.connect(self.enter_edit)
        tl.addWidget(self.mode_bar)
        outer.addWidget(top)
        self.editor = None  # EditorPage, created on first use
        self.doc_editor = None  # DocEditorPage, created on first use
        self.viewer3d = None  # ViewerPage (3D models), created on first use
        self._convert_size = None

        # ---------------------------------------------------------- body: two columns
        body = QWidget(objectName="Body")
        self._columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        self._columns.setContentsMargins(20, 18, 20, 18)
        self._columns.setSpacing(18)

        self.left = QWidget(objectName="LeftColumn")
        left = QVBoxLayout(self.left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_files)
        left.addWidget(self.drop)

        self.input_error = QLabel(objectName="Error")
        self.input_error.setWordWrap(True)
        left.addWidget(self.input_error)

        # results (after a successful run)
        self.results_card = QFrame(objectName="Card")
        rc = QVBoxLayout(self.results_card)
        rc.setContentsMargins(14, 10, 10, 8)
        rc.setSpacing(4)
        head = QHBoxLayout()
        self.results_label = QLabel(objectName="CardTitle")
        self.open_button = QPushButton("Open folder", objectName="Link")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.open_output)
        head.addWidget(self.results_label)
        head.addStretch(1)
        head.addWidget(self.open_button)
        rc.addLayout(head)
        self.results_list = QListWidget(objectName="ResultList")
        self.results_list.setIconSize(QSize(18, 18))
        self.results_list.setToolTip("Click to show the file in its folder")
        self.results_list.itemClicked.connect(self._reveal_result)
        rc.addWidget(self.results_list)
        left.addWidget(self.results_card)

        # queue
        self.files_card = QFrame(objectName="Card")
        fc = QVBoxLayout(self.files_card)
        fc.setContentsMargins(14, 10, 10, 8)
        fc.setSpacing(4)
        head = QHBoxLayout()
        self.files_label = QLabel(objectName="CardTitle")
        clear = QPushButton("Clear", objectName="Link")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear_files)
        self.clear_button = clear
        head.addWidget(self.files_label)
        head.addStretch(1)
        head.addWidget(clear)
        fc.addLayout(head)
        self.file_list = FileQueue()
        self.file_list.model().rowsMoved.connect(self._sync_order)
        self.file_list.remove_requested.connect(self.remove_row)
        remove = QAction(self.file_list)
        remove.setShortcuts([QKeySequence.StandardKey.Delete, QKeySequence(Qt.Key.Key_Backspace)])
        remove.triggered.connect(self.remove_selected)
        self.file_list.addAction(remove)
        fc.addWidget(self.file_list, 1)
        self.files_hint = QLabel("Drag to reorder · Delete removes", objectName="Help")
        fc.addWidget(self.files_hint)
        left.addWidget(self.files_card, 1)
        self._columns.addWidget(self.left, 0)

        # right: Convert to | Settings
        self.picker = FormatPicker()
        self.picker.target_changed.connect(self.on_target_changed)
        self.settings_panel = SettingsPanel()
        self.settings_panel.changed.connect(self._remember_values)
        self.tabs = SideTabs(self.picker, self.settings_panel)
        self.tabs.reset_clicked.connect(self.reset_settings)
        self.empty_hint = QLabel("Add files to see what they can become.", objectName="EmptyHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.right = QStackedWidget(objectName="RightColumn")
        self.right.addWidget(self.empty_hint)
        self.right.addWidget(self.tabs)
        self._columns.addWidget(self.right, 1)
        outer.addWidget(body, 1)

        # ---------------------------------------------------------- bottom bar
        bar = QFrame(objectName="BottomBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(22, 12, 22, 12)
        bl.setSpacing(14)
        self.status = QLabel(objectName="Muted")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.dest_button = QToolButton(objectName="Link")
        self.dest_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.dest_button)
        menu.addAction("Same folder as the original", lambda: self._set_output_dir(None))
        menu.addAction("Choose folder…", self._choose_output_dir)
        self.dest_button.setMenu(menu)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(200)
        self.convert_button = QPushButton("Convert", objectName="Primary")
        self.convert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.convert_button.clicked.connect(self.on_convert_clicked)
        bl.addWidget(self.status, 1)
        bl.addWidget(self.dest_button)
        bl.addWidget(self.progress)
        bl.addWidget(self.convert_button)
        outer.addWidget(bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(root)
        self.setCentralWidget(self.stack)
        self.setAcceptDrops(True)
        saved = self.settings.value("output_dir", "", str)
        self._set_output_dir(Path(saved) if saved and Path(saved).is_dir() else None)
        self._restore_size()
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _s: self._retint())
        self._retint()
        self._apply_layout()
        self._refresh()

    # ------------------------------------------------------------------ window size / layout
    def _restore_size(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry is not None and self.restoreGeometry(geometry):
            return
        screen = QGuiApplication.primaryScreen()
        size = QSize(DEFAULT_SIZE)
        if screen is not None:
            avail = screen.availableGeometry().size()
            size = size.boundedTo(QSize(int(avail.width() * 0.92), int(avail.height() * 0.9)))
        self.resize(size)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_layout()
        self._apply_left_width()

    @property
    def narrow(self) -> bool:
        return self.width() < NARROW

    def _apply_layout(self) -> None:
        narrow = self.narrow
        if narrow == self._narrow:
            return
        self._narrow = narrow
        self._columns.setDirection(
            QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        )
        self._apply_left_width()
        self._update_left_sizes()

    def _apply_left_width(self) -> None:
        if self.narrow or not self.files:
            self.left.setMinimumWidth(0)
            self.left.setMaximumWidth(16777215)
        else:
            self.left.setMinimumWidth(320)
            self.left.setMaximumWidth(max(340, min(460, self.width() // 3)))

    def _update_left_sizes(self) -> None:
        """Queue/results heights: free in the wide layout, capped when stacked."""
        rows = self.file_list.count()
        row_h = 56
        if self.narrow:
            self.file_list.setMaximumHeight(min(max(rows, 1), 4) * row_h + 6)
        else:
            self.file_list.setMaximumHeight(16777215)
        self.results_list.setMaximumHeight(min(max(self.results_list.count(), 1), 4) * 34 + 6)

    def _retint(self) -> None:
        pal = theme.palette()
        self.logo.setPixmap(icons.pixmap("transform", 18, pal["accent_text"]))
        self.drop.retint()
        self.picker.retint()
        self.tabs.retint()
        for i in range(self.results_list.count()):
            self.results_list.item(i).setIcon(icons.icon("check", pal["success"], 18))
        self.file_list.viewport().update()

    # ------------------------------------------------------------------ files
    def add_files(self, paths: list[str]) -> None:
        unsupported = []
        known = {f.path for f in self.files}
        for p in self._expand(paths):
            d = detect(p)
            if d is None:
                unsupported.append(Path(p).name)
            elif d.path not in known:
                self.files.append(d)
                known.add(d.path)
        self._rebuild_list()
        msg = ""
        if unsupported:
            shown = ", ".join(unsupported[:3]) + ("…" if len(unsupported) > 3 else "")
            msg = f"Skipped unsupported file(s): {shown}"
        self._refresh(extra_error=msg)

    @staticmethod
    def _expand(paths: list[str]) -> list[str]:
        """Dropped folders expand to the files inside them (not recursive)."""
        out = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                out += sorted(str(c) for c in pp.iterdir() if c.is_file())
            else:
                out.append(p)
        return out

    def clear_files(self) -> None:
        if self.busy:
            return
        self.files.clear()
        self._rebuild_list()
        self._refresh()

    def remove_selected(self) -> None:
        if self.busy:
            return
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()), reverse=True)
        for r in rows:
            del self.files[r]
        self._rebuild_list()
        self._refresh()

    def remove_row(self, row: int) -> None:
        if self.busy or not 0 <= row < len(self.files):
            return
        del self.files[row]
        self._rebuild_list()
        self._refresh()

    def _rebuild_list(self) -> None:
        self.file_list.set_files(self.files)
        self._update_left_sizes()

    def _sync_order(self, *_args) -> None:
        by_path = {str(f.path): f for f in self.files}
        self.files = [by_path[p] for p in self.file_list.paths()]

    # ------------------------------------------------------------------ targets / settings
    def _refresh(self, extra_error: str = "") -> None:
        if not self.busy:
            self._clear_results()
            self.file_list.reset_states()
            self.progress.setValue(0)
            self._set_status("")
        has_files = bool(self.files)
        self.drop.set_compact(has_files)
        self.files_card.setVisible(has_files)
        self._update_left_sizes()
        error = extra_error
        targets: list[Target] = []
        if has_files:
            fams = {f.family for f in self.files}
            if len(fams) == 1:
                fam = fams.pop()
                noun = FAMILY_NOUN[fam][0 if len(self.files) == 1 else 1]
                self.files_label.setText(f"Queue · {len(self.files)} {noun}")
            else:
                self.files_label.setText(f"Queue · {len(self.files)} files")
            self.files_hint.setVisible(len(self.files) > 1)
            try:
                targets = targets_for(self.files)
            except MixedInputError as exc:
                error = str(exc)
        self.input_error.setText(error)
        self.input_error.setVisible(bool(error))
        self.edit_mode.setEnabled(self.edit_available())
        self._update_edit_tooltip()
        # No files yet: the drop zone gets the whole window.
        self.right.setVisible(has_files)
        self.right.setCurrentWidget(self.tabs if targets else self.empty_hint)
        self._apply_left_width()
        keep = self.target.id if self.target else self.settings.value("last_target", "", str)
        self.picker.set_targets(targets, keep_id=keep)
        self._update_buttons()

    # ------------------------------------------------------------------ Convert | Edit
    def edit_kind(self) -> str | None:
        """ "image" when every file is an image, "document" for one editable document,
        "model" when every file is a 3D model."""
        if not self.files:
            return None
        if all(f.family == MediaFamily.IMAGE for f in self.files):
            return "image"
        if all(f.family == MediaFamily.MODEL for f in self.files):
            return "model"
        if (
            len(self.files) == 1
            and self.files[0].family == MediaFamily.DOCUMENT
            and self.files[0].ext in EDITABLE_DOCUMENT_EXTS
        ):
            return "document"
        return None

    def edit_available(self) -> bool:
        return self.edit_kind() is not None

    def _current_editor(self) -> QWidget | None:
        """The editor page being shown, or None on the converter screen."""
        w = self.stack.currentWidget()
        return w if self.stack.currentIndex() != 0 else None

    def _update_edit_tooltip(self) -> None:
        kind = self.edit_kind()
        if kind == "document":
            tip = "Open the document in the document editor"
        elif kind == "model":
            tip = "Open the 3D models in the 3D viewer"
        elif kind == "image":
            tip = "Open the images in the image editor (each image becomes a layer)"
        else:
            tip = "Add images, one text document, or 3D models to edit them"
        self.edit_mode.setToolTip(tip)

    def enter_edit(self) -> None:
        if self._current_editor() is not None:
            return
        kind = self.edit_kind()
        if kind is None or self.busy:
            self.convert_mode.setChecked(True)
            return
        if kind == "document":
            if self.doc_editor is None:
                from umc.gui.doceditor.doc_editor_page import DocEditorPage

                self.doc_editor = DocEditorPage()
                self.doc_editor.back_requested.connect(self.leave_edit)
                self.stack.addWidget(self.doc_editor)
            self._convert_size = self.size()
            self.doc_editor.open_path(self.files[0].path)
            self.stack.setCurrentWidget(self.doc_editor)
            self.edit_mode.setChecked(True)
            self.resize(max(self.width(), 1100), max(self.height(), 720))
            self.doc_editor.view.setFocus()
            return
        if kind == "model":
            if self.viewer3d is None:
                from umc.gui.editor3d.viewer_page import ViewerPage

                self.viewer3d = ViewerPage()
                self.viewer3d.back_requested.connect(self.leave_edit)
                self.stack.addWidget(self.viewer3d)
            self._convert_size = self.size()
            self.viewer3d.open_files(list(self.files))
            self.stack.setCurrentWidget(self.viewer3d)
            self.edit_mode.setChecked(True)
            self.resize(max(self.width(), 1100), max(self.height(), 720))
            self.viewer3d.view.setFocus()
            return
        if self.editor is None:
            from umc.gui.editor.editor_page import EditorPage

            self.editor = EditorPage()
            self.editor.back_requested.connect(self.leave_edit)
            self.stack.addWidget(self.editor)
        if not self.editor.open_paths([f.path for f in self.files]):
            self.convert_mode.setChecked(True)
            self.input_error.setText(self.editor.message.text())
            self.input_error.setVisible(True)
            return
        self._convert_size = self.size()
        self.stack.setCurrentWidget(self.editor)
        self.edit_mode.setChecked(True)
        self.resize(max(self.width(), 1100), max(self.height(), 720))
        self.editor.canvas.fit()
        self.editor.canvas.setFocus()

    def leave_edit(self) -> None:
        page = self._current_editor()
        if page is None:
            self.convert_mode.setChecked(True)
            return
        if not page.maybe_leave():
            self.edit_mode.setChecked(True)
            return
        self.stack.setCurrentIndex(0)
        self.convert_mode.setChecked(True)
        if self._convert_size is not None:
            self.resize(self._convert_size)

    def on_target_changed(self, target: Target | None) -> None:
        self.target = target
        if not self.busy:
            self._clear_results()
            self.file_list.reset_states()
            self.progress.setValue(0)
            self._set_status("")
        if target is not None:
            self.settings.setValue("last_target", target.id)
            cls = target.converter.options_cls
            values = self.option_values.get(cls.__name__) or opts.defaults(cls)
            self.settings_panel.set_schema(cls, target.id, values)
        else:
            self.settings_panel.set_schema(None, "", {})
        self.tabs.set_target(
            target.label if target else None, self.settings_panel.has_visible_fields()
        )
        self._update_buttons()

    def _remember_values(self) -> None:
        if self.target is not None:
            self.option_values[self.target.converter.options_cls.__name__] = (
                self.settings_panel.values()
            )

    def reset_settings(self) -> None:
        if self.target is None:
            return
        cls = self.target.converter.options_cls
        self.option_values.pop(cls.__name__, None)
        self.settings_panel.set_schema(cls, self.target.id, opts.defaults(cls))

    # ------------------------------------------------------------------ output folder
    def _set_output_dir(self, path: Path | None) -> None:
        self.output_dir = path
        self.settings.setValue("output_dir", str(path) if path else "")
        text = f"Save to: {path.name or path}" if path else "Save to: same folder"
        self.dest_button.setText(text + "  ▾")
        self.dest_button.setToolTip(str(path) if path else "Next to each original file")

    def _choose_output_dir(self) -> None:
        start = str(self.output_dir or (self.files[0].path.parent if self.files else Path.home()))
        chosen = QFileDialog.getExistingDirectory(self, "Save converted files to", start)
        if chosen:
            self._set_output_dir(Path(chosen))

    def open_output(self) -> None:
        if not self.last_outputs:
            return
        p = Path(self.last_outputs[0])
        folder = p if p.is_dir() else p.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reveal_result(self, item: QListWidgetItem) -> None:
        p = Path(item.data(Qt.ItemDataRole.UserRole))
        folder = p if p.is_dir() else p.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------ results
    def _clear_results(self) -> None:
        self.last_outputs = []
        self.results_list.clear()
        self.results_card.hide()

    def _show_results(self, outputs: list[str]) -> None:
        pal = theme.palette()
        self.results_list.clear()
        total = 0
        for out in outputs[:MAX_RESULTS]:
            p = Path(out)
            try:
                size = p.stat().st_size if p.is_file() else 0
            except OSError:
                size = 0
            total += size
            label = p.name + (f"   ·   {fmt_size(size)}" if size else "")
            item = QListWidgetItem(icons.icon("check", pal["success"], 18), label)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(str(p))
            self.results_list.addItem(item)
        if len(outputs) > MAX_RESULTS:
            self.results_list.addItem(f"…and {len(outputs) - MAX_RESULTS} more")
        n = len(outputs)
        size_text = f" · {fmt_size(total)}" if total else ""
        self.results_label.setText(f"Results · {n} file{'s' if n != 1 else ''}{size_text}")
        self.results_card.setVisible(bool(outputs))
        self._update_left_sizes()

    # ------------------------------------------------------------------ conversion
    @property
    def busy(self) -> bool:
        return self._worker is not None

    def _update_buttons(self) -> None:
        ready = bool(self.files) and self.target is not None
        self.convert_button.setEnabled(self.busy or ready)
        n = len(self.files)
        if self.busy:
            text = "Cancel"
        elif n > 1 and ready:
            text = f"Convert {n} files"
        else:
            text = "Convert"
        self.convert_button.setText(text)
        self.convert_button.setAccessibleName("Cancel" if self.busy else "Convert")
        self.convert_button.setProperty("danger", self.busy)
        self.convert_button.style().unpolish(self.convert_button)
        self.convert_button.style().polish(self.convert_button)
        self.open_button.setVisible(bool(self.last_outputs) and not self.busy)
        self.progress.setVisible(self.busy or self.progress.value() > 0)
        for w in (self.drop, self.picker, self.tabs, self.clear_button):
            w.setEnabled(not self.busy)
        self.file_list.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop
            if self.busy
            else QAbstractItemView.DragDropMode.InternalMove
        )
        if not self.busy and not self.last_outputs and self.status.objectName() == "Muted":
            self._set_status(self._idle_status())

    def _idle_status(self) -> str:
        if not self.files:
            return "Add files to get started."
        if self.target is None:
            return "Choose an output format."
        fams = {f.family for f in self.files}
        n = len(self.files)
        if len(fams) == 1:
            what = f"{n} {FAMILY_NOUN[next(iter(fams))][0 if n == 1 else 1]}"
        else:
            what = f"{n} files"
        return f"{what} → {self.target.label}"

    def on_convert_clicked(self) -> None:
        if self.busy:
            self._worker.cancel()
            self.status.setText("Cancelling…")
            return
        if not self.files or self.target is None:
            return
        cls = self.target.converter.options_cls
        options = opts.build(cls, {**opts.defaults(cls), **self.settings_panel.values()})
        self._sync_order()
        job = ConversionJob(
            files=list(self.files), target=self.target, options=options, output_dir=self.output_dir
        )
        self._start(job)

    def _start(self, job: ConversionJob) -> None:
        self._last_job = job
        self._clear_results()
        self.progress.setValue(0)
        self.file_list.set_progress(file_fractions(0.0, len(job.files)))
        self._set_status("Starting…")
        self._thread, self._worker = start_worker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.tool_missing.connect(self._on_tool_missing)
        self._thread.finished.connect(self._on_done)
        self._update_buttons()

    def _on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(round(percent * 10))
        self.file_list.set_progress(file_fractions(percent / 100, self.file_list.count()))
        if message:
            self._set_status(f"{message} — {percent:.0f}%")

    def _on_success(self, outputs: list[str]) -> None:
        self.progress.setValue(1000)
        self.file_list.mark_done()
        self._show_results(outputs)
        self.last_outputs = outputs
        n = len(outputs)
        what = "folder" if n == 1 and Path(outputs[0]).is_dir() else "file"
        names = Path(outputs[0]).name if n == 1 else f"{n} {what}s"
        self._set_status(f"Done — saved {names}", "Success")

    def _on_failed(self, message: str) -> None:
        self.file_list.mark_failed(message)
        self._set_status(message, "Error")

    def _on_cancelled(self) -> None:
        self.file_list.cancel_running()
        self._set_status("Cancelled.")

    def _on_done(self) -> None:
        self._worker = None
        self._thread = None
        self._update_buttons()
        pending, self._pending_tool = self._pending_tool, None
        if pending is not None:
            dialog = ToolMissingDialog(*pending, parent=self)
            if dialog.exec() and self._last_job is not None:
                self._last_job.cancel_event.clear()
                self._start(self._last_job)

    def _on_tool_missing(self, tool: str, url: str, message: str) -> None:
        self.file_list.mark_failed(message)
        self._set_status(message, "Error")
        self._pending_tool = (tool, url, message)  # shown once the worker has finished

    def _set_status(self, text: str, kind: str = "Muted") -> None:
        self.status.setObjectName(kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)

    # Allow dropping onto the whole window, not just the drop zone.
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and not self.busy:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.handle_drop(paths)

    def handle_drop(self, paths: list[str]) -> None:
        page = self._current_editor()
        if page is not None and page is self.doc_editor:
            self.doc_editor.drop_paths([Path(p) for p in self._expand(paths)])
        elif page is not None and page is self.viewer3d:
            self._drop_models(paths)
        elif page is not None:
            self.editor.add_paths([Path(p) for p in self._expand(paths)])
        elif not self.busy:
            self.add_files(paths)

    def _drop_models(self, paths: list[str]) -> None:
        """Models dropped on the 3D viewer join the queue and are shown (others are ignored)."""
        new = [f for f in (detect(p) for p in self._expand(paths)) if f is not None]
        new = [f for f in new if f.family == MediaFamily.MODEL]
        new = [f for f in new if all(f.path != x.path for x in self.files)]
        if not new:
            return
        self.add_files([str(f.path) for f in new])
        self.viewer3d.open_files([f for f in self.files if f.family == MediaFamily.MODEL])
        self.viewer3d.file_box.setCurrentIndex(self.viewer3d.files.index(new[0]))

    def closeEvent(self, event) -> None:  # noqa: N802
        page = self._current_editor()
        if page is not None and not page.maybe_leave():
            event.ignore()
            return
        if self.editor is not None:
            self.editor.stop_background_removal()
        if self.viewer3d is not None:
            self.viewer3d.wait_idle(5000)
        if self._worker is not None:
            self._worker.cancel()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(5000)
        if self.stack.currentIndex() == 0:
            self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
