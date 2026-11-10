// What every editor tool looks like, and shared pixel helpers (port of tools/base.py).
import type { OptionSpec, Values } from "../../core/options";
import { docBounds, type EditorDoc } from "../document";
import { EMPTY, Rect } from "../geometry";
import type { Command } from "../history";
import { layerBounds, type Layer, type RGBA } from "../layer";

export interface ToolContext {
  primary: RGBA;
  secondary: RGBA;
  options: Values;
  shift: boolean;
  button: "left" | "right";
  font: string;
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
  readonly cursor: "cross" | "brush" | "pick" | "text" = "cross";

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
