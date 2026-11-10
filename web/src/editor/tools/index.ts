// Editor tools, in tool-bar order (same as the desktop app).
import type { Rasterizer } from "../raster";
import type { Tool } from "./base";
import { FillTool, PickerTool } from "./fill";
import { BrushTool, EraserTool } from "./paint";
import { CropTool, SelectTool } from "./select";
import { EllipseTool, LineTool, RectTool } from "./shapes";
import { TextTool } from "./text";

export function makeTools(raster: Rasterizer): Tool[] {
  return [
    new BrushTool(),
    new EraserTool(),
    new FillTool(),
    new PickerTool(),
    new LineTool(raster),
    new RectTool(raster),
    new EllipseTool(raster),
    new TextTool(),
    new SelectTool(),
    new CropTool(),
  ];
}
