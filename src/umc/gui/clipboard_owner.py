"""Our own clipboard data, and handing it over safely when the app closes.

The image editor puts a QMimeData made in Python on the clipboard (the picture plus where it
came from, in ``CLIP_MIME``). When the app quits, Qt hands clipboard data over to the system
(so it can still be pasted afterwards) — by then Python has shut down, and a Python-made
QMimeData crashes the process. ``release_clipboard`` (on ``aboutToQuit``, and at the end of
the test run) swaps it for a plain picture that Qt owns."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QImage

CLIP_MIME = "application/x-umc-editor-clip"


def release_clipboard() -> None:
    if QGuiApplication.instance() is None:
        return
    board = QGuiApplication.clipboard()
    data = board.mimeData()
    if data is None or not data.hasFormat(CLIP_MIME):
        return
    image = QImage(data.imageData()) if data.hasImage() else QImage()
    if image.isNull():
        board.clear()
    else:
        board.setImage(image)  # Qt's own data: survives the app, pasteable elsewhere
