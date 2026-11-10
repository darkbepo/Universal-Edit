// Text: typed in an on-canvas box (page), committed as pixels on a new layer (port of tools/text.py).
import { opt } from "../../core/options";
import type { EditorDoc } from "../document";
import type { Rect } from "../geometry";
import { LayerListCommand, type Command } from "../history";
import { makeLayer, type RGBA } from "../layer";
import type { FontSpec, Rasterizer } from "../raster";
import { Tool, type ToolContext } from "./base";

export function addTextLayer(doc: EditorDoc, raster: Rasterizer, text: string, x: number, y: number, font: FontSpec, color: RGBA): Command | null {
  if (!text.trim()) return null;
  const img = raster.text(text, font, color);
  const name = text.trim().split("\n")[0].slice(0, 30);
  const before = [...doc.layers];
  const activeBefore = doc.active;
  const layers = [...doc.layers];
  layers.splice(doc.active + 1, 0, makeLayer(name, img.width, img.height, img.data, { x: Math.trunc(x), y: Math.trunc(y) }));
  doc.layers = layers;
  doc.active += 1;
  const selectedBefore = [...(doc.selected ?? [])];
  doc.selected = [];
  return new LayerListCommand("Text", before, layers, activeBefore, doc.active, selectedBefore, []);
}

/** Click to place text; the page shows an edit box at `anchor` and calls addTextLayer. */
export class TextTool extends Tool {
  readonly id = "text";
  readonly label = "Text";
  readonly shortcut = "T";
  override readonly cursor = "text" as const;
  override readonly options = [
    opt.slider("size", "Size", 48, 6, 500, { unit: "px" }),
    opt.bool("bold", "Bold", false),
    opt.bool("italic", "Italic", false),
  ];
  anchor: [number, number] | null = null;

  override press(_doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.anchor = [x, y];
    return null;
  }
}
