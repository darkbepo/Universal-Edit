"""Tool bar (left), colour swatches and tool settings (right)."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import options as opts
from umc.editor.tools import TOOLS
from umc.editor.tools.base import Tool
from umc.gui.widgets.settings_panel import SettingsPanel

# ------------------------------------------------------------------ icons


def tool_icon(tool_id: str, color: str) -> QIcon:
    """Simple line icons drawn with QPainter (no image files to bundle)."""
    size = 48
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 3.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = QColor(color)
    if tool_id == "move":
        p.drawLine(QPointF(24, 8), QPointF(24, 40))
        p.drawLine(QPointF(8, 24), QPointF(40, 24))
        for tip, a, b in (
            ((24, 6), (19, 12), (29, 12)),
            ((24, 42), (19, 36), (29, 36)),
            ((6, 24), (12, 19), (12, 29)),
            ((42, 24), (36, 19), (36, 29)),
        ):
            p.drawPolyline([QPointF(*a), QPointF(*tip), QPointF(*b)])
    elif tool_id in ("ellipse_select", "lasso", "polygon_lasso"):
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(2.6)
        p.setPen(pen)
        if tool_id == "ellipse_select":
            p.drawEllipse(QRectF(8, 12, 32, 24))
        elif tool_id == "lasso":
            path = QPainterPath(QPointF(14, 34))
            path.cubicTo(QPointF(2, 26), QPointF(10, 8), QPointF(28, 10))
            path.cubicTo(QPointF(46, 12), QPointF(42, 32), QPointF(24, 32))
            p.drawPath(path)
        else:
            p.drawPolygon(
                [QPointF(10, 14), QPointF(28, 8), QPointF(40, 24), QPointF(26, 40), QPointF(10, 32)]
            )
    elif tool_id == "wand":
        p.drawLine(QPointF(8, 40), QPointF(30, 18))
        p.drawLine(QPointF(34, 6), QPointF(34, 14))
        p.drawLine(QPointF(30, 10), QPointF(38, 10))
    elif tool_id == "mask_brush":
        p.drawRoundedRect(QRectF(8, 8, 32, 32), 6, 6)
        p.setBrush(QColor(color))
        p.drawPie(QRectF(14, 14, 20, 20), 90 * 16, 180 * 16)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif tool_id == "gradient":
        p.drawRoundedRect(QRectF(8, 10, 32, 28), 4, 4)
        for i, x in enumerate((14, 19, 24, 29, 34)):
            c = QColor(color)
            c.setAlphaF(1 - i * 0.2)
            pen2 = QPen(c, 3)
            p.setPen(pen2)
            p.drawLine(QPointF(x, 14), QPointF(x, 34))
        p.setPen(pen)
    elif tool_id == "brush":
        p.drawLine(QPointF(34, 10), QPointF(20, 26))
        path = QPainterPath(QPointF(20, 26))
        path.cubicTo(QPointF(12, 26), QPointF(14, 36), QPointF(8, 38))
        path.cubicTo(QPointF(18, 40), QPointF(26, 36), QPointF(24, 30))
        p.setBrush(c)
        p.drawPath(path)
    elif tool_id == "eraser":
        p.translate(24, 24)
        p.rotate(-40)
        p.drawRoundedRect(QRectF(-14, -8, 28, 16), 3, 3)
        p.drawLine(QPointF(-2, -8), QPointF(-2, 8))
    elif tool_id == "fill":
        p.translate(22, 22)
        p.rotate(45)
        p.drawRoundedRect(QRectF(-9, -9, 18, 18), 2, 2)
        p.resetTransform()
        p.setBrush(c)
        p.drawEllipse(QPointF(38, 34), 3.5, 4.5)
    elif tool_id == "picker":
        p.drawLine(QPointF(12, 36), QPointF(30, 18))
        p.setBrush(c)
        p.drawEllipse(QPointF(33, 15), 5, 5)
        p.drawLine(QPointF(12, 36), QPointF(10, 38))
    elif tool_id == "line":
        p.drawLine(QPointF(10, 38), QPointF(38, 10))
    elif tool_id == "rect":
        p.drawRoundedRect(QRectF(10, 13, 28, 22), 2, 2)
    elif tool_id == "ellipse":
        p.drawEllipse(QRectF(9, 13, 30, 22))
    elif tool_id == "text":
        f = QFont()
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "T")
    elif tool_id == "select":
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(2.6)
        p.setPen(pen)
        p.drawRect(QRectF(10, 12, 28, 24))
    elif tool_id == "crop":
        p.drawPolyline([QPointF(16, 6), QPointF(16, 32), QPointF(42, 32)])
        p.drawPolyline([QPointF(6, 16), QPointF(32, 16), QPointF(32, 42)])
    else:
        p.drawEllipse(QPointF(24, 24), 10, 10)
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------ tool bar


class ToolBar(QWidget):
    tool_selected = Signal(str)

    def __init__(self, icon_color: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorToolBar")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        for cls in TOOLS:
            b = QToolButton(objectName="ToolButton")
            b.setCheckable(True)
            b.setIcon(tool_icon(cls.id, icon_color))
            b.setIconSize(QSize(26, 26))
            b.setFixedSize(40, 40)
            b.setToolTip(f"{cls.label} ({cls.shortcut})")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, tid=cls.id: self.tool_selected.emit(tid))
            self._group.addButton(b)
            self._buttons[cls.id] = b
            lay.addWidget(b)
        lay.addStretch(1)

    def select(self, tool_id: str) -> None:
        self._buttons[tool_id].setChecked(True)

    def current(self) -> str:
        return next((tid for tid, b in self._buttons.items() if b.isChecked()), "")


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
