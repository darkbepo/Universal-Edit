// Fill bucket (scanline flood fill) and colour picker — port of tools/fill.py.
import { opt } from "../../core/options";
import { composite, docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData, layerBounds, pasteData, type RGBA } from "../layer";
import { blendPixel, clipRect, colorOf, Tool, type ToolContext } from "./base";

/** Pixels 4-connected to (sx, sy) where `candidates` is 1. */
export function floodMask(candidates: Uint8Array, w: number, h: number, sx: number, sy: number): Uint8Array {
  const filled = new Uint8Array(w * h);
  if (!candidates[sy * w + sx]) return filled;
  const stack: number[] = [sx, sy];
  while (stack.length) {
    const y = stack.pop() as number;
    const x = stack.pop() as number;
    const row = y * w;
    if (filled[row + x] || !candidates[row + x]) continue;
    let x0 = x;
    while (x0 > 0 && candidates[row + x0 - 1] && !filled[row + x0 - 1]) x0--;
    let x1 = x + 1;
    while (x1 < w && candidates[row + x1] && !filled[row + x1]) x1++;
    filled.fill(1, row + x0, row + x1);
    for (const ny of [y - 1, y + 1]) {
      if (ny < 0 || ny >= h) continue;
      const nrow = ny * w;
      let inRun = false;
      for (let nx = x0; nx < x1; nx++) {
        const open = candidates[nrow + nx] && !filled[nrow + nx];
        if (open && !inRun) stack.push(nx, ny);
        inRun = !!open;
      }
    }
  }
  return filled;
}

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
    const seed = [src[si], src[si + 1], src[si + 2], src[si + 3]];
    const tol = Number(ctx.options.tolerance);
    const cand = new Uint8Array(w * h);
    for (let yy = 0; yy < h; yy++) {
      let i = ((yy + oy) * sw + ox) * 4;
      for (let xx = 0; xx < w; xx++, i += 4) {
        cand[yy * w + xx] =
          Math.abs(src[i] - seed[0]) <= tol && Math.abs(src[i + 1] - seed[1]) <= tol && Math.abs(src[i + 2] - seed[2]) <= tol && Math.abs(src[i + 3] - seed[3]) <= tol ? 1 : 0;
      }
    }
    const mask = floodMask(cand, w, h, sx, sy);
    let [bx0, by0, bx1, by1] = [w, h, -1, -1];
    for (let yy = 0; yy < h; yy++)
      for (let xx = 0; xx < w; xx++)
        if (mask[yy * w + xx]) {
          if (xx < bx0) bx0 = xx;
          if (xx > bx1) bx1 = xx;
          if (yy < by0) by0 = yy;
          if (yy > by1) by1 = yy;
        }
    if (bx1 < 0) return null;
    const region = new Rect(bx0 + clip.x0, by0 + clip.y0, bx1 + 1 + clip.x0, by1 + 1 + clip.y0);
    const before = cropData(layer.data, layer.width, region);
    const color = colorOf(ctx);
    for (let yy = by0; yy <= by1; yy++)
      for (let xx = bx0; xx <= bx1; xx++) {
        if (!mask[yy * w + xx]) continue;
        const di = ((yy + clip.y0) * layer.width + xx + clip.x0) * 4;
        if (color[3] === 255) layer.data.set(color, di);
        else blendPixel(layer.data, di, layer.data, di, color, 1);
      }
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
