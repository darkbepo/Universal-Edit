// Fill bucket (scanline flood fill) and colour picker — port of tools/fill.py.
import { opt } from "../../core/options";
import { composite, docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData, layerBounds, pasteData, type RGBA } from "../layer";
import { blendPixel, clipRect, colorOf, Tool, type ToolContext } from "./base";

/** Pixels 4-connected to (sx, sy) where `candidates` is 1. */
export interface Flood {
  mask: Uint8Array;
  /** Bounding box of the filled pixels (x1 / y1 exclusive); empty when nothing was filled. */
  box: Rect;
}

export function floodMask(candidates: Uint8Array, w: number, h: number, sx: number, sy: number): Flood {
  const filled = new Uint8Array(w * h);
  let [bx0, by0, bx1, by1] = [w, h, 0, 0];
  if (!candidates[sy * w + sx]) return { mask: filled, box: new Rect(0, 0, 0, 0) };
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
    if (x0 < bx0) bx0 = x0;
    if (x1 > bx1) bx1 = x1;
    if (y < by0) by0 = y;
    if (y + 1 > by1) by1 = y + 1;
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
  return { mask: filled, box: new Rect(bx0, by0, bx1, by1) };
}

/** 1 where the pixel matches `seed` within `tol` per channel (a fast 32-bit compare for tol 0). */
function candidates(src: Uint8ClampedArray, sw: number, ox: number, oy: number, w: number, h: number, seedIndex: number, tol: number): Uint8Array {
  const cand = new Uint8Array(w * h);
  if (tol <= 0 && src.byteOffset % 4 === 0) {
    const s32 = new Uint32Array(src.buffer, src.byteOffset, src.length >> 2);
    const seed = s32[seedIndex >> 2];
    for (let yy = 0; yy < h; yy++) {
      const base = (yy + oy) * sw + ox;
      const row = yy * w;
      for (let xx = 0; xx < w; xx++) cand[row + xx] = s32[base + xx] === seed ? 1 : 0;
    }
    return cand;
  }
  const [r, g, b, a] = [src[seedIndex], src[seedIndex + 1], src[seedIndex + 2], src[seedIndex + 3]];
  for (let yy = 0; yy < h; yy++) {
    let i = ((yy + oy) * sw + ox) * 4;
    const row = yy * w;
    for (let xx = 0; xx < w; xx++, i += 4) {
      cand[row + xx] = Math.abs(src[i] - r) <= tol && Math.abs(src[i + 1] - g) <= tol && Math.abs(src[i + 2] - b) <= tol && Math.abs(src[i + 3] - a) <= tol ? 1 : 0;
    }
  }
  return cand;
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
    const cand = candidates(src, sw, ox, oy, w, h, si, Number(ctx.options.tolerance));
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
