// Text — port of src/umc/editor/tools/text.py. Typed in an on-canvas box (page) and kept on its
// own layer that stays editable: a text layer remembers how it was made (`Layer.text`), so a
// click with the Text tool (or "Edit text…" in the layer menu) opens it again. Styles: alignment,
// an outline and a background box in the back colour, a drop shadow, and a watermark (the text
// repeated diagonally across the whole picture, faint).
import { opt } from "../../core/options";
import type { EditorDoc } from "../document";
import type { Rect } from "../geometry";
import { LayerListCommand, type Command } from "../history";
import { layerBounds, makeLayer, withProps, type Layer, type RGBA } from "../layer";
import * as ops from "../ops";
import type { Decoded, Rasterizer } from "../raster";
import { blurData } from "../filters";
import { Tool, type ToolContext } from "./base";

export const WATERMARK_OPACITY = 0.3;
export const WATERMARK_ANGLE = 30; // degrees, rising to the right

/** Everything needed to draw a text layer again (same fields as the desktop's TextSpec). */
export interface TextSpec {
  text: string;
  font: string;
  size: number;
  bold: boolean;
  italic: boolean;
  color: RGBA;
  align: "left" | "center" | "right";
  outline: number;
  outline_color: RGBA;
  shadow: boolean;
  box: boolean;
  box_color: RGBA;
  watermark: boolean;
  /** Where the clicked point lies inside the drawn image, so re-drawing keeps the text in place. */
  ox: number;
  oy: number;
}

export function makeSpec(text: string, p: Partial<TextSpec> = {}): TextSpec {
  return { text, font: "DejaVu Sans", size: 48, bold: false, italic: false, color: [0, 0, 0, 255], align: "left", outline: 0, outline_color: [255, 255, 255, 255], shadow: false, box: false, box_color: [255, 255, 255, 255], watermark: false, ox: 0, oy: 0, ...p };
}

function over(dst: Uint8ClampedArray, dw: number, src: Decoded, x0: number, y0: number): void {
  for (let y = 0; y < src.height; y++) {
    for (let x = 0; x < src.width; x++) {
      const dx = x0 + x;
      const dy = y0 + y;
      if (dx < 0 || dy < 0 || dx >= dw || dy * dw * 4 >= dst.length) continue;
      const si = (y * src.width + x) * 4;
      const sa = src.data[si + 3] / 255;
      if (sa <= 0) continue;
      const di = (dy * dw + dx) * 4;
      const da = dst[di + 3] / 255;
      const oa = sa + da * (1 - sa);
      for (let c = 0; c < 3; c++) dst[di + c] = (src.data[si + c] * sa + dst[di + c] * da * (1 - sa)) / oa;
      dst[di + 3] = oa * 255;
    }
  }
}

/** `src` turned by `deg` degrees (counter-clockwise, like Pillow), on a canvas that fits it. */
export function turn(src: Decoded, deg: number): Decoded {
  const a = (deg * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  // "- 1e-6": at 90° the cosine is 6e-17, not 0 — don't round that up to a whole extra pixel
  const w = Math.ceil(Math.abs(src.width * cos) + Math.abs(src.height * sin) - 1e-6);
  const h = Math.ceil(Math.abs(src.width * sin) + Math.abs(src.height * cos) - 1e-6);
  const out = new Uint8ClampedArray(w * h * 4);
  const cx = src.width / 2;
  const cy = src.height / 2;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const rx = x + 0.5 - w / 2;
      const ry = y + 0.5 - h / 2;
      const sx = Math.floor(rx * cos - ry * sin + cx);
      const sy = Math.floor(rx * sin + ry * cos + cy);
      if (sx < 0 || sy < 0 || sx >= src.width || sy >= src.height) continue;
      out.set(src.data.subarray((sy * src.width + sx) * 4, (sy * src.width + sx) * 4 + 4), (y * w + x) * 4);
    }
  }
  return { data: out, width: w, height: h };
}

/** [image, ox, oy] for `spec`: the clicked point sits at (ox, oy) inside the image. A watermark
 *  is canvas-sized and placed at the canvas origin. */
export function renderSpec(raster: Rasterizer, spec: TextSpec, canvas: [number, number]): [Decoded, number, number] {
  const glyphs = raster.text(spec.text, { family: spec.font, size: spec.size, bold: spec.bold, italic: spec.italic, align: spec.align, outline: spec.outline, outlineColor: spec.outline_color }, spec.color);
  if (spec.watermark) return [watermark(glyphs, canvas), 0, 0];
  const pad = spec.box ? Math.round(spec.size * 0.3) : 0;
  const shadow = spec.shadow ? Math.max(2, Math.round(spec.size * 0.06)) : 0;
  const w = glyphs.width + 2 * pad + 3 * shadow;
  const h = glyphs.height + 2 * pad + 3 * shadow;
  const out = new Uint8ClampedArray(w * h * 4);
  const gx = pad + shadow;
  const gy = pad + shadow;
  if (spec.box) {
    const r = Math.max(2, Math.floor(pad / 2));
    const [bx0, by0, bx1, by1] = [shadow, shadow, shadow + glyphs.width + 2 * pad, shadow + glyphs.height + 2 * pad];
    for (let y = by0; y < by1; y++)
      for (let x = bx0; x < bx1; x++) {
        const qx = Math.max(bx0 + r - (x + 0.5), 0, x + 0.5 - (bx1 - r));
        const qy = Math.max(by0 + r - (y + 0.5), 0, y + 0.5 - (by1 - r));
        if (qx * qx + qy * qy <= r * r) out.set(spec.box_color, (y * w + x) * 4); // rounded corners
      }
  }
  if (spec.shadow) {
    const shade = new Uint8ClampedArray(w * h * 4);
    for (let y = 0; y < glyphs.height; y++)
      for (let x = 0; x < glyphs.width; x++) shade[((gy + shadow + y) * w + gx + shadow + x) * 4 + 3] = (glyphs.data[(y * glyphs.width + x) * 4 + 3] * 6) / 10;
    over(out, w, { data: blurData(shade, w, h, shadow), width: w, height: h }, 0, 0);
  }
  over(out, w, glyphs, gx, gy);
  return [{ data: out, width: w, height: h }, gx, gy];
}

function watermark(glyphs: Decoded, [w, h]: [number, number]): Decoded {
  const tile = turn(glyphs, WATERMARK_ANGLE);
  const out = new Uint8ClampedArray(w * h * 4);
  const stepX = Math.max(1, tile.width + glyphs.height * 2);
  const stepY = Math.max(1, tile.height + glyphs.height);
  let row = 0;
  for (let y = -Math.floor(tile.height / 2); y < h; y += stepY, row++) {
    const shift = Math.floor(stepX / 2) * (row % 2);
    for (let x = -Math.floor(tile.width / 2) + shift; x < w; x += stepX) over(out, w, tile, x, y);
  }
  return { data: out, width: w, height: h };
}

const autoName = (spec: TextSpec) => spec.text.trim().split("\n")[0].slice(0, 30);

function textLayer(doc: EditorDoc, raster: Rasterizer, spec: TextSpec, x: number, y: number): Layer {
  const [img, ox, oy] = renderSpec(raster, spec, [doc.width, doc.height]);
  const s: TextSpec = { ...spec, ox, oy };
  const name = autoName(spec);
  if (spec.watermark) return makeLayer(name, img.width, img.height, img.data, { opacity: WATERMARK_OPACITY, text: s });
  return makeLayer(name, img.width, img.height, img.data, { x: Math.floor(x) - ox, y: Math.floor(y) - oy, text: s });
}

/** A new text layer above the active one. */
export function addTextLayer(doc: EditorDoc, raster: Rasterizer, spec: TextSpec, x: number, y: number): Command | null {
  if (!spec.text.trim()) return null;
  const layer = textLayer(doc, raster, spec, x, y);
  const before = [...doc.layers];
  const activeBefore = doc.active;
  const layers = [...doc.layers];
  layers.splice(doc.active + 1, 0, layer);
  doc.layers = layers;
  doc.active += 1;
  const selectedBefore = [...(doc.selected ?? [])];
  doc.selected = [];
  return new LayerListCommand("Text", before, layers, activeBefore, doc.active, selectedBefore, []);
}

/** Where a text layer's text was placed (its clicked point), for editing it again. */
export function anchorOf(layer: Layer): [number, number] {
  const s = layer.text as TextSpec | null | undefined;
  return s ? [layer.x + s.ox, layer.y + s.oy] : [layer.x, layer.y];
}

/** Draw text layer `index` again from `spec` (empty text deletes the layer). */
/** What a text layer shows before anything is typed (nothing; keeps its place in the list). */
export function placeholderLayer(x: number, y: number): Layer {
  return makeLayer("Text", 1, 1, undefined, { x: Math.floor(x), y: Math.floor(y) });
}

/** The layer for `spec` with its clicked point at (x, y), while it is being typed. Editing `old`
 *  keeps what the user set on it: a chosen name, visibility, opacity and mask. Same as
 *  live_text_layer in text.py. */
export function liveTextLayer(doc: EditorDoc, raster: Rasterizer, spec: TextSpec, x: number, y: number, old: Layer | null = null): Layer {
  if (!spec.text.trim()) return placeholderLayer(x, y);
  const fresh = textLayer(doc, raster, spec, x, y);
  if (!old) return fresh;
  const oldSpec = old.text as TextSpec | null | undefined;
  const renamed = !!oldSpec && old.name !== autoName(oldSpec);
  const keepMask = !!old.mask && fresh.width === old.width && fresh.height === old.height;
  return withProps(fresh, {
    name: renamed ? old.name : fresh.name,
    opacity: spec.watermark ? fresh.opacity : old.opacity,
    visible: old.visible,
    mask: keepMask ? old.mask : null,
    fx: old.fx,
  });
}

/** Draw text layer `index` again from `spec`, at `anchor` (default: where it was); empty text
 *  deletes the layer. */
export function editTextLayer(doc: EditorDoc, raster: Rasterizer, index: number, spec: TextSpec, anchor?: [number, number]): Command | null {
  const old = doc.layers[index];
  if (!spec.text.trim()) return ops.deleteLayers(doc, [index]);
  const [x, y] = anchor ?? anchorOf(old);
  return ops.replaceLayer(doc, index, "Edit text", liveTextLayer(doc, raster, spec, x, y, old));
}

/** True when text layer `index` was changed with other tools since it was drawn (editing the
 *  text draws it again, so those changes would go). Same as painted_over in text.py. */
export function paintedOver(doc: EditorDoc, raster: Rasterizer, index: number): boolean {
  const l = doc.layers[index];
  const spec = l.text as TextSpec | null | undefined;
  if (!spec) return false;
  const [img] = renderSpec(raster, spec, [doc.width, doc.height]);
  if (img.width !== l.width || img.height !== l.height) return true;
  for (let k = 0; k < img.data.length; k++) if (img.data[k] !== l.data[k]) return true;
  return false;
}

/** The topmost visible (non-watermark) text layer under (x, y), or null. */
export function textLayerAt(doc: EditorDoc, x: number, y: number): number | null {
  for (let i = doc.layers.length - 1; i >= 0; i--) {
    const l = doc.layers[i];
    const s = l.text as TextSpec | null | undefined;
    if (s && l.visible && !s.watermark && layerBounds(l).contains(Math.floor(x), Math.floor(y))) return i;
  }
  return null;
}

/** Click to place text (or click a text layer to edit it); the page shows the edit box. */
export class TextTool extends Tool {
  readonly id = "text";
  readonly label = "Text";
  readonly shortcut = "T";
  override readonly cursor = "text" as const;
  override readonly options = [
    opt.slider("size", "Size", 48, 6, 500, { unit: "px" }),
    opt.bool("bold", "Bold", false),
    opt.bool("italic", "Italic", false),
    opt.choice("align", "Align", "left", [["left", "Left"], ["center", "Centre"], ["right", "Right"]]),
    opt.slider("outline", "Outline (back colour)", 0, 0, 40, { unit: "px" }),
    opt.bool("shadow", "Drop shadow", false),
    opt.bool("box", "Box behind (back colour)", false),
    opt.bool("watermark", "Watermark (repeat across the picture)", false),
  ];
  anchor: [number, number] | null = null;

  override press(_doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.anchor = [x, y];
    return null;
  }
}
