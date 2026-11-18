"""The small window a right-click "Quick Convert" opens: converts the files one after another
with the default settings, saving each result next to its original, then closes by itself.

Explorer starts the app once per selected file; every file reaches this one window (through
``single_instance`` and ``AppController.quick_convert``) and joins its queue. A missing tool
or another problem is listed in plain words, with "Open in Universal Edit" to sort it out in
the full window (which has the one-click installers and all settings)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import quickconvert as qc
from umc.gui import branding
from umc.gui.widgets.flow_layout import FlowLayout
from umc.gui.worker import start_worker

log = logging.getLogger(__name__)

CLOSE_AFTER_MS = 5000  # after everything went well (not while the pointer is over the window)


@dataclass
class _Item:
    path: Path
    target_id: str
    outputs: list[str] = field(default_factory=list)
    problem: str = ""  # plain-language reason it didn't work
    tool_missing: bool = False


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()


class QuickConvertWindow(QWidget):
    open_in_app = Signal(list)  # list[str]: files to open in the converter window

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(f"{qc.MENU_TEXT} — {branding.APP_NAME}")
        self.setMinimumWidth(580)
        self.items: list[_Item] = []
        self.index = 0  # the item being converted (== len(items) when all are done)
        self.cancelled = False
        self._thread = None
        self._worker = None
        self._hovered = False  # the pointer is over it: someone is reading, don't close

        self.title = QLabel(objectName="CardTitle")
        self.line = QLabel(objectName="Muted")
        self.line.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.problems = QLabel(objectName="QuickProblems")
        self.problems.setWordWrap(True)
        self.problems.hide()
        self.show_button = QPushButton("Show in folder", objectName="Secondary")
        self.show_button.clicked.connect(self.show_in_folder)
        self.open_button = QPushButton(f"Open in {branding.APP_NAME}", objectName="Secondary")
        self.open_button.clicked.connect(self._open_in_app)
        self.cancel_button = QPushButton("Cancel", objectName="Secondary")
        self.cancel_button.clicked.connect(self.cancel)
        self.close_button = QPushButton("Close", objectName="Primary")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.show_button, self.open_button, self.cancel_button, self.close_button):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(self.title)
        lay.addWidget(self.line)
        lay.addWidget(self.bar)
        lay.addWidget(self.problems)
        self.picker = QFrame(objectName="QuickPicker")  # "Quick Convert…" without a format yet
        self.picker_layout = QVBoxLayout(self.picker)
        self.picker_layout.setContentsMargins(0, 0, 0, 0)
        self.picker_layout.setSpacing(6)
        self.picker.hide()
        self._picking: list[str] = []
        self._pending_choice: list[str] | None = None
        lay.addWidget(self.picker)
        lay.addLayout(buttons)
        self._close_timer = QTimer(self, singleShot=True, interval=CLOSE_AFTER_MS)
        self._close_timer.timeout.connect(self._auto_close)
        self._update()

    # ------------------------------------------------------------ queue
    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def done(self) -> bool:
        return not self.busy and self.index >= len(self.items)

    def add(self, target_id: str, paths: list[str]) -> None:
        if self.cancelled and not self.busy:  # what was left when Cancel was pressed stays undone
            self.items = self.items[: self.index]
        known = {(i.path, i.target_id) for i in self.items[self.index :]}
        for p in paths:
            item = _Item(Path(p), target_id)
            if (item.path, target_id) not in known:  # the same file sent twice
                self.items.append(item)
        self.cancelled = False
        self._close_timer.stop()
        self._next()

    def choose(self, paths: list[str]) -> None:
        """Show the formats ``paths`` can become; a click converts them (Thunar / Nemo's
        "Quick Convert…", which can't hold a submenu)."""
        if self.busy:
            self._pending_choice = list(paths)
            return
        self._picking = [p for p in paths if Path(p).is_file()]
        while self.picker_layout.count():
            item = self.picker_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                _clear(item.layout())
        menu = qc.menu_for_files([Path(p) for p in self._picking])
        for group, items in menu.groups():
            self.picker_layout.addWidget(QLabel(group, objectName="Muted"))
            row = FlowLayout(spacing=6)
            for it in items:
                b = QPushButton(it.label, objectName="Secondary")
                b.clicked.connect(lambda _c=False, t=it.target_id: self._picked(t))
                row.addWidget(b)
            holder = QWidget()
            holder.setLayout(row)
            self.picker_layout.addWidget(holder)
        self.picker.setVisible(bool(menu.items))
        self.title.setText("Convert to…" if menu.items else "Nothing to convert")
        names = ", ".join(Path(p).name for p in self._picking[:3])
        more = len(self._picking) - 3
        self.line.setText(names + (f" and {more} more" if more > 0 else ""))
        if not menu.items:
            self.line.setText("These files can't be converted to one common format.")
        self.bar.hide()
        for b in (self.show_button, self.open_button, self.cancel_button):
            b.hide()
        self.close_button.show()
        self.adjustSize()

    def _picked(self, target_id: str) -> None:
        paths, self._picking = self._picking, []
        self.picker.hide()
        self.bar.show()
        self.add(target_id, paths)

    def _next(self) -> None:
        if self.busy or self.cancelled:
            self._update()
            return
        while self.index < len(self.items):
            item = self.items[self.index]
            try:
                job = qc.job_for(item.path, item.target_id)
            except qc.QuickConvertError as exc:
                item.problem = str(exc)
                self.index += 1
                continue
            self._thread, self._worker = start_worker(job, self)
            self._worker.progress.connect(self._on_progress)
            self._worker.succeeded.connect(self._on_success)
            self._worker.failed.connect(self._on_failed)
            self._worker.tool_missing.connect(self._on_tool_missing)
            self._worker.cancelled.connect(self._on_cancelled)
            self._thread.finished.connect(self._on_finished)
            break
        self._update()
        if not self.busy and self._pending_choice is not None:
            paths, self._pending_choice = self._pending_choice, None
            self.choose(paths)

    def cancel(self) -> None:
        self.cancelled = True
        if self._worker is not None:
            self._worker.cancel()
        self._update()

    # ------------------------------------------------------------ worker → window
    def _current(self) -> _Item:
        return self.items[self.index]

    def _on_progress(self, percent: float, _message: str) -> None:
        done = self.index + percent / 100
        self.bar.setValue(round(1000 * done / max(1, len(self.items))))

    def _on_success(self, outputs: list) -> None:
        self._current().outputs = [str(o) for o in outputs]

    def _on_failed(self, message: str) -> None:
        self._current().problem = message

    def _on_tool_missing(self, tool: str, _url: str, message: str) -> None:
        item = self._current()
        item.problem = message or f"This needs {tool}."
        item.tool_missing = True

    def _on_cancelled(self) -> None:
        self._current().problem = "Cancelled."

    def _on_finished(self) -> None:
        self._thread = self._worker = None
        self.index += 1
        self._next()

    # ------------------------------------------------------------ what it shows
    def outputs(self) -> list[str]:
        return [o for i in self.items for o in i.outputs]

    def _update(self) -> None:
        n = len(self.items)
        if self.busy:
            item = self._current()
            label = qc.label_of(item.target_id, item.path)
            self.title.setText(f"Converting {self.index + 1} of {n}" if n > 1 else "Converting")
            self.line.setText(f"{item.path.name} → {label}")
        elif n and self.index < n:  # cancelled with files left
            self.title.setText("Stopped")
            self.line.setText(f"{self.index} of {n} files were done before you cancelled.")
        else:
            made = self.outputs()
            ok = sum(1 for i in self.items if i.outputs)
            if ok and ok == n:
                self.title.setText("Done")
                self.bar.setValue(1000)
            elif ok:
                self.title.setText(f"{ok} of {n} converted")
            else:
                self.title.setText("Nothing was converted" if n else qc.MENU_TEXT)
            if made:
                where = "next to the original" if ok == 1 else "next to the originals"
                saved = "1 file" if len(made) == 1 else f"{len(made)} files"
                self.line.setText(f"{saved} saved {where}.")
            else:
                self.line.setText("")
            if ok and ok == n and not self._hovered:
                self._close_timer.start()
        problems = [f"{i.path.name}: {i.problem}" for i in self.items if i.problem]
        self.problems.setText("\n".join(problems))
        self.problems.setVisible(bool(problems))
        finished = not self.busy
        self.cancel_button.setVisible(self.busy)
        self.close_button.setVisible(finished)
        self.show_button.setVisible(finished and bool(self.outputs()))
        self.open_button.setVisible(finished and bool(self._unconverted()))
        self.adjustSize()

    def _unconverted(self) -> list[str]:
        return [str(i.path) for i in self.items if not i.outputs and i.path.is_file()]

    def show_in_folder(self) -> None:
        made = self.outputs()
        if made:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(made[0]).parent)))

    def _open_in_app(self) -> None:
        files = self._unconverted()
        if files:
            self.open_in_app.emit(files)
        self.close()

    def _all_converted(self) -> bool:
        return bool(self.items) and all(i.outputs for i in self.items)

    def _auto_close(self) -> None:
        if self.done and not self._hovered:
            self.close()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._close_timer.stop()  # reading it: stays open
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        if self.done and self._all_converted():
            self._close_timer.start()
        super().leaveEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.busy:
            self.cancel()
            if self._thread is not None:
                self._thread.wait(10_000)
        super().closeEvent(event)
