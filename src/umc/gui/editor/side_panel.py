"""Colour swatches and tool settings (right side). The tool rail is ``tool_rail.py``."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import options as opts
from umc.editor.tools.base import Tool
from umc.gui.widgets.settings_panel import SettingsPanel

# ------------------------------------------------------------------ colours


def _rgba(c: QColor) -> tuple[int, int, int, int]:
    return (c.red(), c.green(), c.blue(), c.alpha())


class _Swatch(QToolButton):
    def __init__(self, tip: str) -> None:
        super().__init__(objectName="Swatch")
        self.setFixedSize(34, 34)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color = QColor("black")

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        r = self.rect().adjusted(2, 2, -2, -2)
        for i in range(0, r.width(), 6):  # checkerboard shows transparency
            for j in range(0, r.height(), 6):
                if (i // 6 + j // 6) % 2:
                    p.fillRect(r.x() + i, r.y() + j, 6, 6, QColor("#cccccc"))
                else:
                    p.fillRect(r.x() + i, r.y() + j, 6, 6, QColor("white"))
        p.fillRect(r, self.color)
        p.setPen(QPen(QColor(128, 128, 128), 1))
        p.drawRoundedRect(r, 4, 4)
        p.end()


class ColorSwatches(QWidget):
    colors_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._primary = _Swatch("Primary colour — left mouse button")
        self._secondary = _Swatch("Secondary colour — right mouse button, shape fill")
        self._primary.color = QColor("black")
        self._secondary.color = QColor("white")
        swap = QToolButton(text="Swap", objectName="Link")
        swap.setToolTip("Swap colours (X)")
        swap.clicked.connect(self.swap)
        self._primary.clicked.connect(lambda: self._pick(self._primary))
        self._secondary.clicked.connect(lambda: self._pick(self._secondary))
        lay.addWidget(self._primary)
        lay.addWidget(self._secondary)
        lay.addWidget(swap)
        lay.addStretch(1)

    @property
    def primary(self) -> tuple[int, int, int, int]:
        return _rgba(self._primary.color)

    @property
    def secondary(self) -> tuple[int, int, int, int]:
        return _rgba(self._secondary.color)

    def set_color(self, rgba: tuple, which: str = "primary") -> None:
        sw = self._secondary if which in ("secondary", "right") else self._primary
        sw.color = QColor(*rgba)
        sw.update()
        self.colors_changed.emit()

    def swap(self) -> None:
        a, b = self._primary.color, self._secondary.color
        self._primary.color, self._secondary.color = b, a
        self._primary.update()
        self._secondary.update()
        self.colors_changed.emit()

    def _pick(self, sw: _Swatch) -> None:
        c = QColorDialog.getColor(
            sw.color, self, "Choose colour", QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if c.isValid():
            sw.color = c
            sw.update()
            self.colors_changed.emit()


# ------------------------------------------------------------------ tool settings


class ToolSettings(QWidget):
    """The active tool's options (auto form) — values remembered per tool for the session."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.title = QLabel(objectName="Section")
        lay.addWidget(self.title)
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont("DejaVu Sans"))
        self.font_box.currentFontChanged.connect(lambda _f: self.changed.emit())
        lay.addWidget(self.font_box)
        self.panel = SettingsPanel()
        self.panel.changed.connect(self._remember)
        lay.addWidget(self.panel)
        self._values: dict[str, dict[str, Any]] = {}
        self._tool: Tool | None = None

    def show_tool(self, tool: Tool) -> None:
        self._tool = tool
        self.title.setText(tool.label.upper())
        self.font_box.setVisible(tool.id == "text")
        self.panel.set_schema(tool.options_cls, tool.id, self._values.get(tool.id, {}))
        self.panel.setVisible(tool.options_cls is not None)

    def _remember(self) -> None:
        if self._tool is not None:
            self._values[self._tool.id] = self.panel.values()
        self.changed.emit()

    def options_for(self, tool: Tool) -> Any:
        if tool.options_cls is None:
            return None
        values = {**opts.defaults(tool.options_cls), **self._values.get(tool.id, {})}
        return opts.build(tool.options_cls, values)

    def set_values(self, tool: Tool, values: dict) -> None:
        """Set some of ``tool``'s settings (e.g. from a text layer being edited)."""
        self._values.setdefault(tool.id, {}).update(values)
        if tool is self._tool:
            self.show_tool(tool)
        self.changed.emit()

    def font_family(self) -> str:
        return self.font_box.currentFont().family()

    def change_size(self, tool: Tool, factor: float) -> None:
        """[ / ] — grow or shrink the brush-like tool's size."""
        cls = tool.options_cls
        if cls is None or "size" not in {s.name for s in opts.option_specs(cls)}:
            return
        spec = next(s for s in opts.option_specs(cls) if s.name == "size")
        cur = self.options_for(tool).size
        new = cur * factor
        new = math.ceil(new) if factor > 1 else math.floor(new)
        if new == cur:
            new = cur + (1 if factor > 1 else -1)
        new = int(max(spec.min or 1, min(spec.max or 500, new)))
        self._values.setdefault(tool.id, {})["size"] = new
        if tool is self._tool:
            self.show_tool(tool)
        self.changed.emit()
