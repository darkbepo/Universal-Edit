// Open the files the user added as one layered document (port of src/umc/editor/io.py).
import { checkSize, EditorError, type EditorDoc } from "./document";
import { makeLayer, type Layer } from "./layer";
import { readOra } from "./ora";
import type { Decoded, PngCodec } from "./raster";

export interface OpenSource {
  name: string;
  read(): Promise<Uint8Array>;
  /** Pixels of the image (the page decodes in the browser or via the conversion worker). */
  decode(): Promise<Decoded>;
}

const stem = (name: string) => name.replace(/\.[^.]+$/, "") || name;

/** Layers for `sources` (first at the bottom), the largest size, and plain-language errors. */
export async function loadLayers(sources: OpenSource[], png: PngCodec): Promise<{ layers: Layer[]; width: number; height: number; errors: string[] }> {
  const layers: Layer[] = [];
  const errors: string[] = [];
  let width = 0;
  let height = 0;
  for (const s of sources) {
    try {
      if (s.name.toLowerCase().endsWith(".ora")) {
        const doc = await readOra(await s.read(), png, s.name);
        layers.push(...doc.layers);
        width = Math.max(width, doc.width);
        height = Math.max(height, doc.height);
        continue;
      }
      const img = await s.decode();
      checkSize(img.width, img.height);
      layers.push(makeLayer(stem(s.name), img.width, img.height, img.data));
      width = Math.max(width, img.width);
      height = Math.max(height, img.height);
    } catch (e) {
      console.warn("editor: couldn't open", s.name, e);
      const msg = e instanceof EditorError ? e.message : "";
      errors.push(msg.includes(s.name) ? msg : `Couldn't open “${s.name}”${msg ? `: ${msg}` : " as an image."}`);
    }
  }
  return { layers, width, height, errors };
}

export async function openFiles(sources: OpenSource[], png: PngCodec): Promise<{ doc: EditorDoc; errors: string[] }> {
  const { layers, width, height, errors } = await loadLayers(sources, png);
  if (!layers.length) throw new EditorError("None of the files could be opened as an image." + (errors.length === 1 ? ` ${errors[0]}` : ""));
  checkSize(width, height);
  return { doc: { width, height, layers, active: layers.length - 1, selection: null, dirty: false }, errors };
}
