// Flood fill and "similar colour" masks, shared by the fill bucket and the magic wand. No imports
// from tools/ (the selection module needs these, and tools/base needs the selection module).
import { Rect } from "./geometry";

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
export function candidatesOf(src: Uint8ClampedArray, sw: number, ox: number, oy: number, w: number, h: number, seedIndex: number, tol: number): Uint8Array {
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

