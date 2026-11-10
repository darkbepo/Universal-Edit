// Anything that changes the active layer's pixels inside the selection with a live preview:
// adjustments, filters and looks. Port of src/umc/editor/effects.py.
// An effect turns RGBA pixels (width × height) into new ones; `scale` < 1 means the picture was
// shrunk for a quick preview, so effects measured in pixels (blur radius…) shrink with it.
// Effects that look at neighbours give a `margin`: they get that much extra of the layer around
// the selection, so a blur at the selection's edge mixes with what lies beyond it.
import type { EditorDoc } from "./document";
import { Rect } from "./geometry";
import { PatchCommand, type Command } from "./history";
import { cropData, type Layer } from "./layer";
import * as sel from "./selection";
import { clipRect, restrict } from "./tools/base";

export interface Effect {
  identity: boolean;
  margin?: number;
  /** Background blur: work on the inverted selection softened by this many px. */
  outsideSelection?: number;
  apply(data: Uint8ClampedArray, width: number, height: number, scale?: number): Uint8ClampedArray;
}

export const PREVIEW_PIXELS = 2_000_000; // live previews of bigger areas are worked out on a smaller copy

/** The part of the active layer an effect changes (inside the selection), or null. */
export function regionData(doc: EditorDoc): { data: Uint8ClampedArray; width: number; height: number } | null {
  const l = doc.layers[doc.active];
  const region = clipRect(doc, l);
  return region.empty ? null : { data: cropData(l.data, l.width, region), width: region.width, height: region.height };
}

function paste(dst: Uint8ClampedArray, dstW: number, src: Uint8ClampedArray, r: Rect): void {
  for (let y = 0; y < r.height; y++) dst.set(src.subarray(y * r.width * 4, (y + 1) * r.width * 4), ((r.y0 + y) * dstW + r.x0) * 4);
}

/** `effect` on `region` of the layer (layer coordinates), using its margin around it. With
 *  `maxPixels` (previews) a bigger area is worked out on a reduced copy and scaled back. */
function run(l: Layer, region: Rect, effect: Effect, maxPixels?: number): Uint8ClampedArray {
  const m = Math.max(0, Math.ceil(effect.margin ?? 0));
  const wide = m ? new Rect(region.x0 - m, region.y0 - m, region.x1 + m, region.y1 + m).intersect(new Rect(0, 0, l.width, l.height)) : region;
  const part = cropData(l.data, l.width, wide);
  let done: Uint8ClampedArray;
  if (maxPixels !== undefined && region.width * region.height > maxPixels) {
    const f = Math.ceil(Math.sqrt((region.width * region.height) / maxPixels));
    const small = reduce(part, wide.width, wide.height, f);
    done = enlarge(effect.apply(small.data, small.width, small.height, 1 / f), small.width, small.height, wide.width, wide.height);
    if (!m) for (let i = 3; i < done.length; i += 4) done[i] = part[i]; // colour changes keep the layer's edges sharp
  } else done = effect.apply(part, wide.width, wide.height, 1);
  return wide === region ? done : cropData(done, wide.width, region.offset(-wide.x0, -wide.y0));
}

/** Box average by a whole number `f` (fast and clean). */
export function reduce(data: Uint8ClampedArray, w: number, h: number, f: number): { data: Uint8ClampedArray; width: number; height: number } {
  const nw = Math.max(1, Math.ceil(w / f));
  const nh = Math.max(1, Math.ceil(h / f));
  const out = new Uint8ClampedArray(nw * nh * 4);
  const sum = new Float64Array(4);
  for (let y = 0; y < nh; y++) {
    for (let x = 0; x < nw; x++) {
      sum.fill(0);
      let n = 0;
      for (let yy = y * f; yy < Math.min(h, (y + 1) * f); yy++) {
        for (let xx = x * f; xx < Math.min(w, (x + 1) * f); xx++) {
          const i = (yy * w + xx) * 4;
          for (let c = 0; c < 4; c++) sum[c] += data[i + c];
          n++;
        }
      }
      for (let c = 0; c < 4; c++) out[(y * nw + x) * 4 + c] = sum[c] / n; // stored rounded
    }
  }
  return { data: out, width: nw, height: nh };
}

/** Bilinear stretch of `src` (sw × sh) to w × h. */
export function enlarge(src: Uint8ClampedArray, sw: number, sh: number, w: number, h: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    const fy = Math.min(sh - 1, Math.max(0, ((y + 0.5) * sh) / h - 0.5));
    const y0 = Math.floor(fy);
    const y1 = Math.min(sh - 1, y0 + 1);
    const ty = fy - y0;
    for (let x = 0; x < w; x++) {
      const fx = Math.min(sw - 1, Math.max(0, ((x + 0.5) * sw) / w - 0.5));
      const x0 = Math.floor(fx);
      const x1 = Math.min(sw - 1, x0 + 1);
      const tx = fx - x0;
      for (let c = 0; c < 4; c++) {
        const a = src[(y0 * sw + x0) * 4 + c] * (1 - tx) + src[(y0 * sw + x1) * 4 + c] * tx;
        const b = src[(y1 * sw + x0) * 4 + c] * (1 - tx) + src[(y1 * sw + x1) * 4 + c] * tx;
        out[(y * w + x) * 4 + c] = a * (1 - ty) + b * ty; // stored rounded
      }
    }
  }
  return out;
}

/** Run `fn` with the selection inverted and softened when `effect` works outside it (background
 *  blur); the real selection is put back afterwards. `fn` gets false when there is no subject. */
function scoped<T>(doc: EditorDoc, effect: Effect, fn: (ok: boolean) => T): T {
  const soften = effect.outsideSelection;
  if (soften === undefined) return fn(true);
  if (!doc.selection) return fn(false); // nothing selected = no subject: nothing to blur around
  const saved: [typeof doc.selection, typeof doc.selectionMask] = [doc.selection, doc.selectionMask];
  try {
    sel.invert(doc);
    if (!doc.selection) return fn(false); // everything was selected: nothing around the subject
    if (soften > 0) sel.feather(doc, soften);
    return fn(true);
  } finally {
    [doc.selection, doc.selectionMask] = saved;
  }
}

/** The active layer's pixels as they would look with `effect` (for the live preview). */
export function preview(doc: EditorDoc, effect: Effect, maxPixels = PREVIEW_PIXELS): Uint8ClampedArray {
  return scoped(doc, effect, (ok) => (ok ? previewIn(doc, effect, maxPixels) : new Uint8ClampedArray(doc.layers[doc.active].data)));
}

function previewIn(doc: EditorDoc, effect: Effect, maxPixels: number): Uint8ClampedArray {
  const l = doc.layers[doc.active];
  const out = new Uint8ClampedArray(l.data);
  const region = clipRect(doc, l);
  if (region.empty || effect.identity) return out;
  const done = run(l, region, effect, maxPixels);
  if (doc.selectionMask) {
    // mix with the original by the selection (on a throw-away copy of the layer)
    const tmp = { ...l, data: new Uint8ClampedArray(l.data) };
    paste(tmp.data, l.width, done, region);
    restrict(doc, tmp, region, cropData(l.data, l.width, region));
    return tmp.data;
  }
  paste(out, l.width, done, region);
  return out;
}

/** Apply `effect` to the active layer inside the selection — one undo step. */
export function applyToDocument(doc: EditorDoc, effect: Effect, label: string): Command | null {
  if (effect.identity) return null;
  return scoped(doc, effect, (ok) => (ok ? applyIn(doc, effect, label) : null));
}

function applyIn(doc: EditorDoc, effect: Effect, label: string): Command | null {
  const l = doc.layers[doc.active];
  const region = clipRect(doc, l);
  if (region.empty) return null;
  const before = cropData(l.data, l.width, region);
  paste(l.data, l.width, run(l, region, effect), region);
  restrict(doc, l, region, before); // shaped / soft selections
  return PatchCommand.fromRegion(label, doc, doc.active, region, before);
}
