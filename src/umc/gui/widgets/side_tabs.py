"""Two sideways tabs: "Convert to" (format cards) and "Settings" (the options form).

Only one is open at a time. While Settings is open, "Convert to" shrinks to a thin strip that
shows its icon and the chosen format; clicking the strip opens it again.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.gui import icons, theme

STRIP_W = 44


class CollapsedStrip(QAbstractButton):
    """A narrow vertical button: an icon on top and its label written downwards."""

    def __init__(self, icon_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Strip")
        self.icon_name = icon_name
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(STRIP_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(STRIP_W, 200)

    def paintEvent(self, _event) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        p.setPen(QColor(pal["border"]))
        p.setBrush(QColor(pal["surface_alt"] if hover else pal["surface"]))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
        color = pal["accent"] if hover else pal["muted"]
        p.drawPixmap((self.width() - 20) // 2, 14, icons.pixmap(self.icon_name, 20, color))
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(pal["text"] if hover else pal["muted"]))
        p.translate(self.width() / 2 + 5, 48)
        p.rotate(90)
        p.drawText(QRect(0, 0, self.height() - 60, 20), Qt.AlignmentFlag.AlignLeft, self.text())


def _pane(title: str, icon_name: str) -> tuple[QFrame, QHBoxLayout, QLabel, QLabel, QScrollArea]:
    pane = QFrame(objectName="Pane")
    lay = QVBoxLayout(pane)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    head = QHBoxLayout()
    head.setContentsMargins(18, 14, 12, 10)
    head.setSpacing(8)
    ic = QLabel(objectName="PaneIcon")
    ic.setProperty("icon_name", icon_name)
    label = QLabel(title, objectName="PaneTitle")
    head.addWidget(ic)
    head.addWidget(label)
    head.addStretch(1)
    lay.addLayout(head)
    scroll = QScrollArea(objectName="PaneScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    lay.addWidget(scroll, 1)
    return pane, head, ic, label, scroll


class SideTabs(QWidget):
    reset_clicked = Signal()
    settings_shown = Signal(bool)

    def __init__(self, formats: QWidget, settings: QWidget, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.strip = CollapsedStrip("transform")
        self.strip.setText("Convert to")
        self.strip.setToolTip("Back to the output formats")
        self.strip.setAccessibleName("Convert to")
        self.strip.clicked.connect(self.show_formats)
        lay.addWidget(self.strip)

        self.convert_pane, head, self._convert_icon, self.convert_title, scroll = _pane(
            "Convert to", "transform"
        )
        self.cog = QToolButton(objectName="IconButton")
        self.cog.setToolTip("Settings for this format")
        self.cog.setAccessibleName("Settings")
        self.cog.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cog.setIconSize(QSize(18, 18))
        self.cog.clicked.connect(self.show_settings)
        head.addWidget(self.cog)
        body = QWidget(objectName="PaneBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 0, 14, 14)
        bl.addWidget(formats)
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(self.convert_pane, 1)

        self.settings_pane, head, self._settings_icon, self.settings_title, scroll = _pane(
            "Settings", "settings"
        )
        reset = QPushButton("Reset", objectName="Link")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setToolTip("Reset every setting to its default")
        reset.clicked.connect(self.reset_clicked)
        head.addWidget(reset)
        body = QWidget(objectName="PaneBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(18, 0, 18, 18)
        bl.addWidget(settings)
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(self.settings_pane, 1)

        self._available = False
        self.show_formats()
        self.retint()

    @property
    def settings_open(self) -> bool:
        return not self.settings_pane.isHidden()

    def show_settings(self) -> None:
        if not self._available:
            return
        self.convert_pane.hide()
        self.strip.show()
        self.settings_pane.show()
        self.settings_shown.emit(True)

    def show_formats(self) -> None:
        was_open = self.settings_open
        self.settings_pane.hide()
        self.strip.hide()
        self.convert_pane.show()
        if was_open:
            self.settings_shown.emit(False)

    def set_target(self, label: str | None, settings_available: bool) -> None:
        self._available = bool(label) and settings_available
        self.cog.setVisible(self._available)
        self.strip.setText(label or "Convert to")
        self.strip.update()
        self.settings_title.setText(f"Settings · {label}" if label else "Settings")
        if not self._available and self.settings_open:
            self.show_formats()

    def retint(self) -> None:
        pal = theme.palette()
        self.cog.setIcon(icons.icon("settings", pal["muted"], 18))
        self._convert_icon.setPixmap(icons.pixmap("transform", 18, pal["accent"]))
        self._settings_icon.setPixmap(icons.pixmap("settings", 18, pal["accent"]))
        self.strip.update()
