// Content-aware fill: paint over the selected area from what surrounds it. The desktop app uses
// OpenCV's Telea inpainting (cv2.inpaint); this is a port of the same idea — fast marching from
// the edge of the hole inwards, each new pixel a weighted average of the known pixels within
// `radius`, weighted by direction (along the marching front's normal), distance and level.
import { EditorError, type EditorDoc } from "./document";
import { Rect } from "./geometry";
import { PatchCommand, type Command } from "./history";
import { cropData, layerBounds } from "./layer";

export const RADIUS = 5;
const KNOWN = 0;
const BAND = 1;
const INSIDE = 2;
const FAR = 1e6;

/** Binary min-heap of (time, index). */
class Heap {
  private t: number[] = [];
  private i: number[] = [];
  get size(): number {
    return this.t.length;
  }
  push(t: number, i: number): void {
    let k = this.t.length;
    this.t.push(t);
    this.i.push(i);
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (this.t[p] <= t) break;
      this.t[k] = this.t[p];
      this.i[k] = this.i[p];
      k = p;
    }
    this.t[k] = t;
    this.i[k] = i;
  }
  pop(): number {
    const top = this.i[0];
    const lt = this.t.pop() as number;
    const li = this.i.pop() as number;
    const n = this.t.length;
    if (n) {
      let k = 0;
      for (;;) {
        let c = 2 * k + 1;
        if (c >= n) break;
        if (c + 1 < n && this.t[c + 1] < this.t[c]) c++;
        if (this.t[c] >= lt) break;
        this.t[k] = this.t[c];
        this.i[k] = this.i[c];
        k = c;
      }
      this.t[k] = lt;
      this.i[k] = li;
    }
    return top;
  }
}

/** Paints in the pixels of `rgba` (w × h) where `hole` is set, in place. */
export function inpaint(rgba: Uint8ClampedArray, w: number, h: number, hole: Uint8Array, radius = RADIUS): void {
  const n = w * h;
  const flag = new Uint8Array(n);
  const T = new Float32Array(n);
  const heap = new Heap();
  for (let k = 0; k < n; k++) if (hole[k]) {
    flag[k] = INSIDE;
    T[k] = FAR;
  }
  // the band: known pixels touching the hole
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const k = y * w + x;
      if (flag[k] !== KNOWN) continue;
      if ((x > 0 && flag[k - 1] === INSIDE) || (x < w - 1 && flag[k + 1] === INSIDE) || (y > 0 && flag[k - w] === INSIDE) || (y < h - 1 && flag[k + w] === INSIDE)) {
        flag[k] = BAND;
        heap.push(0, k);
      }
    }
  const t = (x: number, y: number) => (x < 0 || y < 0 || x >= w || y >= h ? FAR : T[y * w + x]);
  const f = (x: number, y: number) => (x < 0 || y < 0 || x >= w || y >= h ? INSIDE : flag[y * w + x]);
  // the eikonal equation from two neighbours (Sethian's solve)
  const solve = (x1: number, y1: number, x2: number, y2: number): number => {
    const a = f(x1, y1) !== INSIDE ? t(x1, y1) : FAR;
    const b = f(x2, y2) !== INSIDE ? t(x2, y2) : FAR;
    if (a < FAR && b < FAR) {
      const r = 2 - (a - b) * (a - b);
      if (r > 0) return (a + b + Math.sqrt(r)) / 2;
    }
    return Math.min(a, b) + 1;
  };
  const r2 = radius * radius;
  const paint = (x: number, y: number) => {
    // the front's normal at (x, y)
    const gx = (f(x + 1, y) !== INSIDE && f(x - 1, y) !== INSIDE ? (t(x + 1, y) - t(x - 1, y)) / 2 : f(x + 1, y) !== INSIDE ? t(x + 1, y) - T[y * w + x] : f(x - 1, y) !== INSIDE ? T[y * w + x] - t(x - 1, y) : 0);
    const gy = (f(x, y + 1) !== INSIDE && f(x, y - 1) !== INSIDE ? (t(x, y + 1) - t(x, y - 1)) / 2 : f(x, y + 1) !== INSIDE ? t(x, y + 1) - T[y * w + x] : f(x, y - 1) !== INSIDE ? T[y * w + x] - t(x, y - 1) : 0);
    const sum = [0, 0, 0, 0];
    let total = 0;
    const tk = T[y * w + x];
    for (let dy = -radius; dy <= radius; dy++) {
      const yy = y + dy;
      if (yy < 0 || yy >= h) continue;
      for (let dx = -radius; dx <= radius; dx++) {
        const xx = x + dx;
        const d2 = dx * dx + dy * dy;
        if (xx < 0 || xx >= w || d2 > r2 || d2 === 0) continue;
        const j = yy * w + xx;
        if (flag[j] === INSIDE) continue;
        const rx = x - xx;
        const ry = y - yy;
        const len = Math.sqrt(d2);
        const dir = Math.abs(rx * gx + ry * gy) / len || 1e-6;
        const dst = 1 / d2;
        const lev = 1 / (1 + Math.abs(T[j] - tk));
        const wgt = dir * dst * lev;
        const p = j * 4;
        // premultiplied, so transparent neighbours don't bring in their hidden colour
        const a = rgba[p + 3] / 255;
        sum[0] += wgt * rgba[p] * a;
        sum[1] += wgt * rgba[p + 1] * a;
        sum[2] += wgt * rgba[p + 2] * a;
        sum[3] += wgt * a;
        total += wgt;
      }
    }
    if (total <= 0) return;
    const o = (y * w + x) * 4;
    const alpha = sum[3] / total;
    rgba[o + 3] = alpha * 255;
    if (alpha > 0) for (let c = 0; c < 3; c++) rgba[o + c] = sum[c] / total / alpha;
  };
  while (heap.size) {
    const k = heap.pop();
    if (flag[k] === KNOWN) continue;
    flag[k] = KNOWN;
    const x = k % w;
    const y = (k - x) / w;
    for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
      if (f(nx, ny) !== INSIDE) continue;
      const j = ny * w + nx;
      T[j] = Math.min(solve(nx - 1, ny, nx, ny - 1), solve(nx + 1, ny, nx, ny - 1), solve(nx - 1, ny, nx, ny + 1), solve(nx + 1, ny, nx, ny + 1));
      flag[j] = BAND; // known enough to paint from once painted
      paint(nx, ny);
      heap.push(T[j], j);
    }
  }
}

/** 0…1 weights of the selection over `region` (canvas coordinates; may reach past it). */
export function fillMask(doc: EditorDoc, region: Rect): Float32Array {
  const out = new Float32Array(region.width * region.height);
  const sel = doc.selection;
  const part = sel ? sel.intersect(region) : region;
  for (let y = part.y0; y < part.y1; y++)
    for (let x = part.x0; x < part.x1; x++) {
      const m = sel && doc.selectionMask ? doc.selectionMask[(y - sel.y0) * sel.width + (x - sel.x0)] / 255 : 1;
      out[(y - region.y0) * region.width + (x - region.x0)] = m;
    }
  return out;
}

/** Fill the selection on the active layer from its surroundings (one undo step). */
export function contentAwareFill(doc: EditorDoc, radius = RADIUS): Command | null {
  if (!doc.selection) throw new EditorError("Select what should disappear first (lasso, rectangle…).");
  const layer = doc.layers[doc.active];
  const lb = layerBounds(layer);
  const sel = doc.selection.intersect(lb).intersect(new Rect(0, 0, doc.width, doc.height));
  if (sel.empty) return null;
  const pad = radius * 3 + 8;
  const region = new Rect(sel.x0 - pad, sel.y0 - pad, sel.x1 + pad, sel.y1 + pad).intersect(lb);
  const weight = fillMask(doc, region);
  const hole = new Uint8Array(weight.length);
  let count = 0;
  for (let k = 0; k < weight.length; k++) if (weight[k] > 0) {
    hole[k] = 1;
    count++;
  }
  if (!count) return null;
  if (count === weight.length) throw new EditorError("Leave some of the picture unselected around it to fill from.");
  const local = region.offset(-layer.x, -layer.y);
  const before = cropData(layer.data, layer.width, local);
  const filled = new Uint8ClampedArray(before);
  inpaint(filled, local.width, local.height, hole, radius);
  // soft edges: mix on premultiplied colour, like every other selection-limited change
  for (let k = 0; k < weight.length; k++) {
    const t = weight[k];
    if (t <= 0) continue;
    const p = k * 4;
    const a0 = before[p + 3] / 255;
    const a1 = filled[p + 3] / 255;
    const a = a0 + (a1 - a0) * t;
    const o = ((local.y0 + Math.floor(k / local.width)) * layer.width + local.x0 + (k % local.width)) * 4;
    for (let c = 0; c < 3; c++) layer.data[o + c] = a > 0 ? (before[p + c] * a0 + (filled[p + c] * a1 - before[p + c] * a0) * t) / a : 0;
    layer.data[o + 3] = a * 255;
  }
  return PatchCommand.fromRegion("Content-aware fill", doc, doc.active, local, before);
}
