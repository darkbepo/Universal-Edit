"""Ask to install an editor add-on the first time it's needed, then download it with a
progress window (the download runs on a plain thread; the window polls it)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from umc.core import addons
from umc.core.errors import ConversionCancelled, ConversionError


def _size_text(n: int) -> str:
    return f" ({n / 1e6:.0f} MB)" if n >= 1e6 else ""


def install_with_progress(parent: QWidget | None, addon_id: str) -> str:
    """Download + install; returns "" on success, else a plain-language reason."""
    title = addons.ADDONS[addon_id].title
    progress = QProgressDialog(f"Downloading the {title.lower()}…", "Cancel", 0, 1000, parent)
    progress.setWindowTitle("Installing")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    cancel = threading.Event()
    state = {"fraction": 0.0, "error": None, "done": False}

    def work() -> None:
        try:
            addons.download(addon_id, lambda f: state.__setitem__("fraction", f), cancel)
        except ConversionCancelled:
            state["error"] = "cancelled"
        except ConversionError as exc:
            state["error"] = str(exc)
        except Exception as exc:  # never leave the window hanging
            state["error"] = f"The {title.lower()} couldn't be installed ({exc})."
        finally:
            state["done"] = True

    loop = QEventLoop()
    timer = QTimer(interval=100)

    def tick() -> None:
        progress.setValue(int(state["fraction"] * 1000))
        if progress.wasCanceled():
            cancel.set()
        if state["done"]:
            timer.stop()
            loop.quit()

    timer.timeout.connect(tick)
    thread = threading.Thread(target=work, name=f"umc-addon-{addon_id}", daemon=True)
    thread.start()
    timer.start()
    loop.exec()
    thread.join(5)
    progress.close()
    if state["error"] == "cancelled":
        return "cancelled"
    if state["error"] is None:
        addons.activate(addon_id)  # on this (the UI) thread too, for good measure
    return state["error"] or ""


def refresh_all_windows() -> None:
    """Every open converter window's editor tiles (another window may have installed one)."""
    from PySide6.QtWidgets import QApplication

    for w in QApplication.topLevelWidgets():
        hub = getattr(w, "hub", None)
        if hub is not None and hasattr(hub, "refresh_install_state"):
            hub.refresh_install_state()


def ensure_installed(parent: QWidget | None, editor_id: str) -> bool:
    """True if the editor can open now — asking to install it first when it's an add-on that
    isn't installed yet."""
    if addons.ready(editor_id):
        return True
    title = addons.ADDONS[editor_id].title
    size = _size_text(addons.download_size(editor_id))
    answer = QMessageBox.question(
        parent,
        f"Install the {title.lower()}?",
        f"The {title.lower()} isn't installed yet. Download and install it now{size}?\n\n"
        "You can remove it again in Settings.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    error = install_with_progress(parent, editor_id)
    if error and error != "cancelled":
        QMessageBox.warning(parent, "Not installed", error)
    return not error
