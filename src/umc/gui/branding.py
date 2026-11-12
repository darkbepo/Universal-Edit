"""The app's name, logo and window icon, and moving settings saved under the old name."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

APP_NAME = "Universal Edit"
ORG_NAME = "UniversalEdit"
# QSettings names used before the rename (Windows: HKCU\Software\UniversalMediaConverter\…)
OLD_ORG_NAME = "UniversalMediaConverter"
OLD_APP_NAME = "Universal Media Converter"
# Lets Windows group the taskbar button under our icon instead of python.exe / a generic one.
WINDOWS_APP_ID = "darkbepo.UniversalEdit"

LOGO_SVG = Path(__file__).with_name("logo.svg")
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

log = logging.getLogger(__name__)


def logo_image(size: int, ratio: float = 1.0) -> QImage:
    """The logo drawn at ``size`` logical px (sharp on high-DPI screens via ``ratio``)."""
    px = max(1, round(size * ratio))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(LOGO_SVG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    img.setDevicePixelRatio(ratio)
    return img


def logo_pixmap(size: int, ratio: float = 1.0) -> QPixmap:
    return QPixmap.fromImage(logo_image(size, ratio))


def app_icon() -> QIcon:
    """Window / taskbar icon, every size drawn from the SVG."""
    icon = QIcon()
    for s in ICON_SIZES:
        icon.addPixmap(logo_pixmap(s), QIcon.Mode.Normal)
    return icon


def set_windows_app_id() -> None:
    """Without this Windows shows the icon of the process that started us (python.exe when run
    from source) on the taskbar. Must run before the first window is created."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:  # never stop the app from starting over an icon
        log.exception("could not set the Windows app id")


def migrate_settings() -> int:
    """Copy settings saved under the app's old name once (window size, folds, last format, …).
    Needs the application/organisation names set. Returns how many keys were copied."""
    new = QSettings()
    if new.allKeys():
        return 0
    old = QSettings(OLD_ORG_NAME, OLD_APP_NAME)
    keys = old.allKeys()
    for key in keys:
        new.setValue(key, old.value(key))
    new.sync()
    if keys:
        log.info("copied %d settings saved under the old app name", len(keys))
    return len(keys)
