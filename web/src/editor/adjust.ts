// Tone and colour adjustments (no DOM) — a line-by-line port of src/umc/editor/adjust.py; keep
// the two in step (both test suites check the pixels in tests/fixtures/adjust_cases.json).
// Order: exposure → temperature / tint → brightness / contrast → levels (channel, then all) →
// curves (channel, then all) → saturation / vibrance. The per-channel steps are folded into one
// 256-entry table per channel first; saturation and vibrance then mix the channels per pixel.
import type { EditorDoc } from "./document";
import { PatchCommand, type Command } from "./history";
import { cropData } from "./layer";
import { clipRect } from "./tools/base";

export const CHANNELS = ["rgb", "r", "g", "b"] as const; // "rgb" = all colours together
export type Channel = (typeof CHANNELS)[number];

export interface Levels {
  black: number; // input black point (0…254)
  white: number; // input white point (black+1…255)
  gamma: number; // mid-tones (0.1…10; > 1 brightens)
  outBlack: number;
  outWhite: number;
}

export const NEUTRAL_LEVELS: Levels = { black: 0, white: 255, gamma: 1, outBlack: 0, outWhite: 255 };
export const IDENTITY_CURVE: [number, number][] = [[0, 0], [255, 255]];

export interface Adjustments {
  exposure?: number; // stops, −3…+3
  brightness?: number; // −100…+100
  contrast?: number;
  temperature?: number; // −100 cooler … +100 warmer
  tint?: number; // −100 greener … +100 more magenta
  saturation?: number;
  vibrance?: number; // mostly the dull colours, skin tones half as much
  levels?: Partial<Record<Channel, Levels>>;
  curves?: Partial<Record<Channel, [number, number][]>>;
}

export const levelsIdentity = (l: Levels) =>
  l.black === 0 && l.white === 255 && l.gamma === 1 && l.outBlack === 0 && l.outWhite === 255;
const curveIdentity = (c: [number, number][]) => c.length === 2 && c[0][0] === 0 && c[0][1] === 0 && c[1][0] === 255 && c[1][1] === 255;

export function isIdentity(a: Adjustments): boolean {
  return !a.exposure && !a.brightness && !a.contrast && !a.temperature && !a.tint && !a.saturation && !a.vibrance &&
    Object.values(a.levels ?? {}).every((l) => !l || levelsIdentity(l)) &&
    Object.values(a.curves ?? {}).every((c) => !c || curveIdentity(c));
}

// ------------------------------------------------------------------ per-channel pieces

const toLinear = (v: number) => {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
};
const toSrgb = (v: number) => {
  const x = Math.min(1, Math.max(0, v));
  return 255 * (x <= 0.0031308 ? x * 12.92 : 1.055 * x ** (1 / 2.4) - 0.055);
};
const clamp255 = (v: number) => Math.min(255, Math.max(0, v));
const round = (v: number) => Math.floor(v + 0.5);

function levels(v: number, l: Levels): number {
  const t = Math.min(1, Math.max(0, (v - l.black) / Math.max(1, l.white - l.black)));
  return l.outBlack + (l.outWhite - l.outBlack) * t ** (1 / l.gamma);
}

/** 256 output values for a curve through `points` (x, y in 0…255): a monotone cubic
 *  (Fritsch–Carlson), so it never overshoots between points; flat beyond the end points. */
export function curveTable(points: [number, number][]): Float64Array {
  const pts = new Map<number, number>();
  for (const [x, y] of points) pts.set(Math.trunc(x), Math.trunc(y)); // the last point at an x wins
  const xs = [...pts.keys()].sort((a, b) => a - b);
  const ys = xs.map((x) => pts.get(x) as number);
  const n = xs.length;
  const out = new Float64Array(256);
  if (n === 0) {
    for (let i = 0; i < 256; i++) out[i] = i;
    return out;
  }
  if (n === 1) return out.fill(ys[0]);
  const d = Array.from({ length: n - 1 }, (_, k) => (ys[k + 1] - ys[k]) / (xs[k + 1] - xs[k]));
  const m = new Array<number>(n).fill(0);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let k = 1; k < n - 1; k++) m[k] = d[k - 1] * d[k] <= 0 ? 0 : (d[k - 1] + d[k]) / 2;
  for (let k = 0; k < n - 1; k++) {
    if (d[k] === 0) {
      m[k] = m[k + 1] = 0;
      continue;
    }
    const a = m[k] / d[k];
    const b = m[k + 1] / d[k];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[k] = t * a * d[k];
      m[k + 1] = t * b * d[k];
    }
  }
  let k = 0;
  for (let x = 0; x < 256; x++) {
    if (x <= xs[0]) {
      out[x] = ys[0];
      continue;
    }
    if (x >= xs[n - 1]) {
      out[x] = ys[n - 1];
      continue;
    }
    while (xs[k + 1] < x) k++;
    const h = xs[k + 1] - xs[k];
    const t = (x - xs[k]) / h;
    const t2 = t * t;
    const t3 = t2 * t;
    out[x] = (2 * t3 - 3 * t2 + 1) * ys[k] + (t3 - 2 * t2 + t) * h * m[k] + (-2 * t3 + 3 * t2) * ys[k + 1] + (t3 - t2) * h * m[k + 1];
  }
  return out;
}

/** What every per-channel step does to each value of R, G and B (3 tables of 256). */
export function tables(a: Adjustments): Uint8Array[] {
  const exposure = a.exposure ?? 0;
  const base = Array.from({ length: 256 }, (_, v) => (exposure ? toSrgb(toLinear(v) * 2 ** exposure) : v));
  const k = (a.temperature ?? 0) / 100;
  const m = (a.tint ?? 0) / 100;
  const gains = [1 + 0.3 * k, 1 - 0.3 * m, 1 - 0.3 * k]; // warmer: more red, less blue
  const bright = a.brightness ?? 0;
  const con = a.contrast ?? 0;
  const factor = 1 + con / 100;
  const curves = new Map<Channel, Float64Array>();
  for (const [ch, pts] of Object.entries(a.curves ?? {}) as [Channel, [number, number][]][]) if (pts && !curveIdentity(pts)) curves.set(ch, curveTable(pts));
  return (["r", "g", "b"] as const).map((name, c) => {
    const out = new Uint8Array(256);
    for (let v = 0; v < 256; v++) {
      let x = clamp255(base[v] * gains[c]);
      if (bright || con) x = clamp255((x + bright * 2.55 - 128) * factor + 128);
      for (const ch of [name, "rgb"] as Channel[]) {
        const l = a.levels?.[ch];
        if (l && !levelsIdentity(l)) x = levels(x, l);
      }
      for (const ch of [name, "rgb"] as Channel[]) {
        const t = curves.get(ch);
        if (t) x = t[clamp255(round(x))];
      }
      out[v] = clamp255(round(x));
    }
    return out;
  });
}

// ------------------------------------------------------------------ whole pixels

/** RGBA pixels with `a` applied (a new array); alpha is never changed. */
export function applyData(data: Uint8ClampedArray, a: Adjustments): Uint8ClampedArray {
  const out = new Uint8ClampedArray(data);
  if (isIdentity(a)) return out;
  const [tr, tg, tb] = tables(a);
  const sat = 1 + (a.saturation ?? 0) / 100;
  const vib = (a.vibrance ?? 0) / 100;
  const mix = !!a.saturation || !!a.vibrance;
  for (let i = 0; i < data.length; i += 4) {
    const r = tr[data[i]];
    const g = tg[data[i + 1]];
    const b = tb[data[i + 2]];
    if (!mix) {
      out[i] = r;
      out[i + 1] = g;
      out[i + 2] = b;
      continue;
    }
    const luma = r * 0.299 + g * 0.587 + b * 0.114;
    let f = sat;
    if (vib) {
      const s = (Math.max(r, g, b) - Math.min(r, g, b)) / 255;
      const skin = r > g && g > b; // orange-ish hues: protect skin tones
      f *= 1 + vib * (1 - s) * (skin ? 0.5 : 1);
    }
    out[i] = clamp255(round(luma + (r - luma) * f));
    out[i + 1] = clamp255(round(luma + (g - luma) * f));
    out[i + 2] = clamp255(round(luma + (b - luma) * f));
  }
  return out;
}

/** 256 counts per channel ("rgb" = brightness) of the pixels that aren't fully transparent. */
export function histogram(data: Uint8ClampedArray): Record<Channel, Uint32Array> {
  const h = { rgb: new Uint32Array(256), r: new Uint32Array(256), g: new Uint32Array(256), b: new Uint32Array(256) };
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] === 0) continue;
    h.r[data[i]]++;
    h.g[data[i + 1]]++;
    h.b[data[i + 2]]++;
    h.rgb[round(data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114)]++;
  }
  return h;
}

/** Black / white points that stretch `channel`'s tones (ignoring the darkest and brightest
 *  0.5 %); "rgb" uses the brightness. */
export function autoLevels(data: Uint8ClampedArray, channel: Channel = "rgb", clip = 0.005): Levels {
  const counts = histogram(data)[channel];
  let total = 0;
  for (const c of counts) total += c;
  if (total === 0) return { ...NEUTRAL_LEVELS };
  let cum = 0;
  let black = 256;
  let white = 256;
  for (let v = 0; v < 256; v++) {
    cum += counts[v];
    if (black === 256 && cum > total * clip) black = v;
    if (white === 256 && cum >= total * (1 - clip)) white = v;
  }
  black = Math.min(black, 254);
  if (white <= black) return { ...NEUTRAL_LEVELS };
  return { ...NEUTRAL_LEVELS, black, white };
}

// ------------------------------------------------------------------ on the document

/** The part of the active layer an adjustment changes (inside the selection), or null. */
export function regionData(doc: EditorDoc): Uint8ClampedArray | null {
  const l = doc.layers[doc.active];
  const region = clipRect(doc, l);
  return region.empty ? null : cropData(l.data, l.width, region);
}

/** The active layer's pixels as they would look with `a` (for the live preview). */
export function preview(doc: EditorDoc, a: Adjustments): Uint8ClampedArray {
  const l = doc.layers[doc.active];
  const out = new Uint8ClampedArray(l.data);
  const region = clipRect(doc, l);
  if (region.empty || isIdentity(a)) return out;
  const part = applyData(cropData(l.data, l.width, region), a);
  for (let y = 0; y < region.height; y++) out.set(part.subarray(y * region.width * 4, (y + 1) * region.width * 4), ((region.y0 + y) * l.width + region.x0) * 4);
  return out;
}

/** Apply `a` to the active layer inside the selection — one undo step. */
export function applyToDocument(doc: EditorDoc, a: Adjustments, label = "Adjust"): Command | null {
  if (isIdentity(a)) return null;
  const l = doc.layers[doc.active];
  const region = clipRect(doc, l);
  if (region.empty) return null;
  const before = cropData(l.data, l.width, region);
  const after = applyData(before, a);
  for (let y = 0; y < region.height; y++) l.data.set(after.subarray(y * region.width * 4, (y + 1) * region.width * 4), ((region.y0 + y) * l.width + region.x0) * 4);
  return PatchCommand.fromRegion(label, doc, doc.active, region, before);
}
