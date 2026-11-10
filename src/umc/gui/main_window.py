"""The single application window: input → output format → settings → Convert."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import options as opts
from umc.core.detect import DetectedFile, MediaFamily, detect
from umc.core.job import ConversionJob
from umc.core.registry import MixedInputError, Target, targets_for
from umc.gui.tool_dialog import ToolMissingDialog
from umc.gui.widgets.drop_zone import DropZone
from umc.gui.widgets.format_picker import FormatPicker
from umc.gui.widgets.settings_panel import SettingsPanel
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
}
ICONS = {
    MediaFamily.IMAGE: "🖼",
    MediaFamily.VIDEO: "🎞",
    MediaFamily.AUDIO: "🎵",
    MediaFamily.MODEL: "🧊",
    MediaFamily.DOCUMENT: "📄",
    MediaFamily.ARCHIVE: "🗜",
    MediaFamily.SUBTITLE: "💬",
    MediaFamily.FONT: "🔤",
    MediaFamily.VECTOR: "✒",
    MediaFamily.CAD: "📐",
}


def section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Section")
    return label


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Universal Media Converter")
        self.resize(620, 780)
        self.setMinimumSize(480, 560)
        self.settings = QSettings()
        self.files: list[DetectedFile] = []
        self.target: Target | None = None
        self.option_values: dict[str, dict] = {}  # per options class, remembered across targets
        self.last_outputs: list[str] = []
        self._thread = None
        self._worker = None
        self._last_job: ConversionJob | None = None
        self._pending_tool: tuple[str, str, str] | None = None

        root = QWidget(objectName="Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------------------------------------------------- scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(objectName="Content")
        body = QVBoxLayout(content)
        body.setContentsMargins(28, 24, 28, 16)
        body.setSpacing(12)

        title = QLabel("Universal Media Converter", objectName="Title")
        subtitle = QLabel(
            "Documents, images and video — drop it in, pick a format.", objectName="Subtitle"
        )
        body.addWidget(title)
        body.addWidget(subtitle)
        body.addSpacing(8)

        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_files)
        body.addWidget(self.drop)

        # Convert | Edit — only shown when every added file is an image
        self.mode_bar = QWidget(objectName="ModeBar")
        mb = QHBoxLayout(self.mode_bar)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(0)
        self.convert_mode = QPushButton("Convert", objectName="SegmentLeft")
        self.edit_mode = QPushButton("Edit", objectName="SegmentRight")
        self._mode_group = QButtonGroup(self)
        for b in (self.convert_mode, self.edit_mode):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._mode_group.addButton(b)
            mb.addWidget(b)
        self.convert_mode.setChecked(True)
        self.edit_mode.setToolTip(
            "Open the images in the image editor (each image becomes a layer)"
        )
        self.convert_mode.clicked.connect(self.leave_edit)
        self.edit_mode.clicked.connect(self.enter_edit)
        mb.addStretch(1)
        body.addWidget(self.mode_bar)
        self.editor = None  # EditorPage, created on first use
        self._convert_size = None

        # files card
        self.files_card = QFrame(objectName="Card")
        fc = QVBoxLayout(self.files_card)
        fc.setContentsMargins(14, 10, 14, 10)
        head = QHBoxLayout()
        self.files_label = QLabel()
        self.files_label.setStyleSheet("font-weight: 600;")
        clear = QPushButton("Clear", objectName="Link")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear_files)
        head.addWidget(self.files_label)
        head.addStretch(1)
        head.addWidget(clear)
        fc.addLayout(head)
        self.file_list = QListWidget(objectName="FileList")
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.model().rowsMoved.connect(self._sync_order)
        self.file_list.setToolTip("Drag to reorder · Delete to remove")
        remove = QAction(self.file_list)
        remove.setShortcuts([QKeySequence.StandardKey.Delete, QKeySequence(Qt.Key.Key_Backspace)])
        remove.triggered.connect(self.remove_selected)
        self.file_list.addAction(remove)
        fc.addWidget(self.file_list)
        self.files_hint = QLabel("Drag to reorder · Delete key removes", objectName="Help")
        fc.addWidget(self.files_hint)
        body.addWidget(self.files_card)

        self.input_error = QLabel(objectName="Error")
        self.input_error.setWordWrap(True)
        body.addWidget(self.input_error)

        # output format
        self.format_section = section("Convert to")
        body.addWidget(self.format_section)
        self.picker = FormatPicker()
        self.picker.target_changed.connect(self.on_target_changed)
        body.addWidget(self.picker)

        # settings
        self.settings_toggle = QToolButton(objectName="Disclosure")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.settings_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_toggle.toggled.connect(self._toggle_settings)
        body.addWidget(self.settings_toggle)
        self.settings_card = QFrame(objectName="Card")
        sc = QVBoxLayout(self.settings_card)
        sc.setContentsMargins(18, 14, 18, 16)
        self.settings_panel = SettingsPanel()
        self.settings_panel.changed.connect(self._remember_values)
        reset = QPushButton("Reset to defaults", objectName="Link")
        reset.clicked.connect(self.reset_settings)
        sc.addWidget(self.settings_panel)
        sc.addWidget(reset, alignment=Qt.AlignmentFlag.AlignRight)
        body.addWidget(self.settings_card)
        body.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ---------------------------------------------------------- bottom bar
        bar = QFrame(objectName="Card")
        bar.setStyleSheet(
            "QFrame#Card { border-radius: 0; border-left: none; border-right: none;"
            " border-bottom: none; }"
        )
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(28, 12, 28, 14)
        bl.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.status = QLabel(objectName="Muted")
        self.status.setWordWrap(True)
        bl.addWidget(self.progress)
        bl.addWidget(self.status)
        row = QHBoxLayout()
        self.dest_button = QToolButton(objectName="Link")
        self.dest_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.dest_button)
        menu.addAction("Same folder as the original", lambda: self._set_output_dir(None))
        menu.addAction("Choose folder…", self._choose_output_dir)
        self.dest_button.setMenu(menu)
        self.open_button = QPushButton("Open folder")
        self.open_button.clicked.connect(self.open_output)
        self.convert_button = QPushButton("Convert", objectName="Primary")
        self.convert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.convert_button.clicked.connect(self.on_convert_clicked)
        row.addWidget(self.dest_button)
        row.addStretch(1)
        row.addWidget(self.open_button)
        row.addWidget(self.convert_button)
        bl.addLayout(row)
        outer.addWidget(bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(root)
        self.setCentralWidget(self.stack)
        self.setAcceptDrops(True)
        saved = self.settings.value("output_dir", "", str)
        self._set_output_dir(Path(saved) if saved and Path(saved).is_dir() else None)
        self.settings_toggle.setChecked(self.settings.value("settings_open", False, bool))
        self._refresh()

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
        self.files.clear()
        self._rebuild_list()
        self._refresh()

    def remove_selected(self) -> None:
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()), reverse=True)
        for r in rows:
            del self.files[r]
        self._rebuild_list()
        self._refresh()

    def _rebuild_list(self) -> None:
        self.file_list.clear()
        for f in self.files:
            item = QListWidgetItem(f"{ICONS[f.family]}  {f.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(f.path))
            item.setToolTip(str(f.path))
            self.file_list.addItem(item)
        rows = min(len(self.files), 6)
        row_h = self.file_list.sizeHintForRow(0) if self.files else 30
        self.file_list.setFixedHeight(max(1, rows) * row_h + 6)

    def _sync_order(self, *_args) -> None:
        by_path = {str(f.path): f for f in self.files}
        self.files = [
            by_path[self.file_list.item(i).data(Qt.ItemDataRole.UserRole)]
            for i in range(self.file_list.count())
        ]

    # ------------------------------------------------------------------ targets / settings
    def _refresh(self, extra_error: str = "") -> None:
        if not self.busy:
            self.last_outputs = []
            self.progress.setValue(0)
            self._set_status("")
        has_files = bool(self.files)
        self.drop.set_compact(has_files)
        self.files_card.setVisible(has_files)
        error = extra_error
        targets: list[Target] = []
        if has_files:
            fams = {f.family for f in self.files}
            if len(fams) == 1:
                fam = fams.pop()
                noun = FAMILY_NOUN[fam][0 if len(self.files) == 1 else 1]
                self.files_label.setText(f"{len(self.files)} {noun}")
            else:
                self.files_label.setText(f"{len(self.files)} files")
            self.files_hint.setVisible(len(self.files) > 1)
            try:
                targets = targets_for(self.files)
            except MixedInputError as exc:
                error = str(exc)
        self.input_error.setText(error)
        self.input_error.setVisible(bool(error))
        self.mode_bar.setVisible(self.edit_available())
        self.format_section.setVisible(bool(targets))
        self.picker.setVisible(bool(targets))
        keep = self.target.id if self.target else self.settings.value("last_target", "", str)
        self.picker.set_targets(targets, keep_id=keep)
        self._update_buttons()

    # ------------------------------------------------------------------ Convert | Edit
    def edit_available(self) -> bool:
        return bool(self.files) and all(f.family == MediaFamily.IMAGE for f in self.files)

    def enter_edit(self) -> None:
        if self.stack.currentIndex() == 1:
            return
        if not self.edit_available() or self.busy:
            self.convert_mode.setChecked(True)
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
        if self.stack.currentIndex() != 1:
            self.convert_mode.setChecked(True)
            return
        if not self.editor.maybe_leave():
            self.edit_mode.setChecked(True)
            return
        self.stack.setCurrentIndex(0)
        self.convert_mode.setChecked(True)
        if self._convert_size is not None:
            self.resize(self._convert_size)

    def on_target_changed(self, target: Target | None) -> None:
        self.target = target
        if not self.busy:
            self.last_outputs = []
            self.progress.setValue(0)
            self._set_status("")
        if target is not None:
            self.settings.setValue("last_target", target.id)
            cls = target.converter.options_cls
            values = self.option_values.get(cls.__name__) or opts.defaults(cls)
            self.settings_panel.set_schema(cls, target.id, values)
        else:
            self.settings_panel.set_schema(None, "", {})
        self._update_settings_visibility()
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

    def _toggle_settings(self, open_: bool) -> None:
        self.settings.setValue("settings_open", open_)
        self._update_settings_visibility()

    def _update_settings_visibility(self) -> None:
        available = self.target is not None and self.settings_panel.has_visible_fields()
        self.settings_toggle.setVisible(available)
        open_ = self.settings_toggle.isChecked()
        self.settings_toggle.setText(("▾  " if open_ else "▸  ") + "Settings")
        self.settings_card.setVisible(available and open_)

    # ------------------------------------------------------------------ output folder
    def _set_output_dir(self, path: Path | None) -> None:
        self.output_dir = path
        self.settings.setValue("output_dir", str(path) if path else "")
        text = f"Save to: {path.name or path}" if path else "Save to: same folder as original"
        self.dest_button.setText(text + "  ▾")
        self.dest_button.setToolTip(str(path) if path else "")

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

    # ------------------------------------------------------------------ conversion
    @property
    def busy(self) -> bool:
        return self._worker is not None

    def _update_buttons(self) -> None:
        ready = bool(self.files) and self.target is not None
        self.convert_button.setEnabled(self.busy or ready)
        self.convert_button.setText("Cancel" if self.busy else "Convert")
        self.convert_button.setProperty("danger", self.busy)
        self.convert_button.style().unpolish(self.convert_button)
        self.convert_button.style().polish(self.convert_button)
        self.open_button.setVisible(bool(self.last_outputs) and not self.busy)
        self.progress.setVisible(self.busy or self.progress.value() > 0)
        for w in (self.drop, self.file_list, self.picker, self.settings_card):
            w.setEnabled(not self.busy)
        if not self.busy and not self.last_outputs and self.status.objectName() == "Muted":
            self._set_status(self._idle_status())

    def _idle_status(self) -> str:
        if not self.files:
            return "Add files to get started."
        if self.target is None:
            return "Choose an output format."
        n = len(self.files)
        plural = "s" if n != 1 else ""
        return f"Ready to convert {n} file{plural} to {self.target.label}."

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
        self.last_outputs = []
        self.progress.setValue(0)
        self._set_status("Starting…")
        self._thread, self._worker = start_worker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(lambda m: self._set_status(m, "Error"))
        self._worker.cancelled.connect(lambda: self._set_status("Cancelled."))
        self._worker.tool_missing.connect(self._on_tool_missing)
        self._thread.finished.connect(self._on_done)
        self._update_buttons()

    def _on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(round(percent * 10))
        if message:
            self._set_status(f"{message} — {percent:.0f}%")

    def _on_success(self, outputs: list[str]) -> None:
        self.last_outputs = outputs
        self.progress.setValue(1000)
        n = len(outputs)
        what = "folder" if n == 1 and Path(outputs[0]).is_dir() else "file"
        names = Path(outputs[0]).name if n == 1 else f"{n} {what}s"
        self._set_status(f"Done — saved {names}", "Success")

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
        if self.stack.currentIndex() == 1 and self.editor is not None:
            self.editor.add_paths([Path(p) for p in self._expand(paths)])
        else:
            self.add_files(paths)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentIndex() == 1 and not self.editor.maybe_leave():
            event.ignore()
            return
        if self._worker is not None:
            self._worker.cancel()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(5000)
        super().closeEvent(event)
