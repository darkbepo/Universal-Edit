// A new, empty picture for the image editor (started from the editor tiles, without a file).
// Same as blank_document in src/umc/editor/blank.py.
import { checkSize, type EditorDoc } from "./document";
import { makeLayer } from "./layer";
import { BACKGROUNDS, type Background } from "./presets";

/** One "Background" layer of `width` × `height` filled with `background`. */
export function blankDocument(width: number, height: number, background: Background = "white"): EditorDoc {
  checkSize(width, height);
  const data = new Uint8ClampedArray(width * height * 4);
  const [r, g, b, a] = BACKGROUNDS[background][1];
  if (a) {
    for (let i = 0; i < data.length; i += 4) {
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
      data[i + 3] = a;
    }
  }
  return { width, height, layers: [makeLayer("Background", width, height, data)], active: 0, selection: null, dirty: false };
}
