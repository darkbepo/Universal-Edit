// Finding and replacing the EXIF block and the XMP packet inside JPEG, PNG and WebP files —
// a line-by-line port of the desktop's properties/containers.py (byte-identical output, checked
// by tests/fixtures/photo_edit_cases.json). The picture data is never touched.
import { unzlibSync } from "fflate";

const ascii = (s: string) => Uint8Array.from(s, (c) => c.charCodeAt(0));
export const EXIF_ID = ascii("Exif\0\0");
export const XMP_ID = ascii("http://ns.adobe.com/xap/1.0/\0");
const XMP_EXT_ID = ascii("http://ns.adobe.com/xmp/extension/\0");
const PNG_SIG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const PNG_XMP_KEY = ascii("XML:com.adobe.xmp");
export const KEEP = Symbol("keep the XMP packet");
export type XmpArg = string | null | typeof KEEP;
const MAX_JPEG_SEGMENT = 65533;
const PHOTOSHOP_ID = ascii("Photoshop 3.0\0");
const ICC_ID = ascii("ICC_PROFILE\0");
const ADOBE_ID = ascii("Adobe");
const MPF_ID = ascii("MPF\0");
// IPTC records that say where: sublocation, city, province, country code / name, location code
const IPTC_PLACES = new Set(["2:26", "2:27", "2:90", "2:92", "2:95", "2:100", "2:101"]);

export class ContainerError extends Error {}

const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { ignoreBOM: true });

class Out {
  private parts: Uint8Array[] = [];
  push(...p: Uint8Array[]) { this.parts.push(...p); }
  bytes(): Uint8Array {
    const out = new Uint8Array(this.parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of this.parts) { out.set(p, at); at += p.length; }
    return out;
  }
}

const startsWith = (data: Uint8Array, prefix: Uint8Array, at = 0) => {
  if (data.length - at < prefix.length) return false;
  for (let i = 0; i < prefix.length; i++) if (data[at + i] !== prefix[i]) return false;
  return true;
};
const same = (a: Uint8Array, s: string) => a.length === s.length && [...a].every((b, i) => b === s.charCodeAt(i));
const be16 = (n: number) => new Uint8Array([n >> 8, n & 0xff]);
const be32 = (n: number) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n); return b; };
const le32 = (n: number) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n, true); return b; };

export function kindOf(data: Uint8Array): "jpeg" | "png" | "webp" | null {
  if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return "jpeg";
  if (startsWith(data, PNG_SIG)) return "png";
  if (same(data.subarray(0, 4), "RIFF") && same(data.subarray(8, 12), "WEBP")) return "webp";
  return null;
}

// ---------------------------------------------------------------------------- JPEG
type Segment = [number, Uint8Array | null];

function jpegSegments(data: Uint8Array): [Segment[], Uint8Array] {
  if (data[0] !== 0xff || data[1] !== 0xd8) throw new ContainerError("not a JPEG");
  const segs: Segment[] = [];
  let p = 2;
  while (p < data.length) {
    if (data[p] !== 0xff) throw new ContainerError("damaged JPEG");
    while (p < data.length && data[p] === 0xff) p++;
    if (p >= data.length) break;
    const marker = data[p];
    p++;
    if (marker === 0xda || marker === 0xd9) return [segs, data.subarray(p - 2)];
    if ((marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
      segs.push([marker, null]);
      continue;
    }
    if (p + 2 > data.length) throw new ContainerError("damaged JPEG");
    const length = (data[p] << 8) | data[p + 1];
    if (length < 2 || p + length > data.length) throw new ContainerError("damaged JPEG");
    segs.push([marker, data.subarray(p + 2, p + length)]);
    p += length;
  }
  throw new ContainerError("damaged JPEG");
}

function jpegSegment(marker: number, payload: Uint8Array): Uint8Array {
  if (payload.length > MAX_JPEG_SEGMENT) throw new ContainerError("too many details to fit in a JPEG");
  const out = new Uint8Array(4 + payload.length);
  out.set([0xff, marker], 0);
  out.set(be16(payload.length + 2), 2);
  out.set(payload, 4);
  return out;
}

function jpegGet(data: Uint8Array, ident: Uint8Array): Uint8Array | null {
  const [segs] = jpegSegments(data);
  for (const [marker, payload] of segs) {
    if (marker === 0xe1 && payload !== null && startsWith(payload, ident)) return payload.subarray(ident.length);
  }
  return null;
}

const join = (a: Uint8Array, b: Uint8Array) => { const o = new Uint8Array(a.length + b.length); o.set(a); o.set(b, a.length); return o; };

/** Where the main picture ends (after its EOI) in ``rest`` (from the first SOS), or null. */
function jpegEnd(rest: Uint8Array): number | null {
  let p = 0;
  const n = rest.length;
  while (p + 1 < n) {
    if (rest[p] !== 0xff) return null;
    const m = rest[p + 1];
    if (m === 0xff) { p += 1; continue; }
    if (m === 0xd9) return p + 2;
    if ((m >= 0xd0 && m <= 0xd7) || m === 0x01) {
      p += 2;
    } else {
      if (p + 4 > n) return null;
      p += 2 + ((rest[p + 2] << 8) | rest[p + 3]);
    }
    if (m === 0xda || (m >= 0xd0 && m <= 0xd7)) { // entropy-coded data follows
      while (p < n) {
        if (rest[p] === 0xff && p + 1 < n) {
          const nxt = rest[p + 1];
          if (nxt !== 0 && !(nxt >= 0xd0 && nxt <= 0xd7)) break;
          p += 2;
        } else {
          p += 1;
        }
      }
    }
  }
  return null;
}

function iptcWithoutPlaces(iim: Uint8Array): Uint8Array | null {
  const out = new Out();
  let p = 0;
  while (p < iim.length) {
    if (iim[p] !== 0x1c || p + 5 > iim.length) return null;
    const rec = iim[p + 1], ds = iim[p + 2];
    let size = (iim[p + 3] << 8) | iim[p + 4];
    let head = 5;
    if (size & 0x8000) {
      const k = size & 0x7fff;
      if (k > 4 || p + 5 + k > iim.length) return null;
      size = 0;
      for (let i = 0; i < k; i++) size = size * 256 + iim[p + 5 + i];
      head += k;
    }
    const end = p + head + size;
    if (end > iim.length) return null;
    if (!IPTC_PLACES.has(`${rec}:${ds}`)) out.push(iim.subarray(p, end));
    p = end;
  }
  return out.bytes();
}

function photoshopWithoutPlaces(payload: Uint8Array): Uint8Array | null {
  const out = new Out();
  out.push(PHOTOSHOP_ID);
  const dv = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  let p = PHOTOSHOP_ID.length;
  while (p < payload.length) {
    if (!same(payload.subarray(p, p + 4), "8BIM") || p + 7 > payload.length) return null;
    const rid = dv.getUint16(p + 4);
    const nameLen = payload[p + 6];
    const nameEnd = p + 7 + nameLen + ((nameLen + 1) % 2);
    if (nameEnd + 4 > payload.length) return null;
    const size = dv.getUint32(nameEnd);
    const dataEnd = nameEnd + 4 + size;
    if (dataEnd > payload.length) return null;
    let data: Uint8Array | null = payload.subarray(nameEnd + 4, dataEnd);
    const next = dataEnd + (size % 2);
    if (rid === 0x0425) { p = next; continue; }
    if (rid === 0x0404) {
      data = iptcWithoutPlaces(data);
      if (data === null) return null;
    }
    out.push(payload.subarray(p, nameEnd), be32(data.length), data);
    if (data.length % 2) out.push(new Uint8Array(1));
    p = next;
  }
  return out.bytes();
}

function jpegPut(data: Uint8Array, exif: Uint8Array | null, xmp: XmpArg, strip: boolean, dropLocation: boolean): Uint8Array {
  const [segs, rest0] = jpegSegments(data);
  let rest = rest0;
  if (strip || dropLocation) { // the appended pictures (previews, depth maps) go too
    const end = jpegEnd(rest);
    if (end !== null) rest = rest.subarray(0, end);
  }
  const out = new Out();
  out.push(new Uint8Array([0xff, 0xd8]));
  let placedExif = false, placedXmp = false;
  const newExif = exif !== null ? jpegSegment(0xe1, join(EXIF_ID, exif)) : new Uint8Array(0);
  const newXmp = xmp !== KEEP && xmp !== null ? jpegSegment(0xe1, join(XMP_ID, enc.encode(xmp as string))) : new Uint8Array(0);
  let lead = 0;
  while (lead < segs.length && segs[lead][0] === 0xe0) lead++;
  segs.forEach(([marker, payload0], i) => {
    let payload = payload0;
    if (i === lead && !placedExif) {
      out.push(newExif);
      placedExif = true;
    }
    if (payload === null) {
      out.push(new Uint8Array([0xff, marker]));
      return;
    }
    const isExif = marker === 0xe1 && startsWith(payload, EXIF_ID);
    const isXmp = marker === 0xe1 && startsWith(payload, XMP_ID);
    const isXmpExt = marker === 0xe1 && startsWith(payload, XMP_EXT_ID);
    if (isExif) return;
    if (isXmp) {
      if (xmp === KEEP && !strip) out.push(jpegSegment(marker, payload));
      else if (!placedXmp) out.push(newXmp);
      placedXmp = true;
      return;
    }
    if (strip) {
      const keep = marker === 0xe0 || (marker === 0xe2 && startsWith(payload, ICC_ID)) || (marker === 0xee && startsWith(payload, ADOBE_ID));
      if (((marker >= 0xe1 && marker <= 0xef) || marker === 0xfe) && !keep) return; // every other APPn, comments
    }
    if (dropLocation) {
      if (isXmpExt || (marker === 0xe2 && startsWith(payload, MPF_ID))) return; // extended XMP; index of the appended pictures
      if (marker === 0xed && startsWith(payload, PHOTOSHOP_ID)) {
        const cleaned = photoshopWithoutPlaces(payload);
        if (cleaned === null) return; // unreadable: drop it rather than keep a location
        payload = cleaned;
      }
    }
    out.push(jpegSegment(marker, payload));
  });
  if (!placedExif) out.push(newExif);
  if (!placedXmp) out.push(newXmp);
  out.push(rest);
  return out.bytes();
}

// ---------------------------------------------------------------------------- PNG
type Chunk = [string, Uint8Array];

function pngChunks(data: Uint8Array): Chunk[] {
  if (!startsWith(data, PNG_SIG)) throw new ContainerError("not a PNG");
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const chunks: Chunk[] = [];
  let p = 8;
  while (p + 12 <= data.length) {
    const length = dv.getUint32(p);
    const kind = String.fromCharCode(...data.subarray(p + 4, p + 8));
    if (p + 12 + length > data.length) throw new ContainerError("damaged PNG");
    chunks.push([kind, data.subarray(p + 8, p + 8 + length)]);
    p += 12 + length;
    if (kind === "IEND") return chunks;
  }
  throw new ContainerError("damaged PNG");
}

let CRC_TABLE: Uint32Array | null = null;
function crc32(bytes: Uint8Array): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(kind: string, body: Uint8Array): Uint8Array {
  const kb = ascii(kind);
  return concatAll(be32(body.length), kb, body, be32(crc32(join(kb, body))));
}

function concatAll(...parts: Uint8Array[]): Uint8Array {
  const o = new Out();
  o.push(...parts);
  return o.bytes();
}

function pngXmpText(body: Uint8Array): string | null {
  if (!startsWith(body, join(PNG_XMP_KEY, new Uint8Array([0]))) || body.length < PNG_XMP_KEY.length + 3) return null;
  let rest = body.subarray(PNG_XMP_KEY.length + 1);
  const compressed = rest[0] === 1;
  rest = rest.subarray(2);
  const langEnd = rest.indexOf(0);
  const transEnd = rest.indexOf(0, langEnd + 1);
  if (langEnd < 0 || transEnd < 0) return null;
  let text = rest.subarray(transEnd + 1);
  if (compressed) {
    try {
      text = unzlibSync(text);
    } catch {
      return null;
    }
  }
  return dec.decode(text);
}

function pngPut(data: Uint8Array, exif: Uint8Array | null, xmp: XmpArg, strip: boolean, dropLocation: boolean): Uint8Array {
  const chunks = pngChunks(data);
  const out = new Out();
  out.push(PNG_SIG);
  let placed = false;
  const xmpKey0 = join(PNG_XMP_KEY, new Uint8Array([0]));
  for (const [kind, body] of chunks) {
    if (kind === "eXIf") continue;
    const isXmp = kind === "iTXt" && startsWith(body, xmpKey0);
    if (isXmp && (xmp !== KEEP || strip)) continue;
    if (strip && ["tEXt", "zTXt", "iTXt", "tIME"].includes(kind)) continue;
    const rawProfile = startsWith(body, ascii("Raw profile type"));
    if (dropLocation && ["tEXt", "zTXt", "iTXt"].includes(kind) && rawProfile) continue; // ImageMagick's copies
    if ((kind === "IDAT" || kind === "IEND") && !placed) {
      if (exif !== null) out.push(pngChunk("eXIf", exif));
      if (xmp !== KEEP && xmp !== null) {
        out.push(pngChunk("iTXt", concatAll(PNG_XMP_KEY, new Uint8Array([0, 0, 0, 0, 0]), enc.encode(xmp as string))));
      }
      placed = true;
    }
    out.push(pngChunk(kind, body));
  }
  return out.bytes();
}

// ---------------------------------------------------------------------------- WebP
function webpChunks(data: Uint8Array): Chunk[] {
  if (!same(data.subarray(0, 4), "RIFF") || !same(data.subarray(8, 12), "WEBP")) throw new ContainerError("not a WebP");
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const end = Math.min(data.length, 8 + dv.getUint32(4, true));
  const chunks: Chunk[] = [];
  let p = 12;
  while (p + 8 <= end) {
    const kind = String.fromCharCode(...data.subarray(p, p + 4));
    const size = dv.getUint32(p + 4, true);
    if (p + 8 + size > end) throw new ContainerError("damaged WebP");
    chunks.push([kind, data.subarray(p + 8, p + 8 + size)]);
    p += 8 + size + (size & 1);
  }
  return chunks;
}

function webpCanvas(chunks: Chunk[]): [number, number, boolean] {
  for (const [kind, body] of chunks) {
    if (kind === "VP8 " && body.length >= 10 && body[3] === 0x9d && body[4] === 0x01 && body[5] === 0x2a) {
      const w = (body[6] | (body[7] << 8)) & 0x3fff;
      const h = (body[8] | (body[9] << 8)) & 0x3fff;
      return [w, h, chunks.some(([k]) => k === "ALPH")];
    }
    if (kind === "VP8L" && body.length >= 5 && body[0] === 0x2f) {
      const bits = new DataView(body.buffer, body.byteOffset + 1, 4).getUint32(0, true);
      return [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1, Boolean((bits >>> 28) & 1)];
    }
  }
  throw new ContainerError("damaged WebP");
}

function webpChunk(kind: string, body: Uint8Array): Uint8Array {
  return concatAll(ascii(kind), le32(body.length), body, body.length & 1 ? new Uint8Array(1) : new Uint8Array(0));
}

const le24 = (n: number) => new Uint8Array([n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff]);

function webpPut(data: Uint8Array, exif: Uint8Array | null, xmp: XmpArg, strip: boolean): Uint8Array {
  const chunks = webpChunks(data);
  const keepXmp = xmp === KEEP && !strip;
  const oldXmp = keepXmp ? chunks.find(([k]) => k === "XMP ")?.[1] ?? null : null;
  const newXmp = keepXmp ? oldXmp : xmp !== null && xmp !== KEEP ? enc.encode(xmp as string) : null;
  const body: Chunk[] = chunks.filter(([k]) => k !== "EXIF" && k !== "XMP ");
  let vp8x = body.find(([k]) => k === "VP8X")?.[1] ?? null;
  if (vp8x === null && (exif !== null || newXmp !== null)) {
    const [w, h, alpha] = webpCanvas(body);
    vp8x = concatAll(new Uint8Array([alpha ? 0x10 : 0, 0, 0, 0]), le24(w - 1), le24(h - 1));
    body.unshift(["VP8X", vp8x]);
  }
  const out = new Out();
  for (const [k, b0] of body) {
    let b = b0;
    if (k === "VP8X") {
      let flags = b[0] & ~0x0c;
      flags |= exif !== null ? 0x08 : 0;
      flags |= newXmp !== null ? 0x04 : 0;
      b = join(new Uint8Array([flags]), b.subarray(1));
    }
    out.push(webpChunk(k, b));
  }
  if (exif !== null) out.push(webpChunk("EXIF", exif));
  if (newXmp !== null) out.push(webpChunk("XMP ", newXmp));
  const inner = out.bytes();
  return concatAll(ascii("RIFF"), le32(4 + inner.length), ascii("WEBP"), inner);
}

// ---------------------------------------------------------------------------- the API
export function getExif(data: Uint8Array): Uint8Array | null {
  const kind = kindOf(data);
  if (kind === "jpeg") return jpegGet(data, EXIF_ID);
  if (kind === "png") {
    const raw = pngChunks(data).find(([k]) => k === "eXIf")?.[1] ?? null;
    return raw !== null && startsWith(raw, EXIF_ID) ? raw.subarray(6) : raw;
  }
  if (kind === "webp") {
    const raw = webpChunks(data).find(([k]) => k === "EXIF")?.[1] ?? null;
    return raw !== null && startsWith(raw, EXIF_ID) ? raw.subarray(6) : raw;
  }
  return null;
}

export function getXmp(data: Uint8Array): string | null {
  const kind = kindOf(data);
  if (kind === "jpeg") {
    const raw = jpegGet(data, XMP_ID);
    return raw !== null ? dec.decode(raw) : null;
  }
  if (kind === "png") {
    for (const [k, b] of pngChunks(data)) {
      if (k === "iTXt") {
        const text = pngXmpText(b);
        if (text !== null) return text;
      }
    }
    return null;
  }
  if (kind === "webp") {
    const raw = webpChunks(data).find(([k]) => k === "XMP ")?.[1] ?? null;
    return raw !== null ? dec.decode(raw) : null;
  }
  return null;
}

export function put(data: Uint8Array, exif: Uint8Array | null, xmp: XmpArg = KEEP, strip = false, dropLocation = false): Uint8Array {
  const kind = kindOf(data);
  if (kind === "jpeg") return jpegPut(data, exif, xmp, strip, dropLocation);
  if (kind === "png") return pngPut(data, exif, xmp, strip, dropLocation);
  if (kind === "webp") return webpPut(data, exif, xmp, strip);
  throw new ContainerError("only JPEG, PNG and WebP pictures can be changed");
}
