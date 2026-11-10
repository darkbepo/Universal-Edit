"""Editor tools, in tool-bar order."""

from __future__ import annotations

from umc.editor.tools.base import Tool
from umc.editor.tools.fill import FillTool, PickerTool
from umc.editor.tools.paint import BrushTool, EraserTool
from umc.editor.tools.select import CropTool, SelectTool
from umc.editor.tools.shapes import EllipseTool, LineTool, RectTool
from umc.editor.tools.text import TextTool

TOOLS: list[type[Tool]] = [
    BrushTool,
    EraserTool,
    FillTool,
    PickerTool,
    LineTool,
    RectTool,
    EllipseTool,
    TextTool,
    SelectTool,
    CropTool,
]
