"""Tinted line icons (Tabler subset in icon_data.py), rendered with QtSvg."""

from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QListWidget, QWidget

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
    MediaFamily.CODE: "code",
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
    _made_pixmaps[pm.cacheKey()] = (name, size, color, dpr)
    return pm


# What every icon / pixmap made here was made from (keyed by Qt's cacheKey, which copies
# share), so retint() can redraw them in the other theme's colours.
_made_pixmaps: dict[int, tuple[str, int, str, float]] = {}
_made_icons: dict[int, tuple[str, str, int, str | None]] = {}


def icon(name: str, color: str, size: int = 20, selected_color: str | None = None) -> QIcon:
    ic = _icon(name, color, size, selected_color)
    _made_icons[ic.cacheKey()] = (name, color, size, selected_color)
    return ic


def _icon(name: str, color: str, size: int, selected_color: str | None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
    if selected_color:
        sel = pixmap(name, size, selected_color)
        ic.addPixmap(sel, QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(sel, QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(sel, QIcon.Mode.Selected, QIcon.State.On)
    return ic


_HEX = re.compile(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])")


def retint(swap: dict[str, str]) -> None:
    """After a light ↔ dark switch: redraw, in every open window, the icons made here and the
    widget stylesheets that contain palette colours. ``swap`` maps each old palette colour to
    the new one (lower-case "#rrggbb"); colours not in it (pictures, chosen colours) stay."""

    def new(color: str | None) -> str | None:
        return swap.get(color.lower(), color) if color else color

    for w in QApplication.allWidgets():
        if isinstance(w, QAbstractButton):
            # base-class calls: some widgets keep a child named ``icon`` / ``pixmap``
            made = _made_icons.get(QAbstractButton.icon(w).cacheKey())
            if made:
                name, color, size, sel = made
                if new(color) != color or new(sel) != sel:
                    QAbstractButton.setIcon(w, icon(name, new(color), size, new(sel)))
        elif isinstance(w, QLabel):
            pm = QLabel.pixmap(w)
            usable = pm is not None and not pm.isNull()
            made_pm = _made_pixmaps.get(pm.cacheKey()) if usable else None
            if made_pm:
                name, size, color, dpr = made_pm
                if new(color) != color:
                    QLabel.setPixmap(w, pixmap(name, size, new(color), dpr))
        elif isinstance(w, QListWidget):
            for i in range(QListWidget.count(w)):
                item = QListWidget.item(w, i)
                made = _made_icons.get(item.icon().cacheKey())
                if made and new(made[1]) != made[1]:
                    name, color, size, sel = made
                    item.setIcon(icon(name, new(color), size, new(sel)))
        sheet = QWidget.styleSheet(w)
        if sheet and "#" in sheet:  # one pass, so a swapped colour is never swapped again
            changed = _HEX.sub(lambda m: swap.get(m.group(0).lower(), m.group(0)), sheet)
            if changed != sheet:
                QWidget.setStyleSheet(w, changed)
        QWidget.update(w)
