// Whole-image operations, layer-list operations and adjustments (port of src/umc/editor/ops.py).
// Each applies its change and returns the Command to push (null when nothing changed). Layers
// are replaced, never changed in place — see history.ts.
import { checkSize, docBounds, type EditorDoc } from "./document";
import { Rect } from "./geometry";
import { DocStateCommand, LayerListCommand, PatchCommand, type Command } from "./history";
import { copyLayer, cropData, layerBounds, makeLayer, withProps, type Layer } from "./layer";
import { clipRect, overPixel } from "./tools/base";

export type Resample = "lanczos" | "bilinear" | "nearest";
export type Resizer = (data: Uint8ClampedArray, w: number, h: number, nw: number, nh: number, method: Resample) => Uint8ClampedArray;

export const ANCHORS = ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"] as const;
export type Anchor = (typeof ANCHORS)[number];

/** Pure-JS resampling: nearest; area average when shrinking; bilinear when enlarging.
 *  (The page passes a Canvas-based high-quality resizer for "lanczos".) */
export const jsResize: Resizer = (src, w, h, nw, nh, method) => {
  const out = new Uint8ClampedArray(nw * nh * 4);
  if (method === "nearest") {
    for (let y = 0; y < nh; y++) {
      const sy = Math.min(h - 1, Math.floor(((y + 0.5) * h) / nh));
      for (let x = 0; x < nw; x++) {
        const sx = Math.min(w - 1, Math.floor(((x + 0.5) * w) / nw));
        out.set(src.subarray((sy * w + sx) * 4, (sy * w + sx) * 4 + 4), (y * nw + x) * 4);
      }
    }
    return out;
  }
  const premul = (i: number, c: number) => (c === 3 ? src[i + 3] : (src[i + c] * src[i + 3]) / 255);
  const acc = [0, 0, 0, 0];
  for (let y = 0; y < nh; y++)
    for (let x = 0; x < nw; x++) {
      acc.fill(0);
      if (nw <= w && nh <= h) {
        const y0 = Math.floor((y * h) / nh);
        const y1 = Math.max(y0 + 1, Math.floor(((y + 1) * h) / nh));
        const x0 = Math.floor((x * w) / nw);
        const x1 = Math.max(x0 + 1, Math.floor(((x + 1) * w) / nw));
        for (let sy = y0; sy < y1; sy++) for (let sx = x0; sx < x1; sx++) for (let c = 0; c < 4; c++) acc[c] += premul((sy * w + sx) * 4, c);
        const n = (y1 - y0) * (x1 - x0);
        for (let c = 0; c < 4; c++) acc[c] /= n;
      } else {
        const fy = Math.max(0, Math.min(h - 1, ((y + 0.5) * h) / nh - 0.5));
        const fx = Math.max(0, Math.min(w - 1, ((x + 0.5) * w) / nw - 0.5));
        const y0 = Math.floor(fy);
        const x0 = Math.floor(fx);
        const y1 = Math.min(h - 1, y0 + 1);
        const x1 = Math.min(w - 1, x0 + 1);
        const ty = fy - y0;
        const tx = fx - x0;
        for (let c = 0; c < 4; c++) {
          const top = premul((y0 * w + x0) * 4, c) * (1 - tx) + premul((y0 * w + x1) * 4, c) * tx;
          const bot = premul((y1 * w + x0) * 4, c) * (1 - tx) + premul((y1 * w + x1) * 4, c) * tx;
          acc[c] = top * (1 - ty) + bot * ty;
        }
      }
      const i = (y * nw + x) * 4;
      const a = acc[3];
      out[i + 3] = a + 0.5;
      for (let c = 0; c < 3; c++) out[i + c] = a > 0 ? (acc[c] * 255) / a + 0.5 : 0;
    }
  return out;
};

function setState(doc: EditorDoc, label: string, width: number, height: number, layers: Layer[]): Command {
  const before: [number, number, Layer[]] = [doc.width, doc.height, [...doc.layers]];
  doc.width = width;
  doc.height = height;
  doc.layers = layers;
  doc.active = Math.min(doc.active, layers.length - 1);
  doc.selection = null;
  return new DocStateCommand(label, before, [width, height, [...layers]]);
}

// ------------------------------------------------------------------ pixel transforms

/** Rotate an RGBA array clockwise by 90 / 180 / 270. */
function rotateData(src: Uint8ClampedArray, w: number, h: number, deg: 90 | 180 | 270): [Uint8ClampedArray, number, number] {
  const [nw, nh] = deg === 180 ? [w, h] : [h, w];
  const out = new Uint8ClampedArray(src.length);
  const s32 = new Uint32Array(src.buffer, src.byteOffset, w * h);
  const o32 = new Uint32Array(out.buffer);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      let nx: number;
      let ny: number;
      if (deg === 90) [nx, ny] = [h - 1 - y, x];
      else if (deg === 180) [nx, ny] = [w - 1 - x, h - 1 - y];
      else [nx, ny] = [y, w - 1 - x];
      o32[ny * nw + nx] = s32[y * w + x];
    }
  return [out, nw, nh];
}

function flipData(src: Uint8ClampedArray, w: number, h: number, horizontal: boolean): Uint8ClampedArray {
  const out = new Uint8ClampedArray(src.length);
  const s32 = new Uint32Array(src.buffer, src.byteOffset, w * h);
  const o32 = new Uint32Array(out.buffer);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) o32[horizontal ? y * w + (w - 1 - x) : (h - 1 - y) * w + x] = s32[y * w + x];
  return out;
}

// ------------------------------------------------------------------ canvas

export function crop(doc: EditorDoc, rect: Rect): Command | null {
  const r = rect.intersect(docBounds(doc));
  if (r.empty || r.equals(docBounds(doc))) return null;
  const canvas = new Rect(0, 0, r.width, r.height);
  const layers = doc.layers.map((l) => {
    const shifted = layerBounds(l).offset(-r.x0, -r.y0);
    const keep = shifted.intersect(canvas);
    if (keep.empty) return withProps(l, { data: new Uint8ClampedArray(4), width: 1, height: 1, x: 0, y: 0 });
    const data = cropData(l.data, l.width, keep.offset(-shifted.x0, -shifted.y0));
    return withProps(l, { data, width: keep.width, height: keep.height, x: keep.x0, y: keep.y0 });
  });
  return setState(doc, "Crop", r.width, r.height, layers);
}

export function resizeImage(doc: EditorDoc, width: number, height: number, resample: Resample = "lanczos", resizer: Resizer = jsResize): Command | null {
  checkSize(width, height);
  if (width === doc.width && height === doc.height) return null;
  const sx = width / doc.width;
  const sy = height / doc.height;
  const layers = doc.layers.map((l) => {
    const nw = Math.max(1, Math.round(l.width * sx));
    const nh = Math.max(1, Math.round(l.height * sy));
    return withProps(l, { data: resizer(l.data, l.width, l.height, nw, nh, resample), width: nw, height: nh, x: Math.round(l.x * sx), y: Math.round(l.y * sy) });
  });
  return setState(doc, "Resize image", width, height, layers);
}

export function canvasSize(doc: EditorDoc, width: number, height: number, anchor: Anchor = "center"): Command | null {
  checkSize(width, height);
  if (width === doc.width && height === doc.height) return null;
  const idx = ANCHORS.indexOf(anchor) >= 0 ? ANCHORS.indexOf(anchor) : 4;
  const row = Math.floor(idx / 3);
  const col = idx % 3;
  const dx = [0, Math.floor((width - doc.width) / 2), width - doc.width][col];
  const dy = [0, Math.floor((height - doc.height) / 2), height - doc.height][row];
  return setState(doc, "Canvas size", width, height, doc.layers.map((l) => withProps(l, { x: l.x + dx, y: l.y + dy })));
}

/** Rotate clockwise by 90, 180 or 270 degrees. */
export function rotate(doc: EditorDoc, degrees: number, layerOnly = false): Command | null {
  const deg = ((degrees % 360) + 360) % 360;
  if (deg !== 90 && deg !== 180 && deg !== 270) return null;
  if (layerOnly) {
    const l = doc.layers[doc.active];
    const [data, nw, nh] = rotateData(l.data, l.width, l.height, deg);
    const x = l.x + Math.floor((l.width - nw) / 2);
    const y = l.y + Math.floor((l.height - nh) / 2);
    return replace(doc, doc.active, `Rotate layer ${deg}°`, withProps(l, { data, width: nw, height: nh, x, y }));
  }
  const { width: w, height: h } = doc;
  const layers = doc.layers.map((l) => {
    const [data, nw, nh] = rotateData(l.data, l.width, l.height, deg);
    const [x, y] = deg === 90 ? [h - (l.y + l.height), l.x] : deg === 180 ? [w - (l.x + l.width), h - (l.y + l.height)] : [l.y, w - (l.x + l.width)];
    return withProps(l, { data, width: nw, height: nh, x, y });
  });
  const [nw, nh] = deg === 180 ? [w, h] : [h, w];
  return setState(doc, `Rotate ${deg}°`, nw, nh, layers);
}

export function flip(doc: EditorDoc, horizontal: boolean, layerOnly = false): Command | null {
  const label = horizontal ? "Flip horizontal" : "Flip vertical";
  if (layerOnly) {
    const l = doc.layers[doc.active];
    return replace(doc, doc.active, `${label} (layer)`, withProps(l, { data: flipData(l.data, l.width, l.height, horizontal) }));
  }
  const layers = doc.layers.map((l) =>
    withProps(l, {
      data: flipData(l.data, l.width, l.height, horizontal),
      x: horizontal ? doc.width - (l.x + l.width) : l.x,
      y: horizontal ? l.y : doc.height - (l.y + l.height),
    }),
  );
  return setState(doc, label, doc.width, doc.height, layers);
}

// ------------------------------------------------------------------ adjustments

/** Brightness and contrast, each −100…+100; alpha untouched. */
export function brightnessContrast(data: Uint8ClampedArray, brightness: number, contrast: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(data.length);
  const factor = contrast >= 0 ? (100 + contrast) / 100 : 1 + contrast / 100;
  const add = brightness * 2.55;
  for (let i = 0; i < data.length; i += 4) {
    for (let c = 0; c < 3; c++) out[i + c] = (data[i + c] + add - 128) * factor + 128 + 0.5;
    out[i + 3] = data[i + 3];
  }
  return out;
}

export function applyBrightnessContrast(doc: EditorDoc, brightness: number, contrast: number): Command | null {
  if (brightness === 0 && contrast === 0) return null;
  const l = doc.layers[doc.active];
  const region = clipRect(doc, l);
  if (region.empty) return null;
  const before = cropData(l.data, l.width, region);
  const after = brightnessContrast(before, brightness, contrast);
  for (let y = 0; y < region.height; y++) l.data.set(after.subarray(y * region.width * 4, (y + 1) * region.width * 4), ((region.y0 + y) * l.width + region.x0) * 4);
  return PatchCommand.fromRegion("Brightness / contrast", doc, doc.active, region, before);
}

/** Preview helper: the active layer's pixels with brightness / contrast inside the selection. */
export function previewBrightnessContrast(doc: EditorDoc, brightness: number, contrast: number): Uint8ClampedArray {
  const l = doc.layers[doc.active];
  const out = new Uint8ClampedArray(l.data);
  const region = clipRect(doc, l);
  if (region.empty) return out;
  const part = brightnessContrast(cropData(l.data, l.width, region), brightness, contrast);
  for (let y = 0; y < region.height; y++) out.set(part.subarray(y * region.width * 4, (y + 1) * region.width * 4), ((region.y0 + y) * l.width + region.x0) * 4);
  return out;
}

// ------------------------------------------------------------------ layer list

function change(doc: EditorDoc, label: string, layers: Layer[], active: number): Command {
  const before = [...doc.layers];
  const activeBefore = doc.active;
  doc.layers = layers;
  doc.active = active;
  return new LayerListCommand(label, before, layers, activeBefore, active);
}

function replace(doc: EditorDoc, index: number, label: string, layer: Layer): Command {
  const layers = [...doc.layers];
  layers[index] = layer;
  return change(doc, label, layers, doc.active);
}

function uniqueName(doc: EditorDoc, base: string): string {
  const names = new Set(doc.layers.map((l) => l.name));
  let n = doc.layers.length + 1;
  while (names.has(`${base} ${n}`)) n++;
  return `${base} ${n}`;
}

export function addLayer(doc: EditorDoc): Command {
  const layers = [...doc.layers];
  layers.splice(doc.active + 1, 0, makeLayer(uniqueName(doc, "Layer"), doc.width, doc.height));
  return change(doc, "Add layer", layers, doc.active + 1);
}

export function addLayers(doc: EditorDoc, added: Layer[], label = "Add images"): Command | null {
  if (!added.length) return null;
  const layers = [...doc.layers];
  layers.splice(doc.active + 1, 0, ...added);
  return change(doc, label, layers, doc.active + added.length);
}

export function duplicateLayer(doc: EditorDoc): Command {
  const layers = [...doc.layers];
  const copy = copyLayer(doc.layers[doc.active]);
  layers.splice(doc.active + 1, 0, withProps(copy, { name: `${copy.name} copy` }));
  return change(doc, "Duplicate layer", layers, doc.active + 1);
}

export function deleteLayer(doc: EditorDoc): Command | null {
  if (doc.layers.length <= 1) return null;
  const layers = [...doc.layers];
  layers.splice(doc.active, 1);
  return change(doc, "Delete layer", layers, Math.max(0, doc.active - 1));
}

export function mergeDown(doc: EditorDoc): Command | null {
  const i = doc.active;
  if (i === 0) return null;
  const lower = doc.layers[i - 1];
  const upper = doc.layers[i];
  const box = upper.visible ? layerBounds(lower).union(layerBounds(upper)) : layerBounds(lower);
  const data = new Uint8ClampedArray(box.width * box.height * 4);
  for (const l of upper.visible ? [lower, upper] : [lower]) {
    for (let y = 0; y < l.height; y++)
      for (let x = 0; x < l.width; x++) overPixel(data, ((l.y - box.y0 + y) * box.width + (l.x - box.x0 + x)) * 4, l.data, (y * l.width + x) * 4, l.opacity);
  }
  const merged = makeLayer(lower.name, box.width, box.height, data, { x: box.x0, y: box.y0, visible: lower.visible });
  const layers = [...doc.layers];
  layers.splice(i - 1, 2, merged);
  return change(doc, "Merge down", layers, i - 1);
}

export function moveLayer(doc: EditorDoc, delta: number): Command | null {
  const i = doc.active;
  const j = Math.max(0, Math.min(doc.layers.length - 1, i + delta));
  if (i === j) return null;
  const layers = [...doc.layers];
  layers.splice(j, 0, ...layers.splice(i, 1));
  return change(doc, "Move layer", layers, j);
}

/** Changing the active layer isn't an undo step. */
export function selectLayer(doc: EditorDoc, index: number): void {
  doc.active = Math.max(0, Math.min(doc.layers.length - 1, index));
}

export function renameLayer(doc: EditorDoc, index: number, name: string): Command | null {
  const n = name.trim();
  if (!n || n === doc.layers[index].name) return null;
  return replace(doc, index, "Rename layer", withProps(doc.layers[index], { name: n }));
}

export function setOpacity(doc: EditorDoc, index: number, opacity: number): Command | null {
  const o = Math.max(0, Math.min(1, opacity));
  if (Math.abs(o - doc.layers[index].opacity) < 1e-6) return null;
  return replace(doc, index, "Layer opacity", withProps(doc.layers[index], { opacity: o }));
}

export function setVisible(doc: EditorDoc, index: number, visible: boolean): Command | null {
  if (doc.layers[index].visible === visible) return null;
  return replace(doc, index, visible ? "Show layer" : "Hide layer", withProps(doc.layers[index], { visible }));
}
