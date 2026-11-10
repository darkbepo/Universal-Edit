"""Editor tools, in tool-bar order."""

from __future__ import annotations

from umc.editor.tools.base import Tool
from umc.editor.tools.fill import FillTool, PickerTool
from umc.editor.tools.paint import BrushTool, EraserTool

TOOLS: list[type[Tool]] = [BrushTool, EraserTool, FillTool, PickerTool]
