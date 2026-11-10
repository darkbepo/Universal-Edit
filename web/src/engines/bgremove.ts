// Background removal with @imgly/background-removal (AGPL-3.0, like this project) on ONNX Runtime
// Web (MIT). Runs inside the conversion worker. The model (IS-Net, fp16, ~80 MB) and the ONNX
// Runtime files come from IMG.LY's CDN the first time and are then cached by the browser.
import { ConversionError } from "../core/types";

/** One byte per pixel: 255 where the main subject is, 0 behind it. */
export async function subjectMask(png: Uint8Array, width: number, height: number, progress: (fraction: number, message: string) => void): Promise<Uint8Array> {
  let imgly: typeof import("@imgly/background-removal");
  try {
    imgly = await import("@imgly/background-removal");
  } catch (e) {
    throw new ConversionError("Couldn't load background removal.", String(e));
  }
  let blob: Blob;
  try {
    blob = await imgly.segmentForeground(new Blob([png as Uint8Array<ArrayBuffer>], { type: "image/png" }), {
      model: "isnet_fp16",
      output: { format: "image/x-alpha8" },
      progress: (key, current, total) => {
        if (key.startsWith("fetch")) progress(total ? current / total : 0, "Downloading the background-removal model (first time only)…");
        else progress(0.99, "Finding the subject…");
      },
    });
  } catch (e) {
    throw new ConversionError("Couldn't download the background-removal model. Check your internet connection and try again.", String((e as Error)?.stack ?? e));
  }
  const alpha = new Uint8Array(await blob.arrayBuffer());
  if (alpha.length !== width * height) throw new ConversionError("Background removal gave a mask of the wrong size.", `${alpha.length} != ${width}×${height}`);
  return alpha;
}
