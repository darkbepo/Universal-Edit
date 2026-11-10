// Editor tools, in tool-bar order (same as the desktop app).
import type { Rasterizer } from "../raster";
import type { Tool } from "./base";
import { FillTool, PickerTool } from "./fill";
import { GradientTool } from "./gradient";
import { MoveTool } from "./move";
import { BrushTool, CloneStampTool, EraserTool, HighlighterTool, MaskBrushTool } from "./paint";
import { CropTool, EllipseSelectTool, LassoTool, MagicWandTool, PolygonLassoTool, SelectTool } from "./select";
import { ArrowTool, EllipseTool, LineTool, RectTool } from "./shapes";
import { TextTool } from "./text";

export function makeTools(raster: Rasterizer): Tool[] {
  return [
    new MoveTool(),
    new BrushTool(),
    new HighlighterTool(),
    new EraserTool(),
    new MaskBrushTool(),
    new CloneStampTool(),
    new FillTool(),
    new GradientTool(),
    new PickerTool(),
    new LineTool(raster),
    new ArrowTool(raster),
    new RectTool(raster),
    new EllipseTool(raster),
    new TextTool(),
    new SelectTool(),
    new EllipseSelectTool(),
    new LassoTool(),
    new PolygonLassoTool(),
    new MagicWandTool(),
    new CropTool(),
  ];
}
