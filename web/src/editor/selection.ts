// Selections of any shape (no DOM) — port of src/umc/editor/selection.py.
// `doc.selection` is the rectangle around what is selected (canvas coordinates; null = nothing),
// so everything that only needs "where" keeps working with a Rect. `doc.selectionMask` says how
// much of each pixel in that rectangle is selected (0…255, the rectangle's width × height, soft
// edges allowed); null means the whole rectangle — a plain rectangle selection.
// Shapes are combined by mode: "replace", "add" (Shift), "subtract" (Alt), "intersect" (both).
import { composite, docBounds, type EditorDoc } from "./document";
import { Rect } from "./geometry";
import { SelectionCommand, type Command, type History } from "./history";
import { blurData } from "./filters";
import { candidatesOf, floodMask } from "./flood";

export type Mode = "replace" | "add" | "subtract" | "intersect";

export const modeFor = (shift: boolean, alt: boolean): Mode => (shift && alt ? "intersect" : shift ? "add" : alt ? "subtract" : "replace");

/** A whole-canvas mask (width × height, 0…255). */
export type Full = Uint8Array;

// ------------------------------------------------------------------ whole-canvas masks

/** The selection as a canvas-sized mask (all 0 when nothing is selected). */
export function fullMask(doc: EditorDoc): Full {
  const out = new Uint8Array(doc.width * doc.height);
  const sel = doc.selection;
  if (!sel) return out;
  const m = doc.selectionMask;
  for (let y = sel.y0; y < sel.y1; y++) {
    if (!m) out.fill(255, y * doc.width + sel.x0, y * doc.width + sel.x1);
    else out.set(m.subarray((y - sel.y0) * sel.width, (y - sel.y0 + 1) * sel.width), y * doc.width + sel.x0);
  }
  return out;
}

/** [rectangle, mask] for a whole-canvas mask: no mask when it is a plain rectangle. */
function normalise(doc: EditorDoc, full: Full): [Rect | null, Uint8Array | null] {
  const w = doc.width;
  let [x0, y0, x1, y1] = [w, doc.height, 0, 0];
  for (let y = 0; y < doc.height; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) {
      if (!full[row + x]) continue;
      if (x < x0) x0 = x;
      if (x + 1 > x1) x1 = x + 1;
      if (y < y0) y0 = y;
      y1 = y + 1;
    }
  }
  if (x1 <= x0) return [null, null];
  const rect = new Rect(x0, y0, x1, y1);
  const part = new Uint8Array(rect.width * rect.height);
  let all = true;
  for (let y = y0; y < y1; y++) {
    const src = full.subarray(y * w + x0, y * w + x1);
    part.set(src, (y - y0) * rect.width);
    if (all) for (const v of src) if (v !== 255) { all = false; break; }
  }
  return [rect, all ? null : part];
}

const sameMask = (a: Uint8Array | null | undefined, b: Uint8Array | null | undefined) =>
  !a || !b ? !a && !b : a.length === b.length && a.every((v, i) => v === b[i]);

/** Make (rect, mask) the selection; one undo step (null when nothing changed). */
export function setSelection(doc: EditorDoc, rect: Rect | null, mask: Uint8Array | null, label: string): Command | null {
  const before: [Rect | null, Uint8Array | null] = [doc.selection, doc.selectionMask ?? null];
  if (rect && rect.empty) [rect, mask] = [null, null];
  const sameRect = before[0] === rect || (!!before[0] && !!rect && before[0].equals(rect));
  if (sameRect && sameMask(before[1], mask)) return null;
  doc.selection = rect;
  doc.selectionMask = mask;
  return new SelectionCommand(before[0], rect, label, before[1], mask);
}

export function fromFull(doc: EditorDoc, full: Full, label: string): Command | null {
  return setSelection(doc, ...normalise(doc, full), label);
}

/** Combine a whole-canvas `shape` with the selection. */
export function combine(doc: EditorDoc, shape: Full, mode: Mode, label = "Select"): Command | null {
  if (mode === "replace") return fromFull(doc, shape, shape.some((v) => v) ? label : "Deselect");
  const cur = fullMask(doc);
  const out = new Uint8Array(cur.length);
  for (let i = 0; i < cur.length; i++) {
    const a = cur[i];
    const b = shape[i];
    out[i] = mode === "add" ? Math.max(a, b) : mode === "subtract" ? Math.floor((a * (255 - b)) / 255) : Math.floor((a * b) / 255);
  }
  return fromFull(doc, out, label);
}

// ------------------------------------------------------------------ shapes

export function rectShape(doc: EditorDoc, rect: Rect): Full {
  const out = new Uint8Array(doc.width * doc.height);
  const r = rect.intersect(docBounds(doc));
  for (let y = r.y0; y < r.y1; y++) out.fill(255, y * doc.width + r.x0, y * doc.width + r.x1);
  return out;
}

/** Pixels whose centre is inside the ellipse that fits `rect`. */
export function ellipseShape(doc: EditorDoc, rect: Rect): Full {
  const out = new Uint8Array(doc.width * doc.height);
  const rx = rect.width / 2;
  const ry = rect.height / 2;
  if (rx <= 0 || ry <= 0) return out;
  const cx = rect.x0 + rx;
  const cy = rect.y0 + ry;
  const r = rect.intersect(docBounds(doc));
  for (let y = r.y0; y < r.y1; y++) {
    const dy = (y + 0.5 - cy) / ry;
    if (Math.abs(dy) > 1) continue;
    const half = rx * Math.sqrt(1 - dy * dy);
    const x0 = Math.max(r.x0, Math.ceil(cx - half - 0.5));
    const x1 = Math.min(r.x1, Math.floor(cx + half - 0.5) + 1);
    if (x1 > x0) out.fill(255, y * doc.width + x0, y * doc.width + x1);
  }
  return out;
}

/** Pixels whose centre is inside the polygon (even-odd rule). */
export function polygonShape(doc: EditorDoc, points: [number, number][]): Full {
  const out = new Uint8Array(doc.width * doc.height);
  if (points.length < 3) return out;
  const n = points.length;
  for (let y = 0; y < doc.height; y++) {
    const yc = y + 0.5;
    const xs: number[] = [];
    for (let i = 0; i < n; i++) {
      const [ax, ay] = points[i];
      const [bx, by] = points[(i + 1) % n];
      if (ay <= yc !== by <= yc) xs.push(ax + ((yc - ay) / (by - ay)) * (bx - ax));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const x0 = Math.max(0, Math.ceil(xs[k] - 0.5));
      const x1 = Math.min(doc.width, Math.floor(xs[k + 1] - 0.5) + 1);
      if (x1 > x0) out.fill(255, y * doc.width + x0, y * doc.width + x1);
    }
  }
  return out;
}

/** Pixels with a colour like the one at (x, y): touching it (`contiguous`) or anywhere. */
export function wandShape(doc: EditorDoc, x: number, y: number, tolerance: number, contiguous = true, sampleAll = false): Full {
  const w = doc.width;
  const h = doc.height;
  const out = new Uint8Array(w * h);
  const px = Math.floor(x);
  const py = Math.floor(y);
  if (px < 0 || py < 0 || px >= w || py >= h) return out;
  let src: Uint8ClampedArray;
  if (sampleAll) src = composite(doc);
  else {
    src = new Uint8ClampedArray(w * h * 4);
    const l = doc.layers[doc.active];
    const part = new Rect(l.x, l.y, l.x + l.width, l.y + l.height).intersect(docBounds(doc));
    for (let yy = part.y0; yy < part.y1; yy++) src.set(l.data.subarray(((yy - l.y) * l.width + (part.x0 - l.x)) * 4, ((yy - l.y) * l.width + (part.x1 - l.x)) * 4), (yy * w + part.x0) * 4);
  }
  const cand = candidatesOf(src, w, 0, 0, w, h, (py * w + px) * 4, tolerance);
  const hit = contiguous ? floodMask(cand, w, h, px, py).mask : cand;
  for (let i = 0; i < hit.length; i++) if (hit[i]) out[i] = 255;
  return out;
}

// ------------------------------------------------------------------ menu actions

export const selectAll = (doc: EditorDoc) => setSelection(doc, docBounds(doc), null, "Select all");
export const deselect = (doc: EditorDoc) => setSelection(doc, null, null, "Deselect");

export function invert(doc: EditorDoc): Command | null {
  const full = fullMask(doc);
  for (let i = 0; i < full.length; i++) full[i] = 255 - full[i];
  return fromFull(doc, full, "Invert selection");
}

/** Soften the selection's edge by `radius` px (a Gaussian blur of the mask). */
export function feather(doc: EditorDoc, radius: number): Command | null {
  if (!doc.selection || radius <= 0) return null;
  const full = fullMask(doc);
  const rgba = new Uint8ClampedArray(full.length * 4);
  for (let i = 0; i < full.length; i++) rgba[i * 4 + 3] = full[i];
  const soft = blurData(rgba, doc.width, doc.height, radius);
  for (let i = 0; i < full.length; i++) full[i] = soft[i * 4 + 3];
  return fromFull(doc, full, "Feather selection");
}

// ------------------------------------------------------------------ for tools and drawing

/** How much each pixel of `rect` (canvas coordinates, inside the selection rectangle) is
 *  selected, 0…1 (rect.width × rect.height); null = all of it (no mask). */
export function weight(doc: EditorDoc, rect: Rect): Float32Array | null {
  const sel = doc.selection;
  const m = doc.selectionMask;
  if (!sel || !m) return null;
  const out = new Float32Array(rect.width * rect.height);
  for (let y = 0; y < rect.height; y++) {
    const sy = rect.y0 + y - sel.y0;
    for (let x = 0; x < rect.width; x++) {
      const sx = rect.x0 + x - sel.x0;
      out[y * rect.width + x] = sx >= 0 && sy >= 0 && sx < sel.width && sy < sel.height ? m[sy * sel.width + sx] / 255 : 0;
    }
  }
  return out;
}

/** True when canvas point (x, y) is inside the selected area (at least half selected). */
export function contains(doc: EditorDoc, x: number, y: number): boolean {
  const sel = doc.selection;
  if (!sel) return false;
  const px = Math.floor(x);
  const py = Math.floor(y);
  if (!sel.contains(px, py)) return false;
  const m = doc.selectionMask;
  return !m || m[(py - sel.y0) * sel.width + (px - sel.x0)] >= 128;
}

/** (rect, mask) moved by (dx, dy) and trimmed to the canvas. Same as shifted in selection.py. */
export function shifted(doc: EditorDoc, rect: Rect, mask: Uint8Array | null, dx: number, dy: number): [Rect | null, Uint8Array | null] {
  const moved = rect.offset(dx, dy);
  const vis = moved.intersect(new Rect(0, 0, doc.width, doc.height));
  if (vis.empty) return [null, null];
  if (!mask) return [vis, null];
  // the visible part, then its bounding box of selected pixels
  let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1;
  for (let y = vis.y0; y < vis.y1; y++)
    for (let x = vis.x0; x < vis.x1; x++)
      if (mask[(y - moved.y0) * rect.width + (x - moved.x0)]) {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x + 1); y1 = Math.max(y1, y + 1);
      }
  if (x1 < 0) return [null, null];
  const out = new Rect(x0, y0, x1, y1);
  const part = new Uint8Array(out.width * out.height);
  let full = true;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const v = mask[(y - moved.y0) * rect.width + (x - moved.x0)];
      part[(y - y0) * out.width + (x - x0)] = v;
      if (v !== 255) full = false;
    }
  return [out, full ? null : part];
}

/** Move the selection outline (not the pixels) by (dx, dy); one undo step. `base` is the
 *  (rect, mask) it started from when a drag already moved it for the preview. */
export function moveSelection(doc: EditorDoc, dx: number, dy: number, base?: [Rect | null, Uint8Array | null], label = "Move selection"): Command | null {
  if (base) [doc.selection, doc.selectionMask] = base;
  if (!doc.selection || (dx === 0 && dy === 0)) return null;
  const [rect, mask] = shifted(doc, doc.selection, doc.selectionMask ?? null, dx, dy);
  return setSelection(doc, rect, mask, label);
}

/** The edges of the selected area (≥ half selected) as merged horizontal and vertical segments
 *  [x0, y0, x1, y1] in canvas coordinates — what the marching ants follow. */
/** Arrow keys: move the selection outline by (dx, dy). A run of nudges is one undo step,
 *  measured from where it started, so the canvas edge trims it only once. Same as
 *  nudge_selection in selection.py. Returns true when something changed. */
export function nudgeSelection(doc: EditorDoc, history: History, dx: number, dy: number): boolean {
  const last = history.last;
  const run = last instanceof SelectionCommand ? last.nudge : null;
  let base: [Rect | null, Uint8Array | null] = [doc.selection, doc.selectionMask ?? null];
  const after = last instanceof SelectionCommand ? last.after : undefined;
  const sameRect = doc.selection === after || (!!doc.selection && !!after && doc.selection.equals(after));
  if (run && last instanceof SelectionCommand && sameRect && (doc.selectionMask ?? null) === last.maskAfter) {
    base = run.base;
    history.undo();
    dx += run.dx;
    dy += run.dy;
  }
  const cmd = moveSelection(doc, dx, dy, base);
  if (cmd instanceof SelectionCommand) {
    cmd.nudge = { base, dx, dy };
    history.push(cmd);
  }
  return !!cmd || !!run;
}

export function outline(doc: EditorDoc): [number, number, number, number][] {
  const sel = doc.selection;
  if (!sel) return [];
  const m = doc.selectionMask;
  if (!m) {
    const { x0, y0, x1, y1 } = sel;
    return [[x0, y0, x1, y0], [x0, y1, x1, y1], [x0, y0, x0, y1], [x1, y0, x1, y1]];
  }
  const w = sel.width;
  const h = sel.height;
  const on = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && m[y * w + x] >= 128;
  const segs: [number, number, number, number][] = [];
  for (let y = 0; y <= h; y++) {
    let start = -1;
    for (let x = 0; x <= w; x++) {
      const edge = x < w && on(x, y) !== on(x, y - 1);
      if (edge && start < 0) start = x;
      if (!edge && start >= 0) {
        segs.push([sel.x0 + start, sel.y0 + y, sel.x0 + x, sel.y0 + y]);
        start = -1;
      }
    }
  }
  for (let x = 0; x <= w; x++) {
    let start = -1;
    for (let y = 0; y <= h; y++) {
      const edge = y < h && on(x, y) !== on(x - 1, y);
      if (edge && start < 0) start = y;
      if (!edge && start >= 0) {
        segs.push([sel.x0 + x, sel.y0 + start, sel.x0 + x, sel.y0 + y]);
        start = -1;
      }
    }
  }
  return segs;
}
