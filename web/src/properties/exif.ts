// Reading and changing a photo's EXIF block — a line-by-line port of the desktop's
// properties/exif.py (both suites check tests/fixtures/photo_edit_cases.json byte for byte).
// Changes are appended: original bytes stay put (maker notes point into them), changed values
// and rewritten directories go at the end, and whatever is left unreferenced is zeroed.

export const SIZES: Record<number, number> = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8 };
export const BYTE = 1, ASCII = 2, SHORT = 3, LONG = 4;
export const EXIF_IFD = 0x8769, GPS_IFD = 0x8825;
export const TAG = {
  description: 0x010e, make: 0x010f, model: 0x0110, orientation: 0x0112, artist: 0x013b,
  copyright: 0x8298, xp_title: 0x9c9b, xp_comment: 0x9c9c, xp_author: 0x9c9d, xp_keywords: 0x9c9e,
  taken: 0x9003, digitized: 0x9004,
} as const;
const XP_TAGS: number[] = [TAG.xp_title, TAG.xp_comment, TAG.xp_author, TAG.xp_keywords];
const MAX_IFD_ENTRIES = 1000;

export class ExifError extends Error {}

export interface Entry { tag: number; type: number; count: number; raw: Uint8Array; at: number }

export class Ifd {
  entries: Entry[] = [];
  next = 0;
  constructor(public offset: number) {}
  get(tag: number): Entry | undefined { return this.entries.find((e) => e.tag === tag); }
  size(): number { return 2 + 12 * this.entries.length + 4; }
}

const utf8 = new TextDecoder("utf-8");
const utf16 = new TextDecoder("utf-16le");

export class Tiff {
  readonly data: Uint8Array;
  readonly le: boolean;
  private readonly dv: DataView;
  readonly ifd0: Ifd;
  readonly exif: Ifd | null;
  readonly gps: Ifd | null;

  constructor(data: Uint8Array) {
    const tag = String.fromCharCode(data[0] ?? 0, data[1] ?? 0);
    if (data.length < 8 || (tag !== "II" && tag !== "MM")) throw new ExifError("not an EXIF block");
    this.data = new Uint8Array(data);
    this.le = tag === "II";
    this.dv = new DataView(this.data.buffer);
    if (this.u16(2) !== 42) throw new ExifError("not an EXIF block");
    this.ifd0 = this.readIfd(this.u32(4));
    this.exif = this.sub(this.ifd0, EXIF_IFD);
    this.gps = this.sub(this.ifd0, GPS_IFD);
  }

  u16(at: number): number { return this.dv.getUint16(at, this.le); }
  u32(at: number): number { return this.dv.getUint32(at, this.le); }

  readIfd(at: number): Ifd {
    if (at < 8 || at + 2 > this.data.length) throw new ExifError("damaged EXIF block");
    const n = this.u16(at);
    if (n > MAX_IFD_ENTRIES || at + 2 + 12 * n + 4 > this.data.length) throw new ExifError("damaged EXIF block");
    const ifd = new Ifd(at);
    for (let i = 0; i < n; i++) {
      const p = at + 2 + 12 * i;
      const tag = this.u16(p), type = this.u16(p + 2), count = this.u32(p + 4);
      const size = (SIZES[type] ?? 0) * count;
      let raw: Uint8Array, where: number;
      if (size <= 4) {
        raw = this.data.slice(p + 8, p + 8 + size);
        where = -1;
      } else {
        where = this.u32(p + 8);
        if (where + size > this.data.length) continue; // a broken entry: leave it out
        raw = this.data.slice(where, where + size);
      }
      ifd.entries.push({ tag, type, count, raw, at: where });
    }
    ifd.next = this.u32(at + 2 + 12 * n);
    return ifd;
  }

  sub(ifd: Ifd, tag: number): Ifd | null {
    const e = ifd.get(tag);
    if (!e || (e.type !== LONG && e.type !== 13) || e.count !== 1) return null;
    try {
      return this.readIfd(new DataView(e.raw.buffer, e.raw.byteOffset, 4).getUint32(0, this.le));
    } catch (err) {
      if (err instanceof ExifError) return null;
      throw err;
    }
  }

  text(ifd: Ifd | null, tag: number): string | null {
    const e = ifd ? ifd.get(tag) : undefined;
    if (!e) return null;
    let text = XP_TAGS.includes(tag) ? utf16.decode(e.raw.length % 2 ? e.raw.subarray(0, e.raw.length - 1) : e.raw) : utf8.decode(e.raw);
    text = text.split("\x00", 1)[0].trim();
    return text || null;
  }

  short(ifd: Ifd | null, tag: number): number | null {
    const e = ifd ? ifd.get(tag) : undefined;
    if (!e || e.type !== SHORT || e.raw.length < 2) return null;
    return new DataView(e.raw.buffer, e.raw.byteOffset, 2).getUint16(0, this.le);
  }
}

// ---------------------------------------------------------------------------- encoding values
export type Change = [number, Uint8Array] | null;

const enc = new TextEncoder();
const concat = (...parts: Uint8Array[]) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
};

export const asciiValue = (text: string): [number, Uint8Array] => [ASCII, concat(enc.encode(text), new Uint8Array([0]))];

/** Windows' own fields (Title, Comments, Authors, Tags): UTF-16 in BYTE values. */
export function xpValue(text: string): [number, Uint8Array] {
  const out = new Uint8Array(text.length * 2 + 2);
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    out[2 * i] = c & 0xff;
    out[2 * i + 1] = c >> 8;
  }
  return [BYTE, out];
}

export function shortValue(n: number, le: boolean): [number, Uint8Array] {
  const b = new Uint8Array(2);
  new DataView(b.buffer).setUint16(0, n, le);
  return [SHORT, b];
}

/** A valid EXIF block with an empty IFD0 (little-endian). */
export const emptyTiff = (): Uint8Array => new Uint8Array([0x49, 0x49, 0x2a, 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0]);

// ---------------------------------------------------------------------------- changing
class Builder {
  buf: Uint8Array;
  length: number;
  constructor(start: Uint8Array) { this.buf = new Uint8Array(Math.max(64, start.length * 2)); this.buf.set(start); this.length = start.length; }
  private room(n: number) {
    if (this.length + n <= this.buf.length) return;
    const bigger = new Uint8Array(Math.max(this.buf.length * 2, this.length + n));
    bigger.set(this.buf.subarray(0, this.length));
    this.buf = bigger;
  }
  push(...bytes: Uint8Array[]) { for (const b of bytes) { this.room(b.length); this.buf.set(b, this.length); this.length += b.length; } }
  align() { if (this.length % 2) this.push(new Uint8Array(1)); }
  bytes(): Uint8Array { return this.buf.slice(0, this.length); }
}

/** ``data`` with the given tags set or deleted (see the header for how). */
export function rewrite(data: Uint8Array, ifd0?: Map<number, Change>, exif?: Map<number, Change>, dropGps = false): Uint8Array {
  const t = new Tiff(data);
  const le = t.le;
  const out = new Builder(t.data);
  const wipe: [number, number][] = [];
  const keep = new Set<number>();
  const u16 = (n: number) => { const b = new Uint8Array(2); new DataView(b.buffer).setUint16(0, n, le); return b; };
  const u32 = (n: number) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n, le); return b; };

  const putDir = (entries: Entry[], nextIfd: number): number => {
    out.align();
    const at = out.length;
    out.push(u16(entries.length));
    for (const en of [...entries].sort((a, b) => a.tag - b.tag)) {
      out.push(u16(en.tag), u16(en.type), u32(en.count));
      if (en.raw.length <= 4) {
        const field = new Uint8Array(4);
        field.set(en.raw);
        out.push(field);
      } else {
        out.push(u32(en.at));
      }
    }
    out.push(u32(nextIfd));
    return at;
  };

  const apply = (ifd: Ifd | null, changes: Map<number, Change>): Entry[] => {
    const entries = ifd ? [...ifd.entries] : [];
    for (const [tag, change] of changes) {
      const i = entries.findIndex((x) => x.tag === tag);
      if (i >= 0) {
        const [old] = entries.splice(i, 1);
        if (old.at >= 0) wipe.push([old.at, old.raw.length]);
      }
      if (change === null) continue;
      const [type, raw] = change;
      const count = raw.length / SIZES[type];
      let where = -1;
      if (raw.length > 4) {
        out.align();
        where = out.length;
        out.push(raw);
      }
      entries.push({ tag, type, count, raw, at: where });
    }
    for (const en of entries) if (en.at >= 0) keep.add(en.at);
    return entries;
  };

  const ifd0Changes = new Map<number, Change>(ifd0 ?? []);
  if (exif && exif.size) {
    if (t.exif) wipe.push([t.exif.offset, t.exif.size()]);
    const entries = apply(t.exif, exif);
    if (entries.length) {
      const at = putDir(entries, t.exif ? t.exif.next : 0);
      ifd0Changes.set(EXIF_IFD, [LONG, u32(at)]);
    } else {
      ifd0Changes.set(EXIF_IFD, null);
    }
  }
  if (dropGps && t.gps) {
    wipe.push([t.gps.offset, t.gps.size()]);
    for (const en of t.gps.entries) if (en.at >= 0) wipe.push([en.at, en.raw.length]);
    ifd0Changes.set(GPS_IFD, null);
  } else if (dropGps && t.ifd0.get(GPS_IFD)) {
    ifd0Changes.set(GPS_IFD, null);
  }
  if (ifd0Changes.size) {
    wipe.push([t.ifd0.offset, t.ifd0.size()]);
    const entries = apply(t.ifd0, ifd0Changes);
    const at = putDir(entries, t.ifd0.next);
    out.buf.set(u32(at), 4);
  }
  for (const [start, length] of wipe) {
    if (start < 8 || keep.has(start)) continue;
    const end = Math.min(start + length, t.data.length);
    out.buf.fill(0, start, end);
  }
  return out.bytes();
}
