// Web readers that need no engine (dates, Office / ODF XML, font tables, EXIF numbers) — the
// counterparts of the desktop's properties/read.py, so both apps fill the same facts. The worker
// converter (converters/properties.ts) adds what needs ImageMagick, Mediabunny or MuPDF.
import * as fmt from "./format";
import type { Facts } from "./info";

export const NOTE = "Some details of this file couldn't be read.";
export const BROWSER_NOTE = "The browser can't read the details of this file. The desktop app can.";

export const NOUN: Record<string, string> = {
  image: "image", video: "video", audio: "audio", document: "document", model: "3D model",
  archive: "archive", subtitle: "subtitles", font: "font", vector: "vector graphic", cad: "CAD drawing",
};
export const OFFICE_EXTS = new Set(["docx", "docm", "dotx", "xlsx", "xlsm", "pptx", "pptm", "ppsx"]);
export const ODF_EXTS = new Set(["odt", "ods", "odp", "odg"]);

/** The "File" facts of a browser File (no folder: the browser never tells). */
export function fileFacts(file: File, name: string, ext: string, family: string): Facts {
  return { name, type: `${ext.toUpperCase()} ${NOUN[family] ?? "file"}`, size: file.size, modified: file.lastModified ? fmt.date(new Date(file.lastModified)) : undefined };
}

// ----------------------------------------------------------------------------- dates
/** An ISO date ("2026-11-01T09:00:00Z") → local display text; a missing zone counts as UTC. */
export function isoLocal(text: string): string | undefined {
  const t = (text || "").trim();
  if (!t) return undefined;
  const zoned = /([Zz]|[+-]\d\d:?\d\d)$/.test(t) ? t : `${t}Z`;
  const d = new Date(zoned);
  return Number.isNaN(d.getTime()) ? undefined : fmt.date(d);
}

/** EXIF "2026:11:12 14:03:00" (the camera's clock, no zone) → "2026-11-12 14:03". */
export function exifDate(text: string): string | undefined {
  const m = /^(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d)/.exec((text || "").trim());
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : undefined;
}

/** PDF "D:20261112140300+01'00'" → local display text (no zone: shown as written). */
export function pdfDate(text: string): string | undefined {
  const m = /^D?:?(\d{4})(\d\d)?(\d\d)?(\d\d)?(\d\d)?(\d\d)?([Zz+-])?(\d\d)?'?(\d\d)?/.exec(text || "");
  if (!m) return undefined;
  const [y, mo, d, h, mi, s] = [1, 2, 3, 4, 5, 6].map((i, k) => (m[i] ? Number(m[i]) : [0, 1, 1, 0, 0, 0][k]));
  const check = new Date(Date.UTC(y, mo - 1, d, h, mi, s)); // Feb 30 rolls over: refuse it, like Python
  if (check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d || h > 23 || mi > 59 || s > 59) return undefined;
  const sign = m[7];
  if (sign === "+" || sign === "-") {
    const off = (Number(m[8] ?? 0) * 60 + Number(m[9] ?? 0)) * (sign === "+" ? 1 : -1);
    return fmt.date(new Date(Date.UTC(y, mo - 1, d, h, mi, s) - off * 60000));
  }
  if (sign === "Z" || sign === "z") return fmt.date(new Date(Date.UTC(y, mo - 1, d, h, mi, s)));
  const two = (x: number) => String(x).padStart(2, "0");
  return `${y}-${two(mo)}-${two(d)} ${two(h)}:${two(mi)}`;
}

/** ISO 6709 ("+55.6761+012.5683/", phones' video location) → [lat, lon]. */
export function iso6709(text: string): [number, number] | null {
  const m = /^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)/.exec(text || "");
  return m ? [Number(m[1]), Number(m[2])] : null;
}

// ----------------------------------------------------------------------------- EXIF numbers
/** "1/120", "18/10", "200" → number (the first of a list). */
export function rational(text: string | null | undefined): number | undefined {
  const first = String(text ?? "").split(",")[0].trim();
  if (!first) return undefined;
  const [a, b] = first.split("/").map(Number);
  const v = b === undefined ? a : b ? a / b : NaN;
  return Number.isFinite(v) ? v : undefined;
}

/** "55/1, 40/1, 3396/100" (degrees, minutes, seconds) → decimal degrees. */
export function dms(text: string | null | undefined): number | undefined {
  const parts = String(text ?? "").split(",").map((p) => rational(p));
  if (parts.length !== 3 || parts.some((p) => p === undefined)) return undefined;
  const [d, m, s] = parts as number[];
  return d + m / 60 + s / 3600;
}

// ----------------------------------------------------------------------------- Office / ODF
/** The text of the first element called ``tag`` (any namespace prefix), or undefined. */
function xmlText(xml: string, tag: string): string | undefined {
  const local = tag.includes(":") ? tag.split(":")[1] : tag;
  const m = new RegExp(`<(?:[\\w-]+:)?${local}(?:\\s[^>]*)?>([\\s\\S]*?)</(?:[\\w-]+:)?${local}>`).exec(xml);
  if (!m) return undefined;
  const text = m[1].replace(/<[^>]*>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&").trim();
  return text || undefined;
}

const numOrUndef = (v: string | undefined) => (Number(v) ? Math.trunc(Number(v)) : undefined);

export function officeFacts(core: string, app: string): Facts {
  const created = xmlText(core, "created"), modified = xmlText(core, "modified");
  return clean({
    title: xmlText(core, "title"),
    subject: xmlText(core, "subject"),
    author: xmlText(core, "creator"),
    last_saved_by: xmlText(core, "lastModifiedBy"),
    keywords: xmlText(core, "keywords"),
    created: created ? isoLocal(created) : undefined,
    modified: modified ? isoLocal(modified) : undefined,
    company: xmlText(app, "Company"),
    application: xmlText(app, "Application"),
    pages: numOrUndef(xmlText(app, "Pages")),
    words: numOrUndef(xmlText(app, "Words")),
    slides: numOrUndef(xmlText(app, "Slides")),
  });
}

export function odfFacts(meta: string): Facts {
  const stat = (k: string) => numOrUndef(new RegExp(`meta:${k}="(\\d+)"`).exec(meta)?.[1]);
  const created = xmlText(meta, "meta:creation-date"), modified = xmlText(meta, "dc:date");
  const app = xmlText(meta, "meta:generator");
  return clean({
    title: xmlText(meta, "dc:title"),
    subject: xmlText(meta, "dc:subject"),
    author: xmlText(meta, "meta:initial-creator"),
    last_saved_by: xmlText(meta, "dc:creator"),
    keywords: xmlText(meta, "meta:keyword"),
    created: created ? isoLocal(created) : undefined,
    modified: modified ? isoLocal(modified) : undefined,
    application: app ? app.split("/")[0] : undefined,
    pages: stat("page-count"),
    words: stat("word-count"),
  });
}

export const clean = (o: Facts): Facts => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ""));

// ----------------------------------------------------------------------------- fonts
type NameRecord = [number, number, number, number, string]; // platform, encoding, language, id, text

/** English Windows name first, then any Windows, then Mac (the desktop's ``best_name``). */
export function bestName(records: NameRecord[], id: number): string | undefined {
  const tests: ((r: NameRecord) => boolean)[] = [(r) => r[0] === 3 && r[2] === 0x409, (r) => r[0] === 3, (r) => r[0] === 1, () => true];
  const candidates = records.filter((r) => r[3] === id && r[4].trim());
  for (const t of tests) for (const r of candidates) if (t(r)) return r[4].trim();
  return undefined;
}

/** An uncompressed TrueType / OpenType font → font facts (first font of a collection). */
export function fontFacts(sfnt: Uint8Array): Facts {
  const dv = new DataView(sfnt.buffer, sfnt.byteOffset, sfnt.byteLength);
  let base = 0;
  if (String.fromCharCode(...sfnt.subarray(0, 4)) === "ttcf") base = dv.getUint32(12);
  const n = dv.getUint16(base + 4);
  const tables = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const at = base + 12 + i * 16;
    tables.set(String.fromCharCode(...sfnt.subarray(at, at + 4)), dv.getUint32(at + 8));
  }
  const records: NameRecord[] = [];
  const name = tables.get("name");
  if (name !== undefined) {
    const count = dv.getUint16(name + 2), strings = name + dv.getUint16(name + 4);
    for (let i = 0; i < count; i++) {
      const at = name + 6 + i * 12;
      const [platform, encoding, language, id, length, offset] = [0, 2, 4, 6, 8, 10].map((o) => dv.getUint16(at + o));
      const bytes = sfnt.subarray(strings + offset, strings + offset + length);
      let text = "";
      if (platform === 3 || platform === 0) {
        for (let k = 0; k + 1 < bytes.length; k += 2) text += String.fromCharCode((bytes[k] << 8) | bytes[k + 1]);
      } else if (platform === 1 && encoding === 0) {
        text = new TextDecoder("macintosh").decode(bytes);
      } else continue;
      records.push([platform, encoding, language, id, text]);
    }
  }
  const pick = (id: number) => bestName(records, id);
  const maxp = tables.get("maxp");
  const axes: string[] = [];
  const fvar = tables.get("fvar");
  if (fvar !== undefined) {
    const offset = dv.getUint16(fvar + 4), count = dv.getUint16(fvar + 8), size = dv.getUint16(fvar + 10);
    for (let i = 0; i < count; i++) {
      const at = fvar + offset + i * size;
      axes.push(String.fromCharCode(...sfnt.subarray(at, at + 4)));
    }
  }
  return clean({
    family: pick(16) ?? pick(1),
    style: pick(17) ?? pick(2),
    version: pick(5),
    designer: pick(9),
    manufacturer: pick(8),
    copyright: pick(0),
    glyphs: maxp !== undefined ? dv.getUint16(maxp + 4) : undefined,
    outlines: tables.has("CFF ") || tables.has("CFF2") ? "cff" : "ttf",
    axes,
  });
}
