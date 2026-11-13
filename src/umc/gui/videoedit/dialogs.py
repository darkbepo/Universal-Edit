"""Dialogs of the video editor."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.errors import ConversionCancelled, ConversionError
from umc.core.job import unique_path
from umc.core.options import build, option_specs
from umc.gui.widgets.settings_panel import SettingsPanel
from umc.videoedit.export import OWN_FILE, ExportOptions, is_project_file, run_export
from umc.videoedit.project import Project
from umc.videoedit.projectfile import MissingSource

log = logging.getLogger(__name__)

_SETTINGS_KEY = "videoedit/export"
_CONTAINERS = frozenset(
    str(value)
    for spec in option_specs(ExportOptions)
    if spec.name == "container"
    for value, _label in spec.choices
)


def with_container(path: Path, container: str) -> Path:
    """``path`` ending in ``.container``: a known container extension is replaced, any other
    one kept (``my.clip`` → ``my.clip.mp4``). A drive root (no name) is returned as is."""
    if not path.name:
        return path
    if path.suffix.lower().lstrip(".") in _CONTAINERS:
        return path.with_suffix(f".{container}")
    return path.with_name(f"{path.name}.{container}")


class LocateDialog(QDialog):
    """Missing project files: find each one, or remove it (and its clips) from the project.

    After ``exec()``, ``choices`` maps every missing source id to the file found, or None
    ("Remove from project", the default).
    """

    def __init__(self, missing: list[MissingSource], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing files")
        self.missing = list(missing)
        self.choices: dict[str, Path | None] = {m.source_id: None for m in self.missing}
        root = QVBoxLayout(self)
        intro = QLabel(
            "Some files of this project can't be found. Find them, or remove them (and the clips"
            " that use them) from the project."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        grid = QGridLayout()
        self._status: dict[str, QLabel] = {}
        for row, m in enumerate(self.missing):
            grid.addWidget(QLabel(f"“{m.path.name}”"), row, 0)
            status = QLabel("Will be removed", objectName="Muted")
            grid.addWidget(status, row, 1)
            self._status[m.source_id] = status
            find_button = QPushButton("Find file…", objectName="Small")
            find_button.clicked.connect(lambda _=False, m=m: self._find(m))
            grid.addWidget(find_button, row, 2)
            remove = QPushButton("Remove from project", objectName="Small")
            remove.clicked.connect(lambda _=False, m=m: self.choose(m.source_id, None))
            grid.addWidget(remove, row, 3)
        root.addLayout(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open project")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def choose(self, source_id: str, path: Path | None) -> None:
        self.choices[source_id] = path
        self._status[source_id].setText(f"Use “{path.name}”" if path else "Will be removed")

    def _find(self, m: MissingSource) -> None:
        folder = m.path.parent if m.path.parent.exists() else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self, f"Find “{m.path.name}”", str(folder / m.path.name), "All files (*)"
        )
        if path:
            self.choose(m.source_id, Path(path))


class _ExportWorker(QObject):
    progress = Signal(float, str)
    succeeded = Signal(str)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, project: Project, options: ExportOptions, dest: Path) -> None:
        super().__init__()
        self.project, self.options, self.dest = project, options, dest
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            out = run_export(
                self.project,
                self.options,
                self.dest,
                on_progress=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_event=self.cancel_event,
            )
            self.succeeded.emit(str(out))
        except ConversionCancelled:
            self.cancelled.emit()
        except ConversionError as exc:
            log.error("video export failed: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("video export failed")
            self.failed.emit("Couldn't export the video. See the log for details.")


class ExportDialog(QDialog):
    """Export the project: format settings, where to save, progress. Never changes the project."""

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export video")
        self.setMinimumWidth(520)
        self.project = project
        self.running = False
        self._thread: QThread | None = None
        self._worker: _ExportWorker | None = None
        self._result: Path | None = None
        self._close_when_done = False

        root = QVBoxLayout(self)
        self.settings = SettingsPanel()
        values = self._saved_values()
        self.settings.set_schema(ExportOptions, values.get("container", "mp4"), values)
        self.settings.changed.connect(self._container_changed)
        root.addWidget(self.settings)

        row = QHBoxLayout()
        row.addWidget(QLabel("Save as"))
        self.path_edit = QLineEdit()
        row.addWidget(self.path_edit, 1)
        self.browse_button = QPushButton("Browse…", objectName="Small")
        self.browse_button.clicked.connect(self._browse)
        row.addWidget(self.browse_button)
        root.addLayout(row)
        self.path_edit.setText(str(self._default_path()))

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        root.addWidget(self.progress)
        self.message = QLabel("", objectName="Muted")
        self.message.setWordWrap(True)
        root.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_folder_button.hide()
        buttons.addWidget(self.open_folder_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)
        self.export_button = QPushButton("Export", objectName="Primary")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.start)
        buttons.addWidget(self.export_button)
        root.addLayout(buttons)

    # ------------------------------------------------------------ settings
    @staticmethod
    def _saved_values() -> dict:
        try:
            data = json.loads(str(QSettings().value(_SETTINGS_KEY, "") or "{}"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def remember(self) -> None:
        QSettings().setValue(_SETTINGS_KEY, json.dumps(self.settings.values()))

    def options(self) -> ExportOptions:
        return build(ExportOptions, self.settings.values())

    @property
    def container(self) -> str:
        return str(self.settings.values().get("container", "mp4"))

    # ------------------------------------------------------------ path
    def _default_path(self) -> Path:
        first = self.project.sources[0].path if self.project.sources else Path.home() / "video"
        return unique_path(first.parent, f"{first.stem}_edit", self.container)

    def _container_changed(self) -> None:
        text = self.path_edit.text().strip()
        if text:
            new = with_container(Path(text), self.container)
            if new != Path(text):
                self.path_edit.setText(str(new))

    def _browse(self) -> None:
        ext = self.container
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as",
            self.path_edit.text(),
            f"{ext.upper()} (*.{ext})",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.path_edit.setText(str(with_container(Path(path), ext)))

    def _confirm_replace(self, path: Path) -> bool:
        answer = QMessageBox.question(
            self,
            "Replace file?",
            f"Replace “{path.name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------ running
    def _show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(True)
        self.export_button.setVisible(not running)
        self.close_button.setEnabled(not running)
        self.settings.setEnabled(not running)
        self.path_edit.setEnabled(not running)
        self.browse_button.setEnabled(not running)

    def start(self) -> None:
        if self.running:
            return
        text = self.path_edit.text().strip()
        if not text:
            self._show_message("Choose where to save the video.", error=True)
            return
        dest = with_container(Path(text), self.container)
        if not dest.name or dest.is_dir():
            self._show_message("Choose a file name for the video.", error=True)
            return
        self.path_edit.setText(str(dest))
        if is_project_file(self.project, dest):
            self._show_message(OWN_FILE, error=True)
            return
        if dest.exists() and not self._confirm_replace(dest):
            return
        self.remember()
        self._result = None
        self.open_folder_button.hide()
        self.progress.setValue(0)
        self.progress.show()
        self._show_message("Starting…")
        self._set_running(True)
        thread = QThread(self)
        worker = _ExportWorker(self.project, self.options(), dest)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_done)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        for sig in (worker.succeeded, worker.cancelled, worker.failed):
            sig.connect(thread.quit)
        # References are kept until QThread.finished (CLAUDE.md). The worker is never given to
        # deleteLater: that would delete it on the worker thread while the GUI thread drops the
        # last Python reference (two deletions racing). _thread_finished runs on the GUI thread.
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_progress(self, percent: float, message: str) -> None:
        self.progress.setValue(round(percent))
        self._show_message(message)

    def _on_done(self, path: str) -> None:
        self._result = Path(path)
        self.progress.setValue(100)
        self._show_message(f"Saved “{self._result.name}”.")
        self.open_folder_button.show()

    def _on_cancelled(self) -> None:
        self.progress.hide()
        self._show_message("Export cancelled.")

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self._show_message(message, error=True)

    @Slot()
    def _thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.wait()  # finished comes just before the thread really ends
        # Dropping the worker deletes it here, on the GUI thread (see gui/videoedit/threads.py).
        self._thread, self._worker = None, None
        self._set_running(False)
        if self._close_when_done:
            self._close_when_done = False
            self.reject()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self._show_message("Cancelling…")

    def _open_folder(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.parent)))

    def done(self, result: int) -> None:  # noqa: N802
        if self._thread is not None:
            # Closed (×, Esc) mid-export: never block or leave the thread running behind a
            # hidden window. Cancel, and _thread_finished closes the window once it has ended.
            self._close_when_done = True
            self._cancel()
            return
        super().done(result)
