// Fonts without loss: WOFF (own zlib codec on fflate), WOFF2 (Google's woff2 via woff2-encoder),
// subsetting with HarfBuzz's hb-subset (keeps OpenType layout). TTF ↔ OTF outline conversion is
// desktop-only (fontTools).
import { ConversionError } from "../core/types";

export type Flavor = "ttf" | "otf" | "woff" | "woff2";

export function flavorOf(data: Uint8Array): Flavor | null {
  const tag = String.fromCharCode(...data.subarray(0, 4));
  if (tag === "wOFF") return "woff";
  if (tag === "wOF2") return "woff2";
  if (tag === "OTTO") return "otf";
  if (tag === "\x00\x01\x00\x00" || tag === "true") return "ttf";
  return null;
}

/** "ttf" (TrueType glyf outlines) or "otf" (CFF) of an uncompressed SFNT. */
export function outlinesOf(sfnt: Uint8Array): "ttf" | "otf" {
  return flavorOf(sfnt) === "otf" ? "otf" : "ttf";
}

const align4 = (n: number) => (n + 3) & ~3;

// ------------------------------------------------------------------------- WOFF 1.0

export async function woffToSfnt(woff: Uint8Array): Promise<Uint8Array> {
  const { unzlibSync } = await import("fflate");
  const dv = new DataView(woff.buffer, woff.byteOffset, woff.byteLength);
  const flavor = dv.getUint32(4);
  const n = dv.getUint16(12);
  const tables: { tag: number; checksum: number; data: Uint8Array }[] = [];
  for (let i = 0; i < n; i++) {
    const p = 44 + i * 20;
    const [tag, offset, compLen, origLen, checksum] = [dv.getUint32(p), dv.getUint32(p + 4), dv.getUint32(p + 8), dv.getUint32(p + 12), dv.getUint32(p + 16)];
    const raw = woff.subarray(offset, offset + compLen);
    const data = compLen < origLen ? unzlibSync(raw) : raw;
    if (data.length !== origLen) throw new ConversionError("This WOFF file is damaged.");
    tables.push({ tag, checksum, data });
  }
  return buildSfnt(flavor, tables);
}

function buildSfnt(flavor: number, tables: { tag: number; checksum: number; data: Uint8Array }[]): Uint8Array {
  tables.sort((a, b) => a.tag - b.tag);
  const n = tables.length;
  const size = 12 + 16 * n + tables.reduce((s, t) => s + align4(t.data.length), 0);
  const out = new Uint8Array(size);
  const dv = new DataView(out.buffer);
  const entrySelector = Math.floor(Math.log2(n));
  dv.setUint32(0, flavor);
  dv.setUint16(4, n);
  dv.setUint16(6, 16 * 2 ** entrySelector);
  dv.setUint16(8, entrySelector);
  dv.setUint16(10, n * 16 - 16 * 2 ** entrySelector);
  let offset = 12 + 16 * n;
  tables.forEach((t, i) => {
    const p = 12 + i * 16;
    dv.setUint32(p, t.tag);
    dv.setUint32(p + 4, t.checksum);
    dv.setUint32(p + 8, offset);
    dv.setUint32(p + 12, t.data.length);
    out.set(t.data, offset);
    offset += align4(t.data.length);
  });
  return out;
}

function readSfnt(sfnt: Uint8Array) {
  const dv = new DataView(sfnt.buffer, sfnt.byteOffset, sfnt.byteLength);
  const n = dv.getUint16(4);
  const tables = [];
  for (let i = 0; i < n; i++) {
    const p = 12 + i * 16;
    const offset = dv.getUint32(p + 8);
    tables.push({ tag: dv.getUint32(p), checksum: dv.getUint32(p + 4), data: sfnt.subarray(offset, offset + dv.getUint32(p + 12)) });
  }
  return { flavor: dv.getUint32(0), tables };
}

export async function sfntToWoff(sfnt: Uint8Array): Promise<Uint8Array> {
  const { zlibSync } = await import("fflate");
  const { flavor, tables } = readSfnt(sfnt);
  const packed = tables.map((t) => {
    const z = zlibSync(t.data, { level: 9 });
    return { ...t, stored: z.length < t.data.length ? z : t.data };
  });
  const n = packed.length;
  const total = 44 + 20 * n + packed.reduce((s, t) => s + align4(t.stored.length), 0);
  const out = new Uint8Array(total);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x774f4646); // "wOFF"
  dv.setUint32(4, flavor);
  dv.setUint32(8, total);
  dv.setUint16(12, n);
  dv.setUint32(16, 12 + 16 * n + tables.reduce((s, t) => s + align4(t.data.length), 0)); // totalSfntSize
  dv.setUint16(20, 1); // version 1.0
  let offset = 44 + 20 * n;
  packed.forEach((t, i) => {
    const p = 44 + i * 20;
    dv.setUint32(p, t.tag);
    dv.setUint32(p + 4, offset);
    dv.setUint32(p + 8, t.stored.length);
    dv.setUint32(p + 12, t.data.length);
    dv.setUint32(p + 16, t.checksum);
    out.set(t.stored, offset);
    offset += align4(t.stored.length);
  });
  return out;
}

// ------------------------------------------------------------------------- any → SFNT and back

export async function toSfnt(data: Uint8Array, name: string): Promise<Uint8Array> {
  const flavor = flavorOf(data);
  if (flavor === "ttf" || flavor === "otf") return data;
  if (flavor === "woff") return woffToSfnt(data);
  if (flavor === "woff2") {
    const { decompress } = await import("woff2-encoder");
    return decompress(data);
  }
  throw new ConversionError(`“${name}” isn't a TTF, OTF, WOFF or WOFF2 font.`);
}

export async function fromSfnt(sfnt: Uint8Array, target: Flavor): Promise<Uint8Array> {
  if (target === "woff") return sfntToWoff(sfnt);
  if (target === "woff2") {
    const { compress } = await import("woff2-encoder");
    return compress(sfnt);
  }
  return sfnt;
}

// ------------------------------------------------------------------------- hb-subset

interface HB {
  memory: WebAssembly.Memory;
  malloc(n: number): number;
  free(p: number): void;
  hb_blob_create(data: number, len: number, mode: number, user: number, destroy: number): number;
  hb_blob_destroy(b: number): void;
  hb_blob_get_length(b: number): number;
  hb_blob_get_data(b: number, lenPtr: number): number;
  hb_face_create(blob: number, index: number): number;
  hb_face_destroy(f: number): void;
  hb_face_reference_blob(f: number): number;
  hb_set_add(s: number, v: number): void;
  hb_set_invert(s: number): void;
  hb_subset_input_create_or_fail(): number;
  hb_subset_input_destroy(i: number): void;
  hb_subset_input_unicode_set(i: number): number;
  hb_subset_input_set(i: number, kind: number): number;
  hb_subset_input_get_flags(i: number): number;
  hb_subset_input_set_flags(i: number, f: number): void;
  hb_subset_or_fail(face: number, input: number): number;
}
let hb: Promise<HB> | null = null;

async function harfbuzz(): Promise<HB> {
  if (!hb) {
    hb = (async () => {
      const { default: url } = await import("harfbuzzjs/dist/harfbuzz-subset.wasm?url");
      const { instance } = await WebAssembly.instantiate(await (await fetch(url)).arrayBuffer(), {});
      const ex = instance.exports as unknown as HB & { _initialize?: () => void };
      ex._initialize?.();
      return ex;
    })();
    hb.catch(() => (hb = null));
  }
  return hb;
}

const SETS = { UNICODE: 1, NAME_ID: 4, NAME_LANG_ID: 5, LAYOUT_FEATURE_TAG: 6 };
const FLAGS = { NO_HINTING: 0x1, NOTDEF_OUTLINE: 0x40, GLYPH_NAMES: 0x80 };

/** Keep only ``codepoints`` (null = all); optionally drop hinting; keep all features or the default set. */
export async function subset(sfnt: Uint8Array, codepoints: number[] | null, opts: { hinting: boolean; allFeatures: boolean }): Promise<Uint8Array> {
  const h = await harfbuzz();
  const ptr = h.malloc(sfnt.length);
  new Uint8Array(h.memory.buffer).set(sfnt, ptr);
  const blob = h.hb_blob_create(ptr, sfnt.length, 2 /* writable */, 0, 0);
  const face = h.hb_face_create(blob, 0);
  h.hb_blob_destroy(blob);
  const input = h.hb_subset_input_create_or_fail();
  try {
    if (!input) throw new ConversionError("Couldn't prepare the font for subsetting.");
    const unicodes = h.hb_subset_input_unicode_set(input);
    if (codepoints) for (const c of codepoints) h.hb_set_add(unicodes, c);
    else h.hb_set_invert(unicodes); // every character
    h.hb_set_invert(h.hb_subset_input_set(input, SETS.NAME_ID)); // keep all names…
    h.hb_set_invert(h.hb_subset_input_set(input, SETS.NAME_LANG_ID)); // …in all languages
    if (opts.allFeatures) h.hb_set_invert(h.hb_subset_input_set(input, SETS.LAYOUT_FEATURE_TAG));
    let flags = h.hb_subset_input_get_flags(input) | FLAGS.NOTDEF_OUTLINE | FLAGS.GLYPH_NAMES;
    if (!opts.hinting) flags |= FLAGS.NO_HINTING;
    h.hb_subset_input_set_flags(input, flags);
    const result = h.hb_subset_or_fail(face, input);
    if (!result) throw new ConversionError("Subsetting this font failed.");
    const out = h.hb_face_reference_blob(result);
    const lenPtr = h.malloc(4);
    const dataPtr = h.hb_blob_get_data(out, lenPtr);
    const len = h.hb_blob_get_length(out);
    const bytes = new Uint8Array(h.memory.buffer, dataPtr, len).slice();
    h.free(lenPtr);
    h.hb_blob_destroy(out);
    h.hb_face_destroy(result);
    if (!len) throw new ConversionError("Subsetting this font failed.");
    return bytes;
  } finally {
    if (input) h.hb_subset_input_destroy(input);
    h.hb_face_destroy(face);
    h.free(ptr);
  }
}

/** "U+0000-00FF,U+0131,…" → code points. */
export function parseUnicodes(ranges: string): number[] {
  const out: number[] = [];
  for (const part of ranges.split(",")) {
    const m = /^U\+([0-9A-F]+)(?:-([0-9A-F]+))?$/i.exec(part.trim());
    if (!m) continue;
    const a = parseInt(m[1], 16), b = m[2] ? parseInt(m[2], 16) : a;
    for (let c = a; c <= b; c++) out.push(c);
  }
  return out;
}
