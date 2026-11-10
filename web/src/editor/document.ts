// The editor document: canvas size plus a stack of layers (index 0 = bottom).
// Port of src/umc/editor/document.py. No DOM here — runs in node tests too.
import { Rect } from "./geometry";
import { layerBounds, makeLayer, type Layer } from "./layer";

export const MAX_SIDE = 16_000;
export const MAX_PIXELS = 250_000_000;
export const TOO_LARGE = "This image is too large to edit (max 16 000 px per side).";

export class EditorError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EditorError";
  }
}

export interface EditorDoc {
  width: number;
  height: number;
  layers: Layer[];
  active: number;
  /** The rectangle around what is selected (canvas coordinates); see selection.ts. */
  selection: Rect | null;
  /** How much of each pixel in that rectangle is selected (0…255, row by row); null / absent =
   *  all of it, a plain rectangle selection. */
  selectionMask?: Uint8Array | null;
  dirty: boolean;
  /** Layers selected in the layers list (Ctrl / Shift+click). The active layer always counts as
   *  selected — it's the one tools paint on. Not an undo step by itself. */
  selected?: number[];
}

/** Selected layer indices, bottom first; always includes the active layer. */
export function selectedIndices(doc: EditorDoc): number[] {
  const n = doc.layers.length;
  const set = new Set((doc.selected ?? []).filter((i) => i >= 0 && i < n));
  set.add(doc.active);
  return [...set].sort((a, b) => a - b);
}

export function checkSize(width: number, height: number): void {
  if (width < 1 || height < 1) throw new EditorError("The image size must be at least 1 × 1 pixel.");
  if (width > MAX_SIDE || height > MAX_SIDE || width * height > MAX_PIXELS) throw new EditorError(TOO_LARGE);
}

export function newDoc(width: number, height: number): EditorDoc {
  checkSize(width, height);
  return { width, height, layers: [makeLayer("Background", width, height)], active: 0, selection: null, dirty: false };
}

export const docBounds = (doc: EditorDoc) => new Rect(0, 0, doc.width, doc.height);

/** Flatten the visible layers into RGBA for `rect` (default: the whole canvas). */
/** The area the editor shows: the canvas plus the parts of visible layers lying around it (the
 *  "pasteboard" — shown, never saved). At most half the longer side past each edge; `step` > 1
 *  rounds the overhang up to whole steps. Same as Document.pasteboard in document.py. */
/** Pixels the canvas + pasteboard may cover: the view caches the whole area, and browsers (iOS
 *  Safari above all) refuse canvases much bigger. The desktop allows 40 MP. */
export const PASTEBOARD_BUDGET = 16_000_000;

/** How far the pasteboard may reach past each edge: half the longer side, but at most what keeps
 *  (w + 2m) × (h + 2m) within `budget`. Same as pasteboard_margin in document.py. */
export function pasteboardMargin(w: number, h: number, budget: number): number {
  if (budget <= w * h) return 0;
  const m = (-(w + h) + Math.sqrt((w + h) ** 2 - 4 * (w * h - budget))) / 4;
  return Math.max(0, Math.min(Math.floor(Math.max(w, h) / 2), Math.floor(m)));
}

export function pasteboard(doc: EditorDoc, step = 1, budget = PASTEBOARD_BUDGET): Rect {
  const bounds = docBounds(doc);
  let out = bounds;
  const m = pasteboardMargin(doc.width, doc.height, budget);
  const limit = new Rect(-m, -m, doc.width + m, doc.height + m);
  for (const l of doc.layers) if (l.visible && l.opacity > 0) out = out.union(layerBounds(l).intersect(limit));
  if (step > 1 && !out.equals(bounds)) {
    const up = (v: number) => (v > 0 ? Math.ceil(v / step) * step : 0);
    out = new Rect(-up(-out.x0), -up(-out.y0), doc.width + up(out.x1 - doc.width), doc.height + up(out.y1 - doc.height)).intersect(limit);
  }
  return out;
}

/** Flatten the visible layers. `clip = false` lets `rect` reach past the canvas (the pasteboard). */
export function composite(doc: EditorDoc, rect?: Rect, override?: Map<number, Uint8ClampedArray>, clip = true): Uint8ClampedArray {
  const r = rect && !clip ? rect : (rect ?? docBounds(doc)).intersect(docBounds(doc));
  const out = new Uint8ClampedArray(r.width * r.height * 4);
  if (r.empty) return out;
  doc.layers.forEach((layer, i) => {
    if (!layer.visible || layer.opacity <= 0) return;
    const part = layerBounds(layer).intersect(r);
    if (part.empty) return;
    const src = override?.get(i) ?? layer.data;
    const op = layer.opacity;
    const mask = layer.mask && layer.mask.length === src.length ? layer.mask : null;
    for (let y = part.y0; y < part.y1; y++) {
      let si = ((y - layer.y) * layer.width + (part.x0 - layer.x)) * 4;
      let di = ((y - r.y0) * r.width + (part.x0 - r.x0)) * 4;
      for (let x = part.x0; x < part.x1; x++, si += 4, di += 4) {
        const sa = (src[si + 3] / 255) * op * (mask ? mask[si] / 255 : 1);
        if (sa <= 0) continue;
        const da = out[di + 3] / 255;
        const oa = sa + da * (1 - sa);
        const k = da * (1 - sa);
        out[di] = (src[si] * sa + out[di] * k) / oa;
        out[di + 1] = (src[si + 1] * sa + out[di + 1] * k) / oa;
        out[di + 2] = (src[si + 2] * sa + out[di + 2] * k) / oa;
        out[di + 3] = oa * 255;
      }
    }
  });
  return out;
}
