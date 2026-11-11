/**
 * Picture sizes for the DOCX writer: python-docx's image header parsers (PNG, JPEG with
 * JFIF / Exif, GIF, BMP) and its `scaled_dimensions`, so a picture gets the same size in EMU
 * as on the desktop. Anything else (TIFF, WebP, broken data) is `null`: left out, as there.
 */

/** Python's round(): halves go to the even neighbour. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export interface ImageInfo {
  ext: string;
  type: string;
  width: number;
  height: number;
  dpiX: number;
  dpiY: number;
}

const ascii = (d: Uint8Array, at: number, n: number) =>
  String.fromCharCode(...Array.from(d.subarray(at, at + n)));

export function imageInfo(d: Uint8Array): ImageInfo | null {
  const v = new DataView(d.buffer, d.byteOffset, d.byteLength);
  if (d.length >= 24 && ascii(d, 0, 8) === "\x89PNG\r\n\x1a\n" && ascii(d, 12, 4) === "IHDR") {
    let dpiX = 72;
    let dpiY = 72;
    for (let at = 8; at + 8 <= d.length;) {
      const len = v.getUint32(at);
      const type = ascii(d, at + 4, 4);
      if (type === "IEND") break;
      if (type === "pHYs" && at + 17 <= d.length && d[at + 16] === 1) {
        const x = v.getUint32(at + 8);
        const y = v.getUint32(at + 12);
        dpiX = x ? pyRound(x * 0.0254) : 72;
        dpiY = y ? pyRound(y * 0.0254) : 72;
      }
      at += 12 + len;
    }
    return {
      ext: "png",
      type: "image/png",
      width: v.getUint32(16),
      height: v.getUint32(20),
      dpiX,
      dpiY,
    };
  }
  if (d.length > 10 && (ascii(d, 0, 6) === "GIF87a" || ascii(d, 0, 6) === "GIF89a")) {
    const [width, height] = [v.getUint16(6, true), v.getUint16(8, true)];
    return { ext: "gif", type: "image/gif", width, height, dpiX: 72, dpiY: 72 };
  }
  if (d.length >= 0x2e && ascii(d, 0, 2) === "BM") {
    const dpi = (ppm: number) => (ppm === 0 ? 96 : pyRound(ppm * 0.0254));
    const [width, height] = [v.getUint32(0x12, true), v.getUint32(0x16, true)];
    const [dpiX, dpiY] = [dpi(v.getUint32(0x26, true)), dpi(v.getUint32(0x2a, true))];
    return { ext: "bmp", type: "image/bmp", width, height, dpiX, dpiY };
  }
  const kind = d.length > 10 && d[0] === 0xff && d[1] === 0xd8 ? ascii(d, 6, 4) : "";
  if (kind === "JFIF" || kind === "Exif") return jpegInfo(d, v, kind === "JFIF");
  return null; // TIFF and WebP are left out too (python-docx can't read WebP)
}

const SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);

function jpegInfo(d: Uint8Array, v: DataView, jfif: boolean): ImageInfo | null {
  let size: [number, number] | null = null;
  let dpi: [number, number] | null = jfif ? null : [72, 72]; // Exif: python-docx reads its TIFF tags; 72 is their default
  for (let at = 2; at + 4 <= d.length && !(size && dpi);) {
    if (d[at] !== 0xff) return null;
    const code = d[at + 1];
    if (code === 0xff) {
      at += 1; // fill byte
      continue;
    }
    if (code === 0xd8 || code === 0x01 || (code >= 0xd0 && code <= 0xd7)) {
      at += 2;
      continue;
    }
    if (code === 0xd9 || code === 0xda) break;
    const len = v.getUint16(at + 2);
    if (SOF.has(code) && at + 9 <= d.length) size = [v.getUint16(at + 7), v.getUint16(at + 5)];
    if (code === 0xe0 && jfif && !dpi && at + 16 <= d.length) {
      const units = d[at + 11];
      const conv = (n: number) => (units === 1 ? n : units === 2 ? pyRound(n * 2.54) : 72);
      dpi = [conv(v.getUint16(at + 12)), conv(v.getUint16(at + 14))];
    }
    at += 2 + len;
  }
  if (!size || !dpi) return null;
  return {
    ext: "jpg",
    type: "image/jpeg",
    width: size[0],
    height: size[1],
    dpiX: dpi[0],
    dpiY: dpi[1],
  };
}

/** (cx, cy) in EMU: python-docx's `Image.scaled_dimensions`. */
export function extent(
  info: ImageInfo,
  width: number | null,
  height: number | null,
): [number, number] {
  const nativeW = Math.trunc((info.width / info.dpiX) * 914400);
  const nativeH = Math.trunc((info.height / info.dpiY) * 914400);
  if (width === null && height === null) return [nativeW, nativeH];
  if (width === null) return [pyRound(nativeW * ((height as number) / nativeH)), height as number];
  if (height === null) return [width, pyRound(nativeH * (width / nativeW))];
  return [width, height];
}
