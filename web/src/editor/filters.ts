// Filters and one-click looks (no DOM): blur, sharpen, looks — each an effects.Effect. Port of
// src/umc/editor/filters.py. Blur here is a three-pass box blur (close to, not pixel-identical
// with, Pillow's Gaussian); both work on premultiplied colour, so transparent edges don't get
// dark fringes. The looks come from looks.json, which both apps share exactly.
import { applyData, fromSettings } from "./adjust";
import type { Effect } from "./effects";
import looksJson from "./looks.json";

type LookDef = { label: string; steps: Record<string, unknown>[] };
export const LOOKS: Record<string, LookDef> = Object.fromEntries(Object.entries(looksJson).filter(([k]) => !k.startsWith("_"))) as Record<string, LookDef>;

/** Widths of three box blurs that together approximate a Gaussian of `sigma`. */
function boxes(sigma: number): number[] {
  const n = 3;
  const ideal = Math.sqrt((12 * sigma * sigma) / n + 1);
  let wl = Math.floor(ideal);
  if (wl % 2 === 0) wl--;
  const wu = wl + 2;
  const m = Math.round((12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4));
  return Array.from({ length: n }, (_, i) => (i < m ? wl : wu));
}

/** One running-sum box blur of radius r along rows (horizontal) or columns, edges clamped. */
function boxPass(src: Float32Array, dst: Float32Array, w: number, h: number, r: number, horizontal: boolean): void {
  const [len, lines, step, lineStep] = horizontal ? [w, h, 4, w * 4] : [h, w, w * 4, 4];
  const norm = 1 / (2 * r + 1);
  for (let line = 0; line < lines; line++) {
    const base = line * lineStep;
    for (let c = 0; c < 4; c++) {
      const at = (i: number) => src[base + Math.min(len - 1, Math.max(0, i)) * step + c];
      let acc = 0;
      for (let i = -r; i <= r; i++) acc += at(i);
      for (let i = 0; i < len; i++) {
        dst[base + i * step + c] = acc * norm;
        acc += at(i + r + 1) - at(i - r);
      }
    }
  }
}

/** One pass of a real Gaussian kernel along rows or columns, edges clamped (small blurs). */
function gaussPass(src: Float32Array, dst: Float32Array, w: number, h: number, sigma: number, horizontal: boolean): void {
  const r = Math.max(1, Math.ceil(3 * sigma));
  const k = Array.from({ length: 2 * r + 1 }, (_, i) => Math.exp(-((i - r) ** 2) / (2 * sigma * sigma)));
  const total = k.reduce((a, b) => a + b, 0);
  for (let i = 0; i < k.length; i++) k[i] /= total;
  const [len, lines, step, lineStep] = horizontal ? [w, h, 4, w * 4] : [h, w, w * 4, 4];
  for (let line = 0; line < lines; line++) {
    const base = line * lineStep;
    for (let i = 0; i < len; i++) {
      for (let c = 0; c < 4; c++) {
        let acc = 0;
        for (let j = -r; j <= r; j++) acc += k[j + r] * src[base + Math.min(len - 1, Math.max(0, i + j)) * step + c];
        dst[base + i * step + c] = acc;
      }
    }
  }
}

/** Below this the three box blurs would all be 1 px wide (no blur at all): use a real kernel. */
const SMALL_SIGMA = 2;

/** Gaussian blur of RGBA pixels, on premultiplied colour: an exact kernel for small radii,
 *  three box blurs (fast, close to Gaussian) for big ones. */
export function blurData(data: Uint8ClampedArray, w: number, h: number, sigma: number): Uint8ClampedArray {
  if (sigma <= 0) return new Uint8ClampedArray(data);
  let a = new Float32Array(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const al = data[i + 3] / 255;
    a[i] = data[i] * al;
    a[i + 1] = data[i + 1] * al;
    a[i + 2] = data[i + 2] * al;
    a[i + 3] = data[i + 3];
  }
  const b = new Float32Array(data.length);
  if (sigma < SMALL_SIGMA) {
    gaussPass(a, b, w, h, sigma, true);
    gaussPass(b, a, w, h, sigma, false);
  } else {
    for (const size of boxes(sigma)) {
      const r = (size - 1) / 2;
      boxPass(a, b, w, h, r, true);
      boxPass(b, a, w, h, r, false);
    }
  }
  // Uint8ClampedArray rounds when storing, so no "+ 0.5" here (that would round up twice)
  const out = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const al = a[i + 3];
    out[i + 3] = al;
    if (out[i + 3] === 0) continue;
    const k = 255 / al;
    out[i] = a[i] * k;
    out[i + 1] = a[i + 1] * k;
    out[i + 2] = a[i + 2] * k;
  }
  return out;
}

export function blur(radius: number): Effect {
  return {
    identity: radius <= 0,
    margin: Math.ceil(3 * radius),
    apply: (data, w, h, scale = 1) => blurData(data, w, h, radius * scale),
  };
}

/** Unsharp mask: edges get `amount` % stronger; differences below `threshold` are left alone. */
export function sharpen(amount: number, radius = 1, threshold = 0): Effect {
  return {
    identity: amount <= 0,
    margin: Math.ceil(3 * radius) + 1,
    apply: (data, w, h, scale = 1) => {
      const soft = blurData(data, w, h, Math.max(0.1, radius * scale));
      const out = new Uint8ClampedArray(data);
      const k = amount / 100;
      for (let i = 0; i < data.length; i += 4) {
        for (let c = 0; c < 3; c++) {
          const d = data[i + c] - soft[i + c];
          if (Math.abs(d) >= threshold) out[i + c] = data[i + c] + d * k;
        }
      }
      return out;
    },
  };
}

/** A look from LOOKS, mixed with the original by `strength` (0…1). */
export function look(name: string, strength = 1): Effect {
  const def = LOOKS[name];
  return {
    identity: !def || strength <= 0,
    apply: (data) => {
      if (!def || strength <= 0) return new Uint8ClampedArray(data);
      let out = data;
      for (const step of def.steps) out = applyData(out, fromSettings(step));
      if (strength >= 1) return out === data ? new Uint8ClampedArray(data) : out;
      const mixed = new Uint8ClampedArray(data);
      for (let i = 0; i < data.length; i += 4) for (let c = 0; c < 3; c++) mixed[i + c] = data[i + c] + (out[i + c] - data[i + c]) * strength;
      return mixed;
    },
  };
}

/** Blur everything outside the selection (select the person first), with a soft edge. */
export function backgroundBlur(radius: number, soften = 4): Effect {
  return { ...blur(radius), outsideSelection: soften };
}
