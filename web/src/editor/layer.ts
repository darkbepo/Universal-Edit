// A layer: an RGBA pixel array placed on the canvas. Layers are treated as immutable by
// structural commands (see history.ts) — change one with withProps(), which keeps `data`.
import { Rect } from "./geometry";

export type RGBA = [number, number, number, number];

export interface Layer {
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly data: Uint8ClampedArray;
  readonly x: number;
  readonly y: number;
  readonly opacity: number;
  readonly visible: boolean;
  /** Layer mask in the same RGBA layout as `data` (grey in R/G/B: black hides, white shows),
   *  so crop / resize / rotate / flip reuse the pixel routines; null / absent = no mask. */
  readonly mask?: Uint8ClampedArray | null;
}

/** Test hook: the largest crop made (in pixels) — lets tests prove strokes don't copy whole layers. */
export const __stats = { largestCrop: 0 };

export function makeLayer(name: string, width: number, height: number, data?: Uint8ClampedArray, extra: Partial<Layer> = {}): Layer {
  return { name, width, height, data: data ?? new Uint8ClampedArray(width * height * 4), x: 0, y: 0, opacity: 1, visible: true, ...extra };
}

export function withProps(layer: Layer, patch: Partial<Layer>): Layer {
  return { ...layer, ...patch };
}

export function copyLayer(layer: Layer): Layer {
  return { ...layer, data: new Uint8ClampedArray(layer.data), mask: layer.mask ? new Uint8ClampedArray(layer.mask) : null };
}

/** A mask (RGBA layout) of `width` × `height`, all `value` (255 = shows everything). */
export function solidMask(width: number, height: number, value = 255): Uint8ClampedArray {
  return solid(width, height, [value, value, value, 255]);
}

/** What the layer shows: its pixels with the mask applied to alpha (a new array). */
export function layerPixels(layer: Layer): Uint8ClampedArray {
  const out = new Uint8ClampedArray(layer.data);
  const m = layer.mask;
  if (m) for (let i = 3; i < out.length; i += 4) out[i] = (out[i] * m[i - 3]) / 255;
  return out;
}

export function layerBounds(layer: Layer): Rect {
  return new Rect(layer.x, layer.y, layer.x + layer.width, layer.y + layer.height);
}

export function solid(width: number, height: number, color: readonly number[]): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) data.set(color, i);
  return data;
}

/** Copy `rect` (in the array's own coordinates) out of an RGBA array of width `w`. */
export function cropData(data: Uint8ClampedArray, w: number, rect: Rect): Uint8ClampedArray {
  const out = new Uint8ClampedArray(rect.width * rect.height * 4);
  __stats.largestCrop = Math.max(__stats.largestCrop, rect.width * rect.height);
  const row = rect.width * 4;
  for (let y = 0; y < rect.height; y++) {
    const start = ((rect.y0 + y) * w + rect.x0) * 4;
    out.set(data.subarray(start, start + row), y * row);
  }
  return out;
}

/** Write `src` (rect.width × rect.height) into `dst` (width `dstW`) at rect's position. */
export function pasteData(dst: Uint8ClampedArray, dstW: number, src: Uint8ClampedArray, rect: Rect): void {
  const row = rect.width * 4;
  for (let y = 0; y < rect.height; y++) {
    dst.set(src.subarray(y * row, y * row + row), ((rect.y0 + y) * dstW + rect.x0) * 4);
  }
}
