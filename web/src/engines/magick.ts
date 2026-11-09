// ImageMagick compiled to WebAssembly. Loaded once per worker, on first use.
import * as Magick from "@imagemagick/magick-wasm";
import wasmURL from "@imagemagick/magick-wasm/magick.wasm?url";

let ready: Promise<typeof Magick> | null = null;

export function getMagick(onStatus?: (msg: string) => void): Promise<typeof Magick> {
  if (!ready) {
    ready = (async () => {
      onStatus?.("Loading image engine…");
      const bytes = new Uint8Array(await (await fetch(wasmURL)).arrayBuffer());
      await Magick.initializeImageMagick(bytes);
      return Magick;
    })();
    ready.catch(() => (ready = null));
  }
  return ready;
}

/** Copy the bytes out of a magick write() callback (its buffer is only valid inside it). */
export const copy = (data: Uint8Array) => new Uint8Array(data);
