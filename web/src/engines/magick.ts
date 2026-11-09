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

// Formats without a reliable signature: ImageMagick only recognises them from the file name,
// which it never sees here, so every ICO failed to open until the format was passed explicitly.
const NO_MAGIC_EXTS = new Set(["ico", "cur", "tga"]);

/** The explicit ImageMagick format for ``ext``, or null to let it sniff the bytes. */
export function formatHint(ext: string): string | null {
  return RAW_EXTS.has(ext) || NO_MAGIC_EXTS.has(ext) ? ext.toUpperCase() : null;
}

/** Read one image (RAW developed; PSD/layered files give their flattened composite). */
export function readImage<T>(M: typeof Magick, bytes: Uint8Array, ext: string, cb: (img: IMagickImage) => T): T {
  const hint = formatHint(ext);
  if (hint === "ICO" || hint === "CUR") return readFrames(M, bytes, ext, (imgs) => cb(imgs[0]));
  if (hint) return M.ImageMagick.read(bytes, hint as Fmt, cb);
  return M.ImageMagick.read(bytes, cb);
}

/** Read all frames (animations). PSD layers are not frames: only the composite is kept. */
export function readFrames<T>(M: typeof Magick, bytes: Uint8Array, ext: string, cb: (imgs: IMagickImageCollection) => T): T {
  const hint = formatHint(ext);
  if (hint === "ICO" || hint === "CUR") {
    // An icon's entries are sizes, not animation frames: keep the largest one.
    return M.ImageMagick.readCollection(bytes, hint as Fmt, (imgs) => {
      let best = 0;
      imgs.forEach((img, i) => { if (img.width * img.height > imgs[best].width * imgs[best].height) best = i; });
      for (let i = imgs.length - 1; i >= 0; i--) if (i !== best) imgs.splice(i, 1)[0]?.dispose();
      return cb(imgs);
    });
  }
  if (hint) return M.ImageMagick.readCollection(bytes, hint as Fmt, cb);
  return M.ImageMagick.readCollection(bytes, (imgs) => {
    if (ext === "psd") while (imgs.length > 1) imgs.pop()?.dispose();
    return cb(imgs);
  });
}

/** Copy the bytes out of a magick write() callback (its buffer is only valid inside it). */
export const copy = (data: Uint8Array) => new Uint8Array(data);
