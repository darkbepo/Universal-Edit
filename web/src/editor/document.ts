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
  selection: Rect | null;
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
export function composite(doc: EditorDoc, rect?: Rect, override?: Map<number, Uint8ClampedArray>): Uint8ClampedArray {
  const r = (rect ?? docBounds(doc)).intersect(docBounds(doc));
  const out = new Uint8ClampedArray(r.width * r.height * 4);
  if (r.empty) return out;
  doc.layers.forEach((layer, i) => {
    if (!layer.visible || layer.opacity <= 0) return;
    const part = layerBounds(layer).intersect(r);
    if (part.empty) return;
    const src = override?.get(i) ?? layer.data;
    const op = layer.opacity;
    for (let y = part.y0; y < part.y1; y++) {
      let si = ((y - layer.y) * layer.width + (part.x0 - layer.x)) * 4;
      let di = ((y - r.y0) * r.width + (part.x0 - r.x0)) * 4;
      for (let x = part.x0; x < part.x1; x++, si += 4, di += 4) {
        const sa = (src[si + 3] / 255) * op;
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
