// Copy, cut and paste (no DOM): port of src/umc/editor/clipboard.py. A copy remembers where it
// came from on the canvas, so pasting it back inside the editor puts it in the same place; an
// image from another program has no position and is centred where the user is looking.
import { checkSize, composite, docBounds, type EditorDoc } from "./document";
import { Rect } from "./geometry";
import { PatchCommand, type Command } from "./history";
import { cropData, layerBounds, makeLayer } from "./layer";
import * as ops from "./ops";
import { weight } from "./selection";
import { restrict } from "./tools/base";

export interface Clip {
  data: Uint8ClampedArray; // RGBA
  width: number;
  height: number;
  x: number | null; // canvas position it was copied from (null = from another program)
  y: number | null;
}

/** The selected part of the active layer (or the whole layer without a selection).
 *  `merged`: what you see — all visible layers flattened — inside the selection or canvas. */
export function copy(doc: EditorDoc, merged = false): Clip | null {
  if (merged) {
    const region = doc.selection ? doc.selection.intersect(docBounds(doc)) : docBounds(doc);
    if (region.empty) return null;
    return { data: shaped(doc, composite(doc, region), region), width: region.width, height: region.height, x: region.x0, y: region.y0 };
  }
  const layer = doc.layers[doc.active];
  const region = doc.selection ? layerBounds(layer).intersect(doc.selection) : layerBounds(layer);
  if (region.empty) return null;
  const data = shaped(doc, cropData(layer.data, layer.width, region.offset(-layer.x, -layer.y)), region);
  return { data, width: region.width, height: region.height, x: region.x0, y: region.y0 };
}

/** `data` (of canvas `region`) with the selection's shape taken into its alpha. */
function shaped(doc: EditorDoc, data: Uint8ClampedArray, region: Rect): Uint8ClampedArray {
  const w = weight(doc, region);
  if (!w) return data;
  for (let i = 0; i < w.length; i++) data[i * 4 + 3] = data[i * 4 + 3] * w[i];
  return data;
}

/** Copy, then make that part of the active layer transparent (one undo step). */
export function cut(doc: EditorDoc): [Clip | null, Command | null] {
  const clip = copy(doc);
  if (!clip || clip.x === null || clip.y === null) return [null, null];
  const layer = doc.layers[doc.active];
  const region = new Rect(clip.x, clip.y, clip.x + clip.width, clip.y + clip.height).offset(-layer.x, -layer.y);
  const before = cropData(layer.data, layer.width, region);
  for (let y = region.y0; y < region.y1; y++) layer.data.fill(0, (y * layer.width + region.x0) * 4, (y * layer.width + region.x1) * 4);
  restrict(doc, layer, region, before); // shaped / soft selections clear only that much
  return [clip, PatchCommand.fromRegion("Cut", doc, doc.active, region, before)];
}

/** `clip` as a new layer above the active one, selected so it can be moved straight away. It goes
 *  back where it was copied from; a clip without a position is centred on `center`. */
export function paste(doc: EditorDoc, clip: Clip, center?: [number, number]): Command {
  checkSize(clip.width, clip.height);
  let x: number;
  let y: number;
  if (clip.x !== null && clip.y !== null) [x, y] = [clip.x, clip.y];
  else {
    const [cx, cy] = center ?? [doc.width / 2, doc.height / 2];
    x = Math.round(cx - clip.width / 2);
    y = Math.round(cy - clip.height / 2);
  }
  const layer = makeLayer(ops.uniqueName(doc, "Pasted"), clip.width, clip.height, new Uint8ClampedArray(clip.data), { x, y });
  return ops.addLayers(doc, [layer], "Paste") as Command;
}
