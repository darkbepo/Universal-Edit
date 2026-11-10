// Background removal for the image editor (never offered on the convert page: no targets).
// Input: one PNG; output: the subject mask, one byte per pixel ("mask.raw").
import type { Converter } from "../core/types";
import { ConversionError } from "../core/types";

export const MASK_TARGET = { id: "mask", label: "Subject mask", ext: "raw", group: "", converterId: "bgremove" };

function pngSize(data: Uint8Array): [number, number] {
  const v = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (data.length < 24 || v.getUint32(12) !== 0x49484452) throw new ConversionError("Not a PNG picture.");
  return [v.getUint32(16), v.getUint32(20)];
}

export const bgremoveConverter: Converter = {
  id: "bgremove",
  family: "image",
  options: [],
  targets: () => [],
  async run(files, _target, _options, ctx) {
    const { subjectMask } = await import("../engines/bgremove");
    const png = new Uint8Array(await files[0].file.arrayBuffer());
    const [w, h] = pngSize(png);
    const alpha = await subjectMask(png, w, h, (f, m) => ctx.progress(f, m));
    return [{ name: "mask.raw", data: alpha, type: "application/octet-stream" }];
  },
};
