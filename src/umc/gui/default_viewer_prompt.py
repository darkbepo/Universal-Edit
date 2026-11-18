"""Installed Linux app, first start: ask once whether files should open in Universal Edit.

Yes = ``linux_integration.set_default_viewer`` (the viewer becomes the default for every type
it shows; the user's earlier choices are remembered, and the Settings switch or removing the
package puts them back). Asked only once, and not when the viewer already is the default."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

log = logging.getLogger(__name__)

ASKED = "linux/asked_default_viewer"
QUESTION = "Open your files with Universal Edit?"
DETAILS = (
    "Pictures, videos, music, PDFs, documents, text and 3D models will open in Universal "
    "Edit's viewer when you double-click them.\n\nYour current choices are remembered: "
    "switching this off in Settings (or removing Universal Edit) gives them back."
)


def wanted(settings: QSettings, frozen: bool | None = None, platform: str | None = None) -> bool:
    """Whether to ask now: the installed Linux app, never asked before."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    return frozen and platform.startswith("linux") and not settings.value(ASKED, False, bool)


def _ask(parent: QWidget | None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Universal Edit")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(QUESTION)
    box.setInformativeText(DETAILS)
    yes = box.addButton("Yes, open them here", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def offer(
    settings: QSettings,
    parent: QWidget | None = None,
    ask: Callable[[QWidget | None], bool] = _ask,
    home=None,
) -> bool:
    """Ask (once) and make the viewer the default on Yes. True when it was made the default."""
    from umc.tools import linux_integration as li

    settings.setValue(ASKED, True)  # once, whatever the answer
    try:
        if li.is_default_viewer(home):
            return False
        if not ask(parent):
            return False
        li.set_default_viewer(home=home)
        return True
    except OSError:
        log.exception("making the viewer the default failed")
        return False


def schedule(delay_ms: int = 600) -> None:
    """From ``app.main``: ask a moment after the first window is up (when wanted)."""
    settings = QSettings()
    if not wanted(settings):
        return

    def run() -> None:
        offer(QSettings(), QApplication.activeWindow())

    QTimer.singleShot(delay_ms, run)
