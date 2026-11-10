// Fill bucket (scanline flood fill) and colour picker — port of tools/fill.py.
import { opt } from "../../core/options";
import { composite, docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData, layerBounds, pasteData, type RGBA } from "../layer";
import { candidatesOf, floodMask } from "../flood";
import { blendPixel, clipRect, colorOf, restrict, Tool, type ToolContext } from "./base";

export class FillTool extends Tool {
  readonly id = "fill";
  readonly label = "Fill";
  readonly shortcut = "G";
  override readonly options = [
    opt.slider("tolerance", "Tolerance", 32, 0, 255),
    opt.bool("sample_all", "Sample all layers", false),
  ];
  private pending: { index: number; region: Rect; before: Uint8ClampedArray } | null = null;

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    this.pending = null;
    const layer = doc.layers[doc.active];
    const clip = clipRect(doc, layer);
    const lx = Math.floor(x) - layer.x;
    const ly = Math.floor(y) - layer.y;
    if (clip.empty || !clip.contains(lx, ly)) return null;
    const w = clip.width;
    const h = clip.height;
    // sample array + how to index it
    let src: Uint8ClampedArray;
    let sw: number;
    let ox: number;
    let oy: number;
    if (ctx.options.sample_all) {
      src = composite(doc, clip.offset(layer.x, layer.y));
      [sw, ox, oy] = [w, 0, 0];
    } else {
      [src, sw, ox, oy] = [layer.data, layer.width, clip.x0, clip.y0];
    }
    const sx = lx - clip.x0;
    const sy = ly - clip.y0;
    const si = ((sy + oy) * sw + sx + ox) * 4;
    const cand = candidatesOf(src, sw, ox, oy, w, h, si, Number(ctx.options.tolerance));
    const { mask, box } = floodMask(cand, w, h, sx, sy);
    if (box.empty) return null;
    const region = box.offset(clip.x0, clip.y0);
    const before = cropData(layer.data, layer.width, region);
    const color = colorOf(ctx);
    const opaque = color[3] === 255 && layer.data.byteOffset % 4 === 0;
    const d32 = opaque ? new Uint32Array(layer.data.buffer, layer.data.byteOffset, layer.data.length >> 2) : null;
    const c32 = new Uint32Array(new Uint8Array(color).buffer)[0];
    for (let yy = box.y0; yy < box.y1; yy++) {
      const mrow = yy * w;
      const drow = (yy + clip.y0) * layer.width + clip.x0;
      for (let xx = box.x0; xx < box.x1; xx++) {
        if (!mask[mrow + xx]) continue;
        if (d32) d32[drow + xx] = c32;
        else blendPixel(layer.data, (drow + xx) * 4, layer.data, (drow + xx) * 4, color, 1);
      }
    }
    restrict(doc, layer, region, before); // shaped / soft selections
    this.pending = { index: doc.active, region, before };
    return region.offset(layer.x, layer.y);
  }

  override release(doc: EditorDoc, _x?: number, _y?: number, _ctx?: ToolContext): Command | null {
    if (!this.pending) return null;
    const { index, region, before } = this.pending;
    this.pending = null;
    return PatchCommand.fromRegion(this.label, doc, index, region, before);
  }

  /** The fill is applied on press; an interrupted gesture must put the pixels back. */
  override cancel(doc: EditorDoc): Rect | null {
    if (!this.pending) return null;
    const { index, region, before } = this.pending;
    this.pending = null;
    const layer = doc.layers[index];
    pasteData(layer.data, layer.width, before, region);
    return region.offset(layer.x, layer.y);
  }
}

export class PickerTool extends Tool {
  readonly id = "picker";
  readonly label = "Colour picker";
  readonly shortcut = "I";
  override readonly cursor = "pick" as const;
  override readonly options = [opt.bool("sample_all", "Sample all layers", true)];
  picked: RGBA | null = null;
  pickedButton: "left" | "right" = "left";

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    this.picked = this.sample(doc, Math.floor(x), Math.floor(y), !!ctx.options.sample_all);
    this.pickedButton = ctx.button;
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    return this.press(doc, x, y, ctx);
  }

  private sample(doc: EditorDoc, x: number, y: number, all: boolean): RGBA | null {
    if (!docBounds(doc).contains(x, y)) return null;
    if (all) return Array.from(composite(doc, new Rect(x, y, x + 1, y + 1))) as RGBA;
    const layer = doc.layers[doc.active];
    if (!layerBounds(layer).contains(x, y)) return null;
    const i = ((y - layer.y) * layer.width + (x - layer.x)) * 4;
    return Array.from(layer.data.subarray(i, i + 4)) as RGBA;
  }
}

export { candidatesOf, floodMask, type Flood } from "../flood";
