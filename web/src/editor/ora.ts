// OpenRaster (.ora): a ZIP of PNG layers plus stack.xml — the same format the desktop app writes
// (src/umc/editor/ora.py) and GIMP / Krita read.
import { anchorOf, liveTextLayer, makeSpec, type TextSpec } from "./tools/text";
import { canvasPoints, shapeLayer, type ShapeSpec } from "./tools/shapes";
import { fxFromDict } from "./layerfx";
import { strFromU8, strToU8, unzipSync, zipSync, type Zippable } from "fflate";
import { checkSize, composite, EditorError, type EditorDoc } from "./document";
import { layerBounds, layerPixels, makeLayer, type Layer } from "./layer";
import type { PngCodec, Rasterizer } from "./raster";

/** A text / shape layer drawn again from its recipe (null when it has none). */
function redrawn(layer: Layer, doc: EditorDoc, raster: Rasterizer): Layer | null {
  if (layer.shape) return shapeLayer(raster, layer.name, { ...(layer.shape as ShapeSpec), points: canvasPoints(layer) }, layer);
  if (layer.text) return liveTextLayer(doc, raster, layer.text as TextSpec, ...anchorOf(layer), layer);
  return null;
}

const same = (a: Layer, b: Layer) =>
  a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height && a.data.every((v, i) => v === b.data[i]);

/** Nearly the same pixels: another app's anti-aliasing or the browser's PNG rounding, not
 *  painting. At most 2 % of the drawn pixels may differ by more than a little (as ora.py). */
function close(a: Layer, b: Layer): boolean {
  const box = layerBounds(a).union(layerBounds(b));
  const planes = [a, b].map((l) => {
    const out = new Uint8ClampedArray(box.width * box.height * 4);
    for (let y = 0; y < l.height; y++) out.set(l.data.subarray(y * l.width * 4, (y + 1) * l.width * 4), ((l.y - box.y0 + y) * box.width + (l.x - box.x0)) * 4);
    return out;
  });
  let bad = 0, drawnA = 0, drawnB = 0;
  for (let i = 0; i < planes[0].length; i += 4) {
    let d = 0;
    for (let c = 0; c < 4; c++) d = Math.max(d, Math.abs(planes[0][i + c] - planes[1][i + c]));
    if (d > 96) bad++;
    if (planes[0][i + 3]) drawnA++;
    if (planes[1][i + 3]) drawnB++;
  }
  return bad <= Math.max(4, Math.floor(Math.max(drawnA, drawnB) / 50));
}

/** Save a text / shape recipe only while the pixels are still exactly what it draws (and there is
 *  no mask, which the file bakes into the pixels). Without a rasterizer: always (unit tests). */
function keepsRecipe(layer: Layer, doc: EditorDoc, raster?: Rasterizer): boolean {
  if (!layer.text && !layer.shape) return false;
  if (!raster) return true;
  if (layer.mask) return false;
  try {
    const again = redrawn(layer, doc, raster);
    return !!again && same(again, layer);
  } catch {
    return false;
  }
}

/** A text / shape layer read from a file: drawn again here when the stored pixels match (so it
 *  stays editable, even if the other app drew it), else plain pixels. */
function fromRecipe(layer: Layer, doc: EditorDoc, raster: Rasterizer): Layer {
  try {
    const again = redrawn(layer, doc, raster);
    if (again && close(again, layer)) return again;
  } catch {
    /* fall through: keep the pixels */
  }
  return { ...layer, text: null, shape: null };
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const unesc = (s: string) =>
  s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&");

/** Box-filter downscale so the longer side is at most `max`. */
export function thumbnail(data: Uint8ClampedArray, w: number, h: number, max = 256): { data: Uint8ClampedArray; width: number; height: number } {
  const scale = Math.min(1, max / Math.max(w, h));
  const tw = Math.max(1, Math.round(w * scale));
  const th = Math.max(1, Math.round(h * scale));
  if (tw === w && th === h) return { data, width: w, height: h };
  const out = new Uint8ClampedArray(tw * th * 4);
  for (let ty = 0; ty < th; ty++) {
    const y0 = Math.floor((ty * h) / th);
    const y1 = Math.max(y0 + 1, Math.floor(((ty + 1) * h) / th));
    for (let tx = 0; tx < tw; tx++) {
      const x0 = Math.floor((tx * w) / tw);
      const x1 = Math.max(x0 + 1, Math.floor(((tx + 1) * w) / tw));
      const sum = [0, 0, 0, 0];
      for (let y = y0; y < y1; y++)
        for (let x = x0; x < x1; x++) for (let c = 0; c < 4; c++) sum[c] += data[(y * w + x) * 4 + c];
      const n = (y1 - y0) * (x1 - x0);
      for (let c = 0; c < 4; c++) out[(ty * tw + tx) * 4 + c] = sum[c] / n;
    }
  }
  return { data: out, width: tw, height: th };
}

/** A small preview of a layer by point-sampling (3 × 3 per thumbnail pixel) — no layer copy,
 *  cost independent of the layer size, so it can run after every stroke. */
export function sampleThumbnail(layer: { width: number; height: number; data: Uint8ClampedArray }, max = 40): { data: Uint8ClampedArray; width: number; height: number } {
  const { width: w, height: h, data } = layer;
  const scale = Math.min(max / w, max / h, 1);
  const tw = Math.max(1, Math.round(w * scale));
  const th = Math.max(1, Math.round(h * scale));
  const out = new Uint8ClampedArray(tw * th * 4);
  for (let ty = 0; ty < th; ty++)
    for (let tx = 0; tx < tw; tx++) {
      const sum = [0, 0, 0, 0];
      for (let sy = 0; sy < 3; sy++)
        for (let sx = 0; sx < 3; sx++) {
          const x = Math.min(w - 1, Math.floor(((tx + (sx + 0.5) / 3) * w) / tw));
          const y = Math.min(h - 1, Math.floor(((ty + (sy + 0.5) / 3) * h) / th));
          const i = (y * w + x) * 4;
          for (let c = 0; c < 4; c++) sum[c] += data[i + c];
        }
      for (let c = 0; c < 4; c++) out[(ty * tw + tx) * 4 + c] = sum[c] / 9 + 0.5;
    }
  return { data: out, width: tw, height: th };
}

export async function writeOra(doc: EditorDoc, png: PngCodec, raster?: Rasterizer): Promise<Uint8Array> {
  const files: Zippable = { mimetype: [strToU8("image/openraster"), { level: 0 }] };
  const entries: string[] = [];
  for (let i = doc.layers.length - 1; i >= 0; i--) {
    const l = doc.layers[i];
    const src = `data/layer${i}.png`;
    files[src] = [await png.encode(layerPixels(l), l.width, l.height), { level: 0 }]; // ORA has no masks: baked in
    const keep = keepsRecipe(l, doc, raster);
    entries.push(
      `<layer name="${esc(l.name)}" src="${src}" x="${l.x}" y="${l.y}" opacity="${l.opacity.toFixed(4)}" visibility="${l.visible ? "visible" : "hidden"}"${i === doc.active ? ' selected="true"' : ""}${keep && l.text ? ` umc-text="${esc(JSON.stringify(l.text))}"` : ""}${keep && l.shape ? ` umc-shape="${esc(JSON.stringify(l.shape))}"` : ""}${l.fx ? ` umc-fx="${esc(JSON.stringify(l.fx))}"` : ""}/>`, // our extras: text / shapes stay editable, effects stay live
    );
  }
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<image version="0.0.3" w="${doc.width}" h="${doc.height}"><stack>${entries.join("")}</stack></image>`;
  const merged = composite(doc);
  const thumb = thumbnail(merged, doc.width, doc.height);
  files["stack.xml"] = strToU8(xml);
  files["mergedimage.png"] = [await png.encode(merged, doc.width, doc.height), { level: 0 }];
  files["Thumbnails/thumbnail.png"] = [await png.encode(thumb.data, thumb.width, thumb.height), { level: 0 }];
  return zipSync(files);
}

function attrs(tag: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of tag.matchAll(/([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) out[m[1]] = unesc(m[3] ?? m[4] ?? "");
  return out;
}

/** A shape layer's recipe saved by either app (JSON of ShapeSpec); null if absent or damaged. */
function parseShape(raw: string | undefined): unknown {
  if (!raw) return null;
  try {
    const t = JSON.parse(raw);
    const ok = t && typeof t === "object" && ["line", "arrow", "rect", "ellipse"].includes(t.kind) && Array.isArray(t.points) && t.points.length === 4;
    return ok ? { color: [0, 0, 0, 255], fill: [255, 255, 255, 255], width: 4, mode: "outline", heads: "end", opacity: 100, ...t } : null;
  } catch {
    return null; // keep the pixels, lose the editing
  }
}

/** A text layer's recipe saved by either app (JSON of TextSpec); null if absent or damaged. */
function parseText(raw: string | undefined): unknown {
  if (!raw) return null;
  try {
    const t = JSON.parse(raw);
    // fill in anything missing (an older or foreign file) so drawing it again can't fail
    return t && typeof t === "object" && typeof t.text === "string" ? makeSpec(t.text, t) : null;
  } catch {
    return null; // keep the pixels, lose the editing
  }
}

function parseFx(raw: string | undefined) {
  if (!raw) return null;
  try {
    return fxFromDict(JSON.parse(raw));
  } catch {
    return null; // the layer without its effects
  }
}

/** `raster` (the page passes it) re-draws text / shape layers so they stay editable. */
export async function readOra(bytes: Uint8Array, png: PngCodec, name = "file", raster?: Rasterizer): Promise<EditorDoc> {
  let files: Record<string, Uint8Array>;
  let xml: string;
  try {
    files = unzipSync(bytes);
    xml = strFromU8(files["stack.xml"]);
  } catch {
    throw new EditorError(`Couldn't open “${name}” as an OpenRaster file.`);
  }
  const image = attrs(xml.match(/<image\b[^>]*>/)?.[0] ?? "");
  const width = Math.round(+image.w || 0);
  const height = Math.round(+image.h || 0);
  checkSize(width, height);
  const layers: Layer[] = [];
  let active: Layer | null = null;
  // Layer elements in document order are top-first; nested stacks are simply flattened.
  for (const m of xml.matchAll(/<layer\b[^>]*>/g)) {
    const a = attrs(m[0]);
    const src = a.src ?? "";
    if (!src.toLowerCase().endsWith(".png") || !files[src]) continue;
    const img = await png.decode(files[src]);
    const layer = makeLayer(a.name ?? src.replace(/^.*\/|\.png$/gi, ""), img.width, img.height, img.data, {
      x: Math.trunc(+(a.x ?? 0) || 0),
      y: Math.trunc(+(a.y ?? 0) || 0),
      opacity: Math.max(0, Math.min(1, a.opacity === undefined ? 1 : +a.opacity || 0)),
      visible: a.visibility !== "hidden",
      text: parseText(a["umc-text"]),
      shape: parseShape(a["umc-shape"]),
      fx: parseFx(a["umc-fx"]),
    });
    const fixed = raster && (layer.text || layer.shape) ? fromRecipe(layer, { width, height, layers: [], active: 0, selection: null, dirty: false }, raster) : layer;
    if (a.selected === "true") active = fixed;
    layers.push(fixed);
  }
  if (!layers.length) throw new EditorError(`“${name}” has no layers.`);
  layers.reverse(); // ours: bottom first
  const idx = active ? layers.indexOf(active) : layers.length - 1;
  return { width, height, layers, active: idx, selection: null, dirty: false };
}
