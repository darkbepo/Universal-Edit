"""Editor tools, in tool-bar order."""

from __future__ import annotations

from umc.editor.tools.base import Tool
from umc.editor.tools.fill import FillTool, PickerTool
from umc.editor.tools.gradient import GradientTool
from umc.editor.tools.move import MoveTool
from umc.editor.tools.paint import BrushTool, EraserTool, HighlighterTool, MaskBrushTool
from umc.editor.tools.select import (
    CropTool,
    EllipseSelectTool,
    LassoTool,
    MagicWandTool,
    PolygonLassoTool,
    SelectTool,
)
from umc.editor.tools.shapes import ArrowTool, EllipseTool, LineTool, RectTool
from umc.editor.tools.text import TextTool

TOOLS: list[type[Tool]] = [
    MoveTool,
    BrushTool,
    HighlighterTool,
    EraserTool,
    MaskBrushTool,
    FillTool,
    GradientTool,
    PickerTool,
    LineTool,
    ArrowTool,
    RectTool,
    EllipseTool,
    TextTool,
    SelectTool,
    EllipseSelectTool,
    LassoTool,
    PolygonLassoTool,
    MagicWandTool,
    CropTool,
]
