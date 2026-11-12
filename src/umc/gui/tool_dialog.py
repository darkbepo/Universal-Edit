"""“LibreOffice/Pandoc/Ghostscript is needed” dialog.

On Windows the main button is **Install it for me**: it downloads, verifies and installs the
tool in the background (see :mod:`umc.tools.installer`) and then closes, so the caller can retry
the conversion automatically. “Download it myself” and “I've installed it — check again”
remain as fallbacks.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from umc.core.errors import ConversionCancelled, ConversionError
from umc.tools import calibre, ffmpeg, ghostscript, installer, libredwg, libreoffice, pandoc

log = logging.getLogger(__name__)

_CHECKS = {
    "LibreOffice": libreoffice.is_available,
    "Pandoc": pandoc.is_available,
    "Ghostscript": ghostscript.is_available,
    "Calibre": calibre.is_available,
    "LibreDWG": libredwg.is_available,
    "FFmpeg": ffmpeg.is_available,
    "FFmpeg (ffprobe)": ffmpeg.is_available,
}

_WHY = {
    "LibreOffice": "LibreOffice is a free office suite. Universal Edit uses it in the "
    "background to convert Word, OpenDocument, RTF, spreadsheet and presentation files.",
    "Pandoc": "Pandoc is a free document converter used for Markdown, HTML, EPUB and similar "
    "formats.",
    "Ghostscript": "Ghostscript is a free PostScript interpreter. Universal Edit uses "
    "it to open and write EPS and PostScript (.ps) files.",
    "Calibre": "Calibre is a free e-book manager. Universal Edit uses its converter "
    "to make Kindle e-books (MOBI and AZW3).",
    "LibreDWG": "LibreDWG is a free library for AutoCAD drawings. Universal Edit uses "
    "it to open DWG files.",
    "FFmpeg": "FFmpeg is a free video toolkit used for every video and audio conversion.",
}

_SIZE = {
    "LibreOffice": "about a 350 MB download, roughly 1 GB on disk, and a few minutes",
    "Pandoc": "about a 40 MB download",
    "Ghostscript": "about a 50 MB download",
    "Calibre": "about a 200 MB download, roughly 600 MB on disk",
    "LibreDWG": "about a 10 MB download",
}


class _InstallWorker(QObject):
    progress = Signal(float, str)
    succeeded = Signal()
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()

    def __init__(self, tool: str) -> None:
        super().__init__()
        self.tool = tool
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            installer.INSTALLERS[self.tool](
                lambda f, m: self.progress.emit(f, m), self.cancel_event
            )
            self.succeeded.emit()
        except ConversionCancelled:
            self.cancelled.emit()
        except ConversionError as exc:
            log.error("installing %s failed: %s\n%s", self.tool, exc, exc.details)
            self.failed.emit(str(exc))
        except Exception:
            log.exception("installing %s failed", self.tool)
            self.failed.emit(f"Installing {self.tool} failed. Details are in the log file.")
        finally:
            self.done.emit()


class ToolMissingDialog(QDialog):
    """``exec()`` returns Accepted once the tool is available (so the caller can retry)."""

    def __init__(self, tool: str, url: str, message: str, parent=None) -> None:
        super().__init__(parent)
        self.tool = tool
        self.url = url
        self._thread: QThread | None = None
        self._worker: _InstallWorker | None = None
        short = tool.split(" ")[0]
        can_install = installer.supported(tool)

        self.setWindowTitle(f"{short} needed")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        title = QLabel(f"{short} is required")
        title.setObjectName("Title")
        if can_install:
            hint = (
                "“Install it for me” downloads it from the official website, checks it and sets "
                f"it up — {_SIZE.get(tool, '')}. Your conversion continues automatically."
            )
        else:
            hint = "Install it, then press “I've installed it” — no restart needed."
        body = QLabel(f"{_WHY.get(short, message)}\n\n{hint}")
        body.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.install_button = QPushButton("Install it for me")
        self.install_button.setObjectName("Primary")
        self.manual_button = QPushButton(f"Download {short} myself")
        self.check_button = QPushButton("I've installed it — check again")
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.install_button)
        lay.addSpacing(4)
        lay.addLayout(buttons)

        # Manual fallbacks as quiet links under the main buttons.
        links = QHBoxLayout()
        links.setSpacing(4)
        for b in (self.manual_button, self.check_button):
            b.setObjectName("Link")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        dot = QLabel("·")
        dot.setObjectName("Muted")
        links.addStretch(1)
        links.addWidget(self.manual_button)
        links.addWidget(dot)
        links.addWidget(self.check_button)
        lay.addLayout(links)

        if not can_install:
            # Without the automatic option, downloading manually becomes the main action.
            self.install_button.hide()
            self.manual_button.setObjectName("Primary")
            self.manual_button.setText(f"Download {short}")
            links.removeWidget(self.manual_button)
            dot.hide()
            buttons.addWidget(self.manual_button)
        # Word-wrapped labels need an explicit width to get their height right.
        self.setFixedWidth(540)
        self._fit()

        self.cancel_button.clicked.connect(self._cancel)
        self.manual_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        self.check_button.clicked.connect(self._check)
        self.install_button.clicked.connect(self._install)

    # ----------------------------------------------------------------- manual path
    def _check(self) -> None:
        check = _CHECKS.get(self.tool)
        if check is not None and check():
            self.accept()
        else:
            self._set_status(
                f"{self.tool} still wasn't found. If you just installed it, make sure the "
                "installation finished.",
                error=True,
            )

    # ----------------------------------------------------------------- automatic path
    def _install(self) -> None:
        self._set_busy(True)
        self._set_status("Starting…")
        self._thread = QThread(self)
        self._worker = _InstallWorker(self.tool)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(lambda: self._set_status("Installation cancelled."))
        self._worker.done.connect(self._thread.quit)
        # Keep Python references until the thread has really finished (see CLAUDE.md).
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.bar.setValue(round(fraction * 1000))
        self._set_status(message)

    def _on_success(self) -> None:
        self.bar.setValue(1000)
        self._set_status(f"{self.tool} is ready.")

    def _on_failed(self, message: str) -> None:
        self._set_status(f"{message}\nYou can try again or download it yourself.", error=True)

    def _on_thread_finished(self) -> None:
        ok = _CHECKS.get(self.tool, lambda: False)()
        self._thread = None
        self._worker = None
        self._set_busy(False)
        if ok and "ready" in self.status.text():
            self.accept()

    def _set_busy(self, busy: bool) -> None:
        self.bar.setVisible(busy or self.bar.value() > 0)
        for b in (self.install_button, self.manual_button, self.check_button):
            b.setEnabled(not busy)
        self.cancel_button.setText("Stop" if busy else "Cancel")
        self._fit()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
            self._set_status("Stopping…")
        else:
            self.reject()

    def reject(self) -> None:  # Esc / window close while installing → stop first
        if self._worker is not None:
            self._worker.cancel_event.set()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(10000)
        super().reject()

    def _fit(self) -> None:
        """Grow/shrink to the content (progress bar and status lines come and go)."""
        height = self.layout().totalHeightForWidth(self.width())
        self.setMinimumHeight(height)
        self.resize(self.width(), height)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setObjectName("Error" if error else "Muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self._fit()
