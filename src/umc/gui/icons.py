"""Tinted line icons (Tabler subset in icon_data.py), rendered with QtSvg."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from umc.core.detect import MediaFamily
from umc.gui.icon_data import ICONS

FAMILY_ICON = {
    MediaFamily.IMAGE: "photo",
    MediaFamily.VIDEO: "movie",
    MediaFamily.AUDIO: "music",
    MediaFamily.MODEL: "cube",
    MediaFamily.DOCUMENT: "document",
    MediaFamily.ARCHIVE: "archive",
    MediaFamily.SUBTITLE: "subtitle",
    MediaFamily.FONT: "font",
    MediaFamily.VECTOR: "vector",
    MediaFamily.CAD: "cad",
    MediaFamily.PROJECT: "slideshow",
}

# First matching keyword (lower case) in a target group name → icon.
_GROUP_ICON = (
    ("slideshow", "slideshow"),
    ("frame", "files"),
    ("one per page", "files"),
    ("animation", "gif"),
    ("ocr", "scan"),
    ("pdf", "pdf"),
    ("split", "scissors"),
    ("trim", "scissors"),
    ("image", "photo"),
    ("video", "movie"),
    ("audio", "music"),
    ("document", "document"),
    ("data", "table"),
    ("3d", "cube"),
    ("archive", "archive"),
    ("subtitle", "subtitle"),
    ("font", "font"),
    ("vector", "vector"),
    ("cad", "cad"),
)


def group_icon(group: str) -> str:
    g = group.lower()
    return next((icon for key, icon in _GROUP_ICON if key in g), "file")


def svg(name: str, color: str, stroke: float = 1.75) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{ICONS[name]}</svg>'
    ).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, size: int, color: str, dpr: float = 0.0) -> QPixmap:
    """``size`` is in device-independent pixels; ``dpr`` 0 = the primary screen's ratio."""
    if not dpr:
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 20, selected_color: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
    if selected_color:
        sel = pixmap(name, size, selected_color)
        ic.addPixmap(sel, QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(sel, QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(sel, QIcon.Mode.Selected, QIcon.State.On)
    return ic
