// Gradient tool — port of src/umc/editor/tools/gradient.py. Drag from where the gradient starts
// to where it ends; it paints colour (front → back, or front → see-through), shapes the layer's
// mask (shows at the start, hides at the end), or makes a soft selection that fades out.
// Shift keeps the direction to 45° steps.
import { opt } from "../../core/options";
import { docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData, layerBounds, withProps } from "../layer";
import * as ops from "../ops";
import { combine } from "../selection";
import { clipRect, restrict, Tool, type ToolContext } from "./base";
import type { Outline } from "./select";

/** 0 at `start` … 1 at `end` for every pixel centre of `rect` (canvas coordinates). */
export function gradientT(rect: Rect, start: [number, number], end: [number, number], shape: string): Float32Array {
  const out = new Float32Array(rect.width * rect.height);
  const [sx, sy] = start;
  const dx = end[0] - sx;
  const dy = end[1] - sy;
  const len2 = dx * dx + dy * dy;
  if (len2 <= 0) return out;
  const len = Math.sqrt(len2);
  for (let y = 0; y < rect.height; y++) {
    const py = rect.y0 + y + 0.5;
    for (let x = 0; x < rect.width; x++) {
      const px = rect.x0 + x + 0.5;
      const t = shape === "radial" ? Math.hypot(px - sx, py - sy) / len : ((px - sx) * dx + (py - sy) * dy) / len2;
      out[y * rect.width + x] = Math.min(1, Math.max(0, t));
    }
  }
  return out;
}

export class GradientTool extends Tool {
  readonly id = "gradient";
  readonly label = "Gradient";
  readonly shortcut = "G";
  override readonly options = [
    opt.choice("shape", "Shape", "linear", [["linear", "Straight"], ["radial", "Round"]]),
    opt.choice("target", "Paints", "colour", [["colour", "Colour on the layer"], ["mask", "Layer mask"], ["selection", "Soft selection"]]),
    opt.choice("colours", "Colours", "front_back", [["front_back", "Front → back colour"], ["front_clear", "Front colour → see-through"]], { showIf: (v) => v.target === "colour" }),
  ];
  outline: Outline | null = null;
  private start: [number, number] | null = null;

  private point(x: number, y: number, ctx?: ToolContext): [number, number] {
    if (!ctx?.shift || !this.start) return [x, y];
    const [sx, sy] = this.start;
    const step = Math.PI / 4;
    const ang = Math.round(Math.atan2(y - sy, x - sx) / step) * step;
    const r = Math.hypot(x - sx, y - sy);
    return [sx + r * Math.cos(ang), sy + r * Math.sin(ang)];
  }

  override press(_doc: EditorDoc, x: number, y: number): Rect | null {
    this.start = [x, y];
    this.outline = ["poly", [this.start, [x, y]]];
    return null;
  }

  override move(_doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Rect | null {
    if (this.start) this.outline = ["poly", [this.start, this.point(x, y, ctx)]];
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Command | null {
    if (!this.start || !ctx) return null;
    const start = this.start;
    const end = this.point(x, y, ctx);
    this.start = null;
    this.outline = null;
    if (Math.hypot(end[0] - start[0], end[1] - start[1]) < 1) return null;
    const o = ctx.options;
    const shape = String(o.shape);
    if (o.target === "selection") {
      const t = gradientT(docBounds(doc), start, end, shape);
      return combine(doc, Uint8Array.from(t, (v) => Math.round((1 - v) * 255)), "replace", "Gradient selection");
    }
    const layer = doc.layers[doc.active];
    if (o.target === "mask") {
      const t = gradientT(layerBounds(layer), start, end, shape);
      const mask = new Uint8ClampedArray(t.length * 4);
      for (let i = 0; i < t.length; i++) mask.set([(1 - t[i]) * 255, (1 - t[i]) * 255, (1 - t[i]) * 255, 255], i * 4);
      return ops.replaceLayer(doc, doc.active, "Gradient mask", withProps(layer, { mask }));
    }
    const region = clipRect(doc, layer);
    if (region.empty) return null;
    const t = gradientT(region.offset(layer.x, layer.y), start, end, shape);
    const c0 = ctx.primary;
    const c1 = o.colours === "front_clear" ? [c0[0], c0[1], c0[2], 0] : ctx.secondary;
    const before = cropData(layer.data, layer.width, region);
    const a0 = c0[3] / 255;
    const a1 = c1[3] / 255;
    for (let y = 0; y < region.height; y++) {
      for (let x = 0; x < region.width; x++) {
        const k = t[y * region.width + x];
        const a = a0 + (a1 - a0) * k; // the gradient's colour, premultiplied mix of the two ends
        if (a <= 0) continue;
        const di = ((region.y0 + y) * layer.width + region.x0 + x) * 4;
        const da = layer.data[di + 3] / 255;
        const oa = a + da * (1 - a);
        for (let c = 0; c < 3; c++) {
          const g = (c0[c] * a0 + (c1[c] * a1 - c0[c] * a0) * k) / a;
          layer.data[di + c] = (g * a + layer.data[di + c] * da * (1 - a)) / oa;
        }
        layer.data[di + 3] = oa * 255;
      }
    }
    restrict(doc, layer, region, before);
    return PatchCommand.fromRegion(this.label, doc, doc.active, region, before);
  }

  override cancel(): Rect | null {
    this.start = null;
    this.outline = null;
    return null;
  }
}
