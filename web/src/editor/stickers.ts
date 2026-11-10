// Built-in stickers (CC0, made for this app): stickers.json is generated from
// src/umc/editor/stickers.py (tests on both sides check they match).
import { type EditorDoc } from "./document";
import type { Command } from "./history";
import { makeLayer, type RGBA } from "./layer";
import { Rect } from "./geometry";
import { addLayers, uniqueName } from "./ops";
import type { DrawItem, Rasterizer } from "./raster";
import data from "./stickers.json";

type Colour = string; // "front" | "back" | "#rrggbb"
interface Shape {
  poly?: number[][];
  ellipse?: number[];
  line?: number[][];
  fill?: Colour;
  stroke?: Colour;
  width?: number;
}
export interface Sticker {
  label: string;
  shapes: Shape[];
}

export const STICKERS: Record<string, Sticker> = Object.fromEntries(Object.entries(data).filter(([k]) => k !== "_note")) as Record<string, Sticker>;

function colour(c: Colour, front: RGBA, back: RGBA): RGBA {
  if (c === "front") return front;
  if (c === "back") return back;
  const v = c.replace("#", "");
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16), 255];
}

/** The drawing for sticker `name` at `size` × `size` px. */
export function stickerItems(name: string, size: number, front: RGBA, back: RGBA): DrawItem[] {
  const k = size / 100;
  const pts = (p: number[][]) => p.map(([x, y]) => [x * k, y * k]);
  return STICKERS[name].shapes.map((sh): DrawItem => {
    const fill = sh.fill ? colour(sh.fill, front, back) : undefined;
    const stroke = sh.stroke ? colour(sh.stroke, front, back) : undefined;
    const width = stroke ? Math.max(1, Math.round((sh.width ?? 0) * k)) : 0;
    if (sh.line) return { line: pts(sh.line), stroke: stroke as RGBA, width };
    if (sh.poly) return { poly: pts(sh.poly), fill, stroke, width };
    const [x0, y0, x1, y1] = (sh.ellipse as number[]).map((v) => v * k);
    return { ellipse: [x0, y0, x1, y1], fill, stroke, width };
  });
}

export function renderSticker(raster: Rasterizer, name: string, size: number, front: RGBA = [0, 0, 0, 255], back: RGBA = [255, 255, 255, 255]): Uint8ClampedArray {
  return raster.draw(new Rect(0, 0, size, size), stickerItems(name, size, front, back));
}

/** Sticker `name` as a new layer centred on `center` (canvas coordinates). */
export function addSticker(doc: EditorDoc, raster: Rasterizer, name: string, size: number, center: [number, number], front: RGBA = [0, 0, 0, 255], back: RGBA = [255, 255, 255, 255]): Command | null {
  const s = Math.max(8, Math.round(size));
  const layer = makeLayer(uniqueName(doc, STICKERS[name].label), s, s, renderSticker(raster, name, s, front, back), {
    x: Math.round(center[0] - s / 2),
    y: Math.round(center[1] - s / 2),
  });
  return addLayers(doc, [layer], "Sticker");
}
