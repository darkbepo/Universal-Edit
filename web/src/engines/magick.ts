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

// Camera RAW files are TIFF-like inside, so ImageMagick must be told the format explicitly or it
// reads them as a plain TIFF (wrong colours) instead of developing them with LibRaw.
export const RAW_EXTS = new Set([
  "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "orf", "raf", "rw2", "pef",
  "srw", "x3f", "3fr", "iiq", "mrw", "kdc", "dcr", "erf", "mef", "mos",
]);

type IMagickImage = import("@imagemagick/magick-wasm").IMagickImage;
type IMagickImageCollection = import("@imagemagick/magick-wasm").IMagickImageCollection;
type Fmt = import("@imagemagick/magick-wasm").MagickFormat;

/** Read one image (RAW developed; PSD/layered files give their flattened composite). */
export function readImage<T>(M: typeof Magick, bytes: Uint8Array, ext: string, cb: (img: IMagickImage) => T): T {
  if (RAW_EXTS.has(ext)) return M.ImageMagick.read(bytes, ext.toUpperCase() as Fmt, cb);
  return M.ImageMagick.read(bytes, cb);
}

/** Read all frames (animations). PSD layers are not frames: only the composite is kept. */
export function readFrames<T>(M: typeof Magick, bytes: Uint8Array, ext: string, cb: (imgs: IMagickImageCollection) => T): T {
  if (RAW_EXTS.has(ext)) return M.ImageMagick.readCollection(bytes, ext.toUpperCase() as Fmt, cb);
  return M.ImageMagick.readCollection(bytes, (imgs) => {
    if (ext === "psd") while (imgs.length > 1) imgs.pop()?.dispose();
    return cb(imgs);
  });
}

/** Copy the bytes out of a magick write() callback (its buffer is only valid inside it). */
export const copy = (data: Uint8Array) => new Uint8Array(data);
