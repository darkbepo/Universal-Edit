// What every editor tool looks like, and shared pixel helpers (port of tools/base.py).
import type { OptionSpec, Values } from "../../core/options";
import { docBounds, type EditorDoc } from "../document";
import { EMPTY, Rect } from "../geometry";
import type { Command } from "../history";
import { layerBounds, type Layer, type RGBA } from "../layer";
import { weight } from "../selection";

export interface ToolContext {
  primary: RGBA;
  secondary: RGBA;
  options: Values;
  shift: boolean;
  button: "left" | "right";
  font: string;
  alt?: boolean;
  /** Screen pixels per canvas pixel (for "click near a point" tolerances). */
  zoom?: number;
}

export const colorOf = (ctx: ToolContext): RGBA => (ctx.button === "right" ? ctx.secondary : ctx.primary);

export interface Preview {
  data: Uint8ClampedArray;
  width: number;
  height: number;
  x: number;
  y: number;
}

/** Pointer-driven tool. Coordinates are canvas pixels (floats). press / move return the canvas
 *  rectangle to repaint; release returns the undoable Command for what the gesture changed. */
export abstract class Tool {
  abstract readonly id: string;
  abstract readonly label: string;
  abstract readonly shortcut: string;
  readonly options: OptionSpec[] = [];
  readonly cursor: "cross" | "brush" | "pick" | "text" | "move" = "cross";

  press(_doc: EditorDoc, _x: number, _y: number, _ctx: ToolContext): Rect | null {
    return null;
  }

  move(_doc: EditorDoc, _x: number, _y: number, _ctx: ToolContext): Rect | null {
    return null;
  }

  release(_doc: EditorDoc, _x: number, _y: number, _ctx: ToolContext): Command | null {
    return null;
  }

  preview(_doc: EditorDoc): Preview | null {
    return null;
  }

  cancel(_doc: EditorDoc): Rect | null {
    return null;
  }
}

/** Where a tool may change `layer`, in layer coordinates (canvas ∩ layer ∩ selection). */
export function clipRect(doc: EditorDoc, layer: Layer): Rect {
  let r = layerBounds(layer).intersect(docBounds(doc));
  if (doc.selection) r = r.intersect(doc.selection);
  return r.empty ? EMPTY : r.offset(-layer.x, -layer.y);
}

/** Source-over `color` with coverage `cov` (0..1) onto pixel `i` of `dst`, reading the original from `src[j]`. */
/** How much each pixel of `region` (layer coordinates, inside clipRect) is selected, 0…1;
 *  null = all of it. */
export function selectionWeight(doc: EditorDoc, layer: Layer, region: Rect): Float32Array | null {
  return weight(doc, region.offset(layer.x, layer.y));
}

/** Keep a change to `region` of `layer` only where (and as much as) it is selected. `before` is
 *  that region's original pixels; the new ones are mixed with them by the selection's strength,
 *  on premultiplied colour so soft edges keep their colour. */
export function restrict(doc: EditorDoc, layer: Layer, region: Rect, before: Uint8ClampedArray): void {
  const w = selectionWeight(doc, layer, region);
  if (!w || region.empty) return;
  for (let y = 0; y < region.height; y++) {
    for (let x = 0; x < region.width; x++) {
      const t = w[y * region.width + x];
      if (t >= 1) continue;
      const bi = (y * region.width + x) * 4;
      const di = ((region.y0 + y) * layer.width + region.x0 + x) * 4;
      const a0 = before[bi + 3] / 255;
      const a1 = layer.data[di + 3] / 255;
      const a = a0 + (a1 - a0) * t;
      for (let c = 0; c < 3; c++) {
        const p = before[bi + c] * a0 + (layer.data[di + c] * a1 - before[bi + c] * a0) * t;
        layer.data[di + c] = a > 0 ? p / a : 0;
      }
      layer.data[di + 3] = a * 255;
    }
  }
}

export function blendPixel(dst: Uint8ClampedArray, i: number, src: Uint8ClampedArray, j: number, color: RGBA, cov: number): void {
  const ca = (color[3] / 255) * cov;
  const ba = src[j + 3] / 255;
  const oa = ca + ba * (1 - ca);
  if (oa <= 0) {
    dst[i] = dst[i + 1] = dst[i + 2] = dst[i + 3] = 0;
    return;
  }
  const k = ba * (1 - ca);
  dst[i] = (color[0] * ca + src[j] * k) / oa + 0.5;
  dst[i + 1] = (color[1] * ca + src[j + 1] * k) / oa + 0.5;
  dst[i + 2] = (color[2] * ca + src[j + 2] * k) / oa + 0.5;
  dst[i + 3] = oa * 255 + 0.5;
}

/** Alpha-composite `src` (box-sized RGBA) onto `dst` pixel `i` from `src[j]`, scaled by `opacity`. */
export function overPixel(dst: Uint8ClampedArray, i: number, src: Uint8ClampedArray, j: number, opacity = 1): void {
  const sa = (src[j + 3] / 255) * opacity;
  if (sa <= 0) return;
  const da = dst[i + 3] / 255;
  const oa = sa + da * (1 - sa);
  const k = da * (1 - sa);
  dst[i] = (src[j] * sa + dst[i] * k) / oa + 0.5;
  dst[i + 1] = (src[j + 1] * sa + dst[i + 1] * k) / oa + 0.5;
  dst[i + 2] = (src[j + 2] * sa + dst[i + 2] * k) / oa + 0.5;
  dst[i + 3] = oa * 255 + 0.5;
}
