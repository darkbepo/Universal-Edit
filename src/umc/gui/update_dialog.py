"""The Updates window (Settings → "Check for updates…", or the "Update available" button in
the top bar): what's new for the app, its editors and its tools, and one "Update everything"
button. The work is done by ``tools/updater.py`` on a background thread.

``background_check`` looks once a day (installed builds only) and tells the converter windows,
which then show the "Update available" button."""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc import __version__
from umc.core import addons
from umc.core.errors import ConversionCancelled, ConversionError
from umc.tools import updater

log = logging.getLogger(__name__)

LAST_CHECK = "updates/last_check"  # QSettings: when the daily check last ran (time.time())
DAY = 24 * 3600


class _Job(QObject):
    """Runs ``fn(progress, cancel)`` on a thread; results reach the GUI thread by signals."""

    progress = Signal(float, str)
    finished = Signal(object, object)  # result, error (plain-language str) — one is None

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="umc-update", daemon=True).start()

    def _run(self) -> None:
        try:
            result = self.fn(self.progress.emit, self.cancel)
        except ConversionCancelled:
            self.finished.emit(None, "Cancelled.")
        except ConversionError as exc:
            log.error("update: %s\n%s", exc, getattr(exc, "details", ""))
            self.finished.emit(None, str(exc))
        except Exception:
            log.exception("update failed")
            self.finished.emit(None, "Something went wrong. Details were written to the log file.")
        else:
            self.finished.emit(result, None)


def describe(report: updater.Report) -> list[str]:
    """The window's lines for a report, in plain words."""
    lines = []
    if report.app is not None:
        lines.append(f"Universal Edit {__version__} → {report.app.version}")
    for addon_id in report.addons:
        if report.app is None:  # with a new app version they're renewed after the restart
            lines.append(f"{addons.ADDONS[addon_id].title}: made for an older version")
    for t in report.tools:
        lines.append(f"{t.tool} {t.installed} → {t.latest}")
    if report.linux_packages:
        lines.append("From your system: " + ", ".join(report.linux_packages))
    return lines


def apply(
    report: updater.Report, progress, cancel: threading.Event
) -> tuple[list[str], str | None, Path | None]:
    """Everything the report lists. Returns (problems, "restart" / "installer" / None,
    the downloaded Windows installer)."""
    problems: list[str] = []
    if sys.platform == "win32":
        if report.tools:
            problems += updater.update_tools(
                report.tools, lambda f, m: progress(0.5 * f, m), cancel
            )
        if report.app is None and report.addons:
            problems += updater.update_addons(
                report.addons, lambda f, m: progress(0.5 + 0.5 * f, m), cancel
            )
        if report.app is not None:
            setup = updater.download_app(
                report.app, lambda f, m: progress(0.5 + 0.5 * f, m), cancel
            )
            return problems, "installer", setup
        return problems, None, None
    if sys.platform.startswith("linux"):
        deb = None
        if report.app is not None and not updater.has_apt_source():
            deb = updater.download_app(report.app, lambda f, m: progress(0.6 * f, m), cancel)
        packages = updater.linux_packages_for(report)
        if deb is not None or packages:
            progress(0.7, "Installing — your password may be asked for…")
            updater.run_linux_update(deb, packages, cancel)
        return problems, "restart" if report.app is not None else None, None
    return problems, None, None


class UpdateDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, report: updater.Report | None = None):
        super().__init__(parent)
        self.setWindowTitle("Updates")
        self.setMinimumWidth(480)
        self.report: updater.Report | None = None
        self.job: _Job | None = None
        self.title = QLabel(objectName="CardTitle")
        self.lines = QLabel(objectName="Muted")
        self.lines.setWordWrap(True)
        self.lines.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = QLabel(objectName="Help")
        self.status.setWordWrap(True)
        self.update_button = QPushButton("Update everything", objectName="Primary")
        self.update_button.clicked.connect(self.update_everything)
        self.restart_button = QPushButton("Restart now", objectName="Primary")
        self.restart_button.clicked.connect(self.restart)
        self.check_button = QPushButton("Check again", objectName="Secondary")
        self.check_button.clicked.connect(self.check)
        self.close_button = QPushButton("Close", objectName="Secondary")
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.check_button, self.close_button, self.restart_button, self.update_button):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(self.title)
        lay.addWidget(self.lines)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addLayout(buttons)
        if report is not None:
            self._checked(report, None)
        else:
            self.check()

    # ------------------------------------------------------------ checking
    def _run(self, fn, done) -> None:
        self.job = _Job(fn, self)
        self.job.progress.connect(self._on_progress)
        self.job.finished.connect(done)
        self.job.start()

    def check(self) -> None:
        self.title.setText("Checking for updates…")
        self.lines.setText("")
        self.status.setText("")
        self._buttons(busy=True)
        self._run(lambda _p, _c: updater.check_all(), self._checked)

    def _checked(self, report: updater.Report | None, error: str | None) -> None:
        self.job = None
        self.report = report
        if report is None:
            self.title.setText("Couldn't check for updates")
            self.status.setText(error or "")
        elif report.anything:
            self.title.setText("Updates are ready")
            self.lines.setText("\n".join(describe(report)))
            self.status.setText("\n".join(report.problems))
        else:
            self.title.setText("Everything is up to date")
            self.lines.setText(f"Universal Edit {__version__}")
            self.status.setText("\n".join(report.problems))
        _remember_check()
        self._buttons()

    # ------------------------------------------------------------ updating
    def update_everything(self) -> None:
        report = self.report
        if report is None or not report.anything:
            return
        self.title.setText("Updating…")
        self.bar.setValue(0)
        self.bar.show()
        self._buttons(busy=True)
        self._run(lambda p, c: apply(report, p, c), self._updated)

    def _on_progress(self, fraction: float, message: str) -> None:
        self.bar.setValue(round(1000 * max(0.0, min(1.0, fraction))))
        if message:
            self.status.setText(message)

    def _updated(self, result, error: str | None) -> None:
        self.job = None
        self.bar.hide()
        if result is None:
            self.title.setText("The update didn't finish")
            self.status.setText(error or "")
            self._buttons()
            return
        problems, then, setup = result
        if then == "installer" and setup is not None:
            self.title.setText("Installing the new version…")
            self.status.setText("Universal Edit closes now and opens again when it's done.")
            QApplication.processEvents()
            updater.start_windows_update(setup)
            QApplication.quit()
            return
        if then == "restart":
            self.title.setText("Updated — restart to use the new version")
        else:
            self.title.setText("Updated")
        self.lines.setText("")
        self.status.setText("\n".join(problems))
        self.report = None
        self._buttons(restart=then == "restart")

    def restart(self) -> None:
        updater.restart_app()
        QApplication.quit()

    def _buttons(self, busy: bool = False, restart: bool = False) -> None:
        ready = not busy and self.report is not None and self.report.anything
        self.update_button.setVisible(ready)
        self.restart_button.setVisible(restart and not busy)
        self.check_button.setVisible(not busy and not ready and not restart)
        self.close_button.setEnabled(not busy)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.job is not None:
            self.job.cancel.set()
        super().closeEvent(event)


# --------------------------------------------------------------------------- daily check
def _remember_check() -> None:
    QSettings().setValue(LAST_CHECK, time.time())


def due(now: float | None = None) -> bool:
    """A day has passed since the last check (installed builds only: a checkout has no
    release to update to)."""
    if not getattr(sys, "frozen", False):
        return False
    last = QSettings().value(LAST_CHECK, 0.0, float) or 0.0
    return (now if now is not None else time.time()) - last >= DAY


class BackgroundCheck(QObject):
    """Checks quietly; ``found`` carries a report that has something to update."""

    found = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.report: updater.Report | None = None
        self._job: _Job | None = None

    def start(self) -> None:
        if self._job is None and due():
            self._job = _Job(lambda _p, _c: updater.check_all(), self)
            self._job.finished.connect(self._done)
            self._job.start()

    def _done(self, report, _error) -> None:
        self._job = None
        if report is None:
            return
        _remember_check()
        if report.anything:
            self.report = report
            self.found.emit(report)
