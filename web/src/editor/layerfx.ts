// Layer effects: drop shadow, glow, outline and colour overlay — live, kept on the layer
// (port of src/umc/editor/layerfx.py: same fields, numbers and drawing order).
//
// `Layer.fx` holds a LayerFx (null = no effects). The layer's own pixels never change: the
// effects are drawn whenever the picture is put together (composite), merged or rasterised.
// Order, bottom to top: shadow, glow, outline (around the outside), then the layer itself with
// the colour overlay on top. Effects are made from what the layer shows (mask applied), before
// its opacity. The blur is a Gaussian approximation (the desktop uses Pillow's), so soft edges
// can differ a little between the apps.
import { opt, type OptionSpec, type Values } from "../core/options";
import { Rect } from "./geometry";
import type { Layer } from "./layer";

export interface LayerFx {
  shadow: boolean;
  shadow_color: string;
  shadow_opacity: number; // %
  shadow_angle: number; // the direction the shadow falls, degrees clockwise from the right
  shadow_distance: number; // px
  shadow_blur: number; // px (softness)
  glow: boolean;
  glow_color: string;
  glow_opacity: number;
  glow_size: number;
  outline: boolean;
  outline_color: string;
  outline_width: number;
  overlay: boolean;
  overlay_color: string;
  overlay_opacity: number;
}

export const NO_FX: Readonly<LayerFx> = Object.freeze({
  shadow: false, shadow_color: "#000000", shadow_opacity: 60, shadow_angle: 45, shadow_distance: 8, shadow_blur: 8,
  glow: false, glow_color: "#ffe680", glow_opacity: 80, glow_size: 12,
  outline: false, outline_color: "#000000", outline_width: 3,
  overlay: false, overlay_color: "#ff0000", overlay_opacity: 100,
});

export type FxKey = "shadow" | "glow" | "outline" | "overlay";

/** id, label (window title / undo step), icon — in the order the rail lists them. */
export const EFFECTS: [FxKey, string, string][] = [
  ["shadow", "Drop shadow", "shadow"],
  ["glow", "Glow", "sun-high"],
  ["outline", "Outline", "border-outer"],
  ["overlay", "Colour overlay", "color-swatch"],
];

export const anyFx = (fx: LayerFx | null | undefined): fx is LayerFx => !!fx && (fx.shadow || fx.glow || fx.outline || fx.overlay);

export function sameFx(a: LayerFx | null | undefined, b: LayerFx | null | undefined): boolean {
  if (!anyFx(a) || !anyFx(b)) return !anyFx(a) && !anyFx(b);
  return (Object.keys(NO_FX) as (keyof LayerFx)[]).every((k) => a[k] === b[k]);
}

const HEX = /^#[0-9a-f]{6}$/;

/** Effects read from a file (either app); unknown or bad fields are left at their defaults. */
export function fxFromDict(d: unknown): LayerFx | null {
  if (!d || typeof d !== "object") return null;
  const out: Record<string, unknown> = { ...NO_FX };
  for (const [k, def] of Object.entries(NO_FX)) {
    const v = (d as Record<string, unknown>)[k];
    if (v === undefined) continue;
    if (typeof def === "boolean") out[k] = !!v;
    else if (typeof def === "number") {
      const n = Math.trunc(Number(v));
      if (Number.isFinite(n)) out[k] = n;
    } else if (typeof v === "string" && HEX.test(v.trim().toLowerCase())) out[k] = v.trim().toLowerCase();
  }
  const fx = out as unknown as LayerFx;
  return anyFx(fx) ? fx : null;
}

// ---------------------------------------------------------------- settings windows

export const OPTIONS: Record<FxKey, OptionSpec[]> = {
  shadow: [
    opt.bool("shadow", "Show drop shadow", true),
    opt.color("shadow_color", "Colour", "#000000"),
    opt.slider("shadow_opacity", "Opacity", 60, 0, 100, { unit: "%" }),
    opt.slider("shadow_angle", "Direction", 45, 0, 359, { unit: "°" }),
    opt.slider("shadow_distance", "Distance", 8, 0, 200, { unit: "px" }),
    opt.slider("shadow_blur", "Softness", 8, 0, 100, { unit: "px" }),
  ],
  glow: [
    opt.bool("glow", "Show glow", true),
    opt.color("glow_color", "Colour", "#ffe680"),
    opt.slider("glow_opacity", "Opacity", 80, 0, 100, { unit: "%" }),
    opt.slider("glow_size", "Size", 12, 1, 100, { unit: "px" }),
  ],
  outline: [
    opt.bool("outline", "Show outline", true),
    opt.color("outline_color", "Colour", "#000000"),
    opt.slider("outline_width", "Width", 3, 1, 50, { unit: "px" }),
  ],
  overlay: [
    opt.bool("overlay", "Show colour overlay", true),
    opt.color("overlay_color", "Colour", "#ff0000"),
    opt.slider("overlay_opacity", "Strength", 100, 0, 100, { unit: "%" }),
  ],
};

/** The settings window's values for effect `key` of a layer (null = no effects: the defaults,
 *  switched off). Same as window_values in layerfx.py. */
export function windowValues(fx: LayerFx | null | undefined, key: FxKey): Values {
  const cur = (fx ?? NO_FX) as unknown as Record<string, unknown>;
  const values = Object.fromEntries(OPTIONS[key].map((s) => [s.name, cur[s.name]])) as Values;
  if (!fx || !fx[key]) values[key] = false;
  return values;
}

/** `fx` with a window's `values` set; null when no effect is left on. */
export function withValues(fx: LayerFx | null | undefined, values: Values): LayerFx | null {
  const out: Record<string, unknown> = { ...(fx ?? NO_FX) };
  for (const k of Object.keys(NO_FX)) if (k in values) out[k] = values[k];
  const next = out as unknown as LayerFx;
  return anyFx(next) ? next : null;
}

// ---------------------------------------------------------------- drawing

const round = (v: number) => Math.floor(v + 0.5); // as layerfx._round
const sigmaOf = (px: number) => px / 2;

function shadowOffset(fx: LayerFx): [number, number] {
  const a = (fx.shadow_angle * Math.PI) / 180;
  return [round(Math.cos(a) * fx.shadow_distance), round(Math.sin(a) * fx.shadow_distance)];
}

/** How far the effects reach past the layer's pixels (all sides). */
export function fxMargin(fx: LayerFx | null | undefined): number {
  if (!anyFx(fx)) return 0;
  let m = 0;
  if (fx.shadow) {
    const [dx, dy] = shadowOffset(fx);
    m = Math.max(m, Math.max(Math.abs(dx), Math.abs(dy)) + Math.ceil(3 * sigmaOf(fx.shadow_blur)));
  }
  if (fx.glow) m = Math.max(m, Math.ceil(3 * sigmaOf(fx.glow_size)));
  if (fx.outline) m = Math.max(m, fx.outline_width);
  return m;
}

/** Where the layer draws anything, effects included. */
export function fxBounds(layer: Layer): Rect {
  const m = fxMargin(layer.fx);
  return new Rect(layer.x - m, layer.y - m, layer.x + layer.width + m, layer.y + layer.height + m);
}

/** Box sizes for three box blurs that approximate a Gaussian of `sigma`. */
function boxes(sigma: number): number[] {
  const ideal = Math.sqrt((12 * sigma * sigma) / 3 + 1);
  let wl = Math.floor(ideal);
  if (wl % 2 === 0) wl--;
  const wu = wl + 2;
  const m = Math.round((12 * sigma * sigma - 3 * wl * wl - 12 * wl - 9) / (-4 * wl - 4));
  return [0, 1, 2].map((i) => (i < m ? wl : wu));
}

function boxPass(src: Float32Array, dst: Float32Array, w: number, h: number, r: number, horizontal: boolean): void {
  const [len, lines, step, stride] = horizontal ? [w, h, 1, w] : [h, w, w, 1];
  const k = 1 / (2 * r + 1);
  for (let line = 0; line < lines; line++) {
    const base = line * stride;
    let sum = 0;
    for (let i = -r; i <= r; i++) if (i >= 0 && i < len) sum += src[base + i * step];
    for (let i = 0; i < len; i++) {
      dst[base + i * step] = sum * k;
      const add = i + r + 1, drop = i - r;
      if (add < len) sum += src[base + add * step];
      if (drop >= 0) sum -= src[base + drop * step];
    }
  }
}

function gaussPass(src: Float32Array, dst: Float32Array, w: number, h: number, sigma: number, horizontal: boolean): void {
  const r = Math.ceil(3 * sigma);
  const kernel: number[] = [];
  let total = 0;
  for (let i = -r; i <= r; i++) {
    const v = Math.exp(-(i * i) / (2 * sigma * sigma));
    kernel.push(v);
    total += v;
  }
  const [len, lines, step, stride] = horizontal ? [w, h, 1, w] : [h, w, w, 1];
  for (let line = 0; line < lines; line++) {
    const base = line * stride;
    for (let i = 0; i < len; i++) {
      let s = 0;
      for (let j = -r; j <= r; j++) {
        const p = i + j;
        if (p >= 0 && p < len) s += src[base + p * step] * kernel[j + r];
      }
      dst[base + i * step] = s / total;
    }
  }
}

/** Gaussian blur of a 0…1 plane (w × h). */
export function blurPlane(plane: Float32Array, w: number, h: number, sigma: number): Float32Array {
  if (sigma <= 0) return plane;
  const a = new Float32Array(plane);
  const b = new Float32Array(plane.length);
  if (sigma < 2) {
    gaussPass(a, b, w, h, sigma, true);
    gaussPass(b, a, w, h, sigma, false);
  } else {
    for (const size of boxes(sigma)) {
      const r = (size - 1) / 2;
      boxPass(a, b, w, h, r, true);
      boxPass(b, a, w, h, r, false);
    }
  }
  return a;
}

function shift(plane: Float32Array, w: number, h: number, dx: number, dy: number): Float32Array {
  const out = new Float32Array(plane.length);
  for (let y = Math.max(0, dy); y < Math.min(h, h + dy); y++) {
    const sy = y - dy;
    for (let x = Math.max(0, dx); x < Math.min(w, w + dx); x++) out[y * w + x] = plane[sy * w + x - dx];
  }
  return out;
}

/** Grow the shape by `width` px: one-pixel steps alternating a cross and a square (a near-round
 *  outline); soft edges stay soft. Same as layerfx.dilate. */
export function dilate(plane: Float32Array, w: number, h: number, width: number): Float32Array {
  let cur = plane;
  for (let step = 0; step < width; step++) {
    const next = new Float32Array(cur);
    const diag = step % 2 === 1;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        let m = next[y * w + x];
        for (let oy = -1; oy <= 1; oy++) {
          const yy = y + oy;
          if (yy < 0 || yy >= h) continue;
          for (let ox = -1; ox <= 1; ox++) {
            if ((ox === 0 && oy === 0) || (!diag && ox !== 0 && oy !== 0)) continue;
            const xx = x + ox;
            if (xx < 0 || xx >= w) continue;
            const v = cur[yy * w + xx];
            if (v > m) m = v;
          }
        }
        next[y * w + x] = m;
      }
    cur = next;
  }
  return cur;
}

const rgbOf = (hex: string): [number, number, number] => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];

/** Paint a colour with coverage `a` (0…1 per pixel) over `out` (float RGB 0…255, alpha 0…1). */
function over(out: Float32Array, n: number, a: Float32Array, color: (i: number, c: number) => number): void {
  for (let i = 0; i < n; i++) {
    const sa = a[i];
    if (sa <= 0) continue;
    const da = out[i * 4 + 3];
    const oa = sa + da * (1 - sa);
    const k = da * (1 - sa);
    for (let c = 0; c < 3; c++) out[i * 4 + c] = (color(i, c) * sa + out[i * 4 + c] * k) / oa;
    out[i * 4 + 3] = oa;
  }
}

/** `data` (RGBA w × h, what the layer shows) with `fx` drawn around / over it, same size. Pass
 *  enough transparent border for the effects to fit (fxMargin). */
export function renderFx(data: Uint8ClampedArray, w: number, h: number, fx: LayerFx | null | undefined): Uint8ClampedArray {
  if (!anyFx(fx)) return data;
  const n = w * h;
  const alpha = new Float32Array(n);
  for (let i = 0; i < n; i++) alpha[i] = data[i * 4 + 3] / 255;
  const out = new Float32Array(n * 4);
  const solid = (hex: string) => {
    const rgb = rgbOf(hex);
    return (_i: number, c: number) => rgb[c];
  };
  // 8-bit planes before blurring, as the desktop (Pillow blurs "L" images)
  const quant = (p: Float32Array) => p.map((v) => Math.min(255, Math.max(0, Math.floor(v * 255 + 0.5))) / 255);
  if (fx.shadow) {
    const [dx, dy] = shadowOffset(fx);
    const blurred = quant(blurPlane(quant(alpha), w, h, sigmaOf(fx.shadow_blur)));
    const a = shift(blurred, w, h, dx, dy).map((v) => v * (fx.shadow_opacity / 100));
    over(out, n, a, solid(fx.shadow_color));
  }
  if (fx.glow) {
    const blurred = quant(blurPlane(quant(alpha), w, h, sigmaOf(fx.glow_size)));
    over(out, n, blurred.map((v) => Math.min(v * 2, 1) * (fx.glow_opacity / 100)), solid(fx.glow_color));
  }
  if (fx.outline) over(out, n, dilate(alpha, w, h, fx.outline_width), solid(fx.outline_color));
  const k = fx.overlay ? fx.overlay_opacity / 100 : 0;
  const tint = rgbOf(fx.overlay_color);
  over(out, n, alpha, (i, c) => data[i * 4 + c] * (1 - k) + tint[c] * k);
  const res = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n * 4; i++) res[i] = i % 4 === 3 ? out[i] * 255 : out[i]; // the array rounds
  for (let i = 0; i < n * 4; i += 4) if (res[i + 3] === 0) res[i] = res[i + 1] = res[i + 2] = 0;
  return res;
}

/** What `layer` shows inside canvas `rect` (mask and effects, not its opacity), as RGBA of
 *  rect's size. `data` replaces the layer's pixels (live previews). */
export function fxRegion(layer: Layer, rect: Rect, data: Uint8ClampedArray = layer.data): Uint8ClampedArray {
  const m = fxMargin(layer.fx);
  const need = new Rect(rect.x0 - m, rect.y0 - m, rect.x1 + m, rect.y1 + m);
  const nw = need.width, nh = need.height;
  const padded = new Uint8ClampedArray(nw * nh * 4);
  const mask = layer.mask && layer.mask.length === data.length ? layer.mask : null;
  const part = need.intersect(new Rect(layer.x, layer.y, layer.x + layer.width, layer.y + layer.height));
  for (let y = part.y0; y < part.y1; y++)
    for (let x = part.x0; x < part.x1; x++) {
      const si = ((y - layer.y) * layer.width + (x - layer.x)) * 4;
      const di = ((y - need.y0) * nw + (x - need.x0)) * 4;
      padded[di] = data[si];
      padded[di + 1] = data[si + 1];
      padded[di + 2] = data[si + 2];
      padded[di + 3] = mask ? (data[si + 3] * mask[si]) / 255 : data[si + 3];
    }
  const drawn = renderFx(padded, nw, nh, layer.fx);
  const out = new Uint8ClampedArray(rect.width * rect.height * 4);
  for (let y = 0; y < rect.height; y++) {
    const start = ((y + m) * nw + m) * 4;
    out.set(drawn.subarray(start, start + rect.width * 4), y * rect.width * 4);
  }
  return out;
}

/** `layer` with its effects (and mask) drawn into its pixels: a plain layer grown to fit them.
 *  Rasterising and merging use this. */
export function bakedLayer(layer: Layer): Layer {
  if (!anyFx(layer.fx)) return layer.fx ? { ...layer, fx: null } : layer;
  const b = fxBounds(layer);
  return { ...layer, data: fxRegion(layer, b), width: b.width, height: b.height, x: b.x0, y: b.y0, mask: null, fx: null, text: null, shape: null };
}
