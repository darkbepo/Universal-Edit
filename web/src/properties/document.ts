// Changing a document's details on the website — a line-by-line port of the desktop's
// properties/document.py (both suites check tests/fixtures/document_cases.json, so the XML
// comes out byte for byte the same): title, subject, author and keywords of Office
// (docProps/core.xml) and OpenDocument (meta.xml) files, changed as text. ``rewriteZip`` copies
// every other entry of the zip as it is (raw bytes, same order; ODF's mimetype stays first and
// stored). PDFs are changed with MuPDF in the worker (converters/documenttags.ts).
import { deflateSync, Inflate } from "fflate";
import { wellFormed, wsTrim } from "./exif";

export const PDF_EXTS = new Set(["pdf"]);
export const OFFICE_EXTS = new Set(["docx", "docm", "dotx", "xlsx", "xlsm", "pptx", "pptm", "ppsx"]);
export const ODF_EXTS = new Set(["odt", "ods", "odp", "odg"]);
export const WRITABLE_EXTS = new Set([...PDF_EXTS, ...OFFICE_EXTS, ...ODF_EXTS]);
export const FIELDS = ["title", "subject", "author", "keywords"] as const;
export type Field = (typeof FIELDS)[number];
export type Kind = "pdf" | "office" | "odf";
export const MAX_CHARS = 2000;
export const MAX_PART_BYTES = 10 * 1024 * 1024;
/** The browser keeps the whole file in memory twice; above this it's the desktop app's job. */
export const MAX_BYTES = 1024 ** 3;

const DC = "http://purl.org/dc/elements/1.1/";
const CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties";
const DCTERMS = "http://purl.org/dc/terms/";
const OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0";
const META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0";
const MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0";

const CORE_PART = "docProps/core.xml";
const CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml";
const CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties";
// field → [namespace, local name, prefix used when the file has none for it]
const CORE_FIELDS: Record<Field, [string, string, string]> = {
  title: [DC, "title", "dc"],
  subject: [DC, "subject", "dc"],
  author: [DC, "creator", "dc"],
  keywords: [CP, "keywords", "cp"],
};
const ODF_FIELDS: Record<Field, [string, string, string]> = {
  title: [DC, "title", "dc"],
  subject: [DC, "subject", "dc"],
  author: [META, "initial-creator", "meta"],
  keywords: [META, "keyword", "meta"],
};
export const PDF_KEYS: Record<Field, string> = { title: "Title", subject: "Subject", author: "Author", keywords: "Keywords" };
export const XMP_TWINS: Record<Field, string> = {
  title: "dc:title",
  subject: "dc:description",
  author: "dc:creator",
  keywords: "pdf:Keywords",
};

export const LOCKED_TEXT = "This PDF is locked with a password. Type it to see and change the details.";
export const WRONG_PASSWORD_TEXT = "That password isn't right.";
export const FORMAT_TEXT = "Only PDF, Word, Excel, PowerPoint and OpenDocument files can be changed here.";
export const BROKEN_TEXT = "The details part of this file can't be read, so it can't be changed safely.";
export const SIGNED_TEXT =
  "This PDF is signed. The signature stays, but PDF readers will show that the file was " +
  "changed after signing.";

export class DocumentError extends Error {}
export class PasswordNeeded extends DocumentError {}

export interface DocumentDetails {
  kind: string;
  title: string;
  subject: string;
  author: string;
  keywords: string;
  signed: boolean;
}

export type Values = Partial<Record<Field, string>>;
export type Parts = Record<string, string | null>;

export const emptyDetails = (kind: string): DocumentDetails => ({
  kind, title: "", subject: "", author: "", keywords: "", signed: false,
});

export function kindOf(ext: string): Kind | null {
  ext = ext.toLowerCase();
  if (PDF_EXTS.has(ext)) return "pdf";
  if (OFFICE_EXTS.has(ext)) return "office";
  if (ODF_EXTS.has(ext)) return "odf";
  return null;
}

/** The user's text → what's written ("" = remove it); throws DocumentError. */
export function cleanValues(values: Values): Values {
  const out: Values = {};
  for (const name of FIELDS) {
    const raw = values[name];
    if (raw === undefined) continue;
    let text = wellFormed(raw);
    text = text.replace(/[\t\n\r]+/g, " ");
    text = wsTrim(text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]/g, ""));
    if ([...text].length > MAX_CHARS) throw new DocumentError(`Keep each detail under ${MAX_CHARS} characters.`);
    out[name] = text;
  }
  return out;
}

/** "a, b; c" → ["a", "b", "c"] (ODF stores one element per keyword). */
export const keywordList = (text: string): string[] =>
  text.split(/[,;]/).map(wsTrim).filter((k) => k);

/** Each field's value when every file has the same one, else null ("Several values"). */
export function common(details: DocumentDetails[]): Record<Field, string | null> {
  const out = {} as Record<Field, string | null>;
  for (const name of FIELDS) {
    const values = new Set(details.map((d) => d[name]));
    out[name] = values.size === 1 ? [...values][0] : null;
  }
  return out;
}

// ----------------------------------------------------------------------------- XML as text
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const ENTITIES: Record<string, string> = { lt: "<", gt: ">", amp: "&", quot: '"', apos: "'" };
const charOf = (code: number, fallback: string) =>
  code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : fallback;

export function unescape(text: string): string {
  return text.replace(/&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);/g, (m, e: string) => {
    if (e.startsWith("#x") || e.startsWith("#X")) return charOf(parseInt(e.slice(2), 16), m);
    if (e.startsWith("#")) return charOf(parseInt(e.slice(1), 10), m);
    return ENTITIES[e] ?? m;
  });
}

/** The prefix ``xml`` declares for ``uri`` (the first one), or null. */
export function prefixOf(xml: string, uri: string): string | null {
  const u = esc(uri);
  const m = new RegExp(`\\sxmlns:([\\w.-]+)\\s*=\\s*(?:"${u}"|'${u}')`).exec(xml);
  return m ? m[1] : null;
}

/** The root element's start tag (after the declaration, comments and processing steps). */
function rootStart(xml: string): RegExpExecArray {
  const m = /<(?![?!])[\w.:-]+(?:\s[^>]*?)?\/?>/.exec(xml);
  if (!m) throw new DocumentError(BROKEN_TEXT);
  return m;
}

/** [xml with ``uri`` declared on the root, the prefix to use for it]. Only the root's own
 * declarations count (see the desktop's ``declare``). */
export function declare(xml: string, uri: string, prefix: string): [string, string] {
  const m = rootStart(xml);
  let tag = m[0];
  const have = prefixOf(tag, uri);
  if (have !== null) return [xml, have];
  while (new RegExp(`\\sxmlns:${esc(prefix)}\\s*=`).test(tag)) prefix += "x";
  const end = tag.length - (tag.endsWith("/>") ? 2 : 1);
  tag = `${tag.slice(0, end)} xmlns:${prefix}="${uri}"${tag.slice(end)}`;
  return [xml.slice(0, m.index) + tag + xml.slice(m.index + m[0].length), prefix];
}

/** The text of every ``name`` element (prefixed name), unescaped. */
export function elementTexts(xml: string, name: string): string[] {
  const n = esc(name);
  return [...xml.matchAll(new RegExp(`<${n}(?:\\s[^>]*)?>([\\s\\S]*?)</${n}>`, "g"))].map((m) =>
    unescape(m[1].replace(/<[^>]*>/g, "")),
  );
}

/** ``xml`` with every ``local`` element (in namespace ``uri``) inside ``container`` replaced by
 * one element per value, where the first one was (else at the end of ``container``). */
export function setText(xml: string, container: string, uri: string, local: string, prefix: string, values: string[]): string {
  let p: string;
  [xml, p] = declare(xml, uri, prefix);
  const n = esc(`${p}:${local}`);
  const source = `<${n}(?:\\s[^>]*?)?/>|<${n}(?:\\s[^>]*)?>[\\s\\S]*?</${n}>`;
  const add = values.map((v) => `<${p}:${local}>${escape(v)}</${p}:${local}>`).join("");
  const first = new RegExp(source).exec(xml);
  if (first) {
    const head = xml.slice(0, first.index);
    const tail = xml.slice(first.index + first[0].length);
    return head + add + tail.replace(new RegExp(source, "g"), "");
  }
  if (!add) return xml;
  const c = esc(container);
  const close = new RegExp(`</${c}\\s*>`).exec(xml);
  if (close) return xml.slice(0, close.index) + add + xml.slice(close.index);
  const empty = new RegExp(`<${c}(\\s[^>]*?)?/>`).exec(xml);
  if (empty) {
    return xml.slice(0, empty.index) + `<${container}${empty[1] ?? ""}>${add}</${container}>` +
      xml.slice(empty.index + empty[0].length);
  }
  throw new DocumentError(BROKEN_TEXT);
}

const rootName = (xml: string): string => /<([\w.:-]+)/.exec(rootStart(xml)[0])![1];

const NEW_CORE =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
  `<cp:coreProperties xmlns:cp="${CP}" xmlns:dc="${DC}" xmlns:dcterms="${DCTERMS}" ` +
  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>';

/** ``docProps/core.xml`` (null = the file has none) with the cleaned ``values`` set. */
export function coreXml(xml: string | null, values: Values): string {
  xml = xml ?? NEW_CORE;
  const root = rootName(xml);
  for (const name of FIELDS) {
    const v = values[name];
    if (v === undefined) continue;
    const [uri, local, prefix] = CORE_FIELDS[name];
    xml = setText(xml, root, uri, local, prefix, v ? [v] : []);
  }
  return xml;
}

const NEW_META =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  `<office:document-meta xmlns:office="${OFFICE}" xmlns:meta="${META}" xmlns:dc="${DC}" ` +
  'office:version="1.2"><office:meta></office:meta></office:document-meta>';

/** ``meta.xml`` (null = the file has none) with the cleaned ``values`` set. */
export function odfMeta(xml: string | null, values: Values): string {
  xml = xml ?? NEW_META;
  let op: string;
  [xml, op] = declare(xml, OFFICE, "office");
  const container = `${op}:meta`;
  if (!new RegExp(`<${esc(container)}[\\s/>]`).test(xml)) {
    xml = setText(xml, rootName(xml), OFFICE, "meta", op, [""]);
  }
  for (const name of FIELDS) {
    const text = values[name];
    if (text === undefined) continue;
    const [uri, local, prefix] = ODF_FIELDS[name];
    const items = name === "keywords" ? keywordList(text) : text ? [text] : [];
    xml = setText(xml, container, uri, local, prefix, items);
  }
  return xml;
}

/** ``[Content_Types].xml`` with an override for a new core.xml (kept if it has one). */
export function contentTypes(xml: string): string {
  if (/PartName\s*=\s*["']\/docProps\/core\.xml["']/i.test(xml)) return xml;
  const add = `<Override PartName="/${CORE_PART}" ContentType="${CORE_TYPE}"/>`;
  const m = /<\/(?:[\w.-]+:)?Types\s*>/.exec(xml);
  if (!m) throw new DocumentError(BROKEN_TEXT);
  return xml.slice(0, m.index) + add + xml.slice(m.index);
}

/** ``_rels/.rels`` with a relationship to a new core.xml. */
export function packageRels(xml: string): string {
  if (xml.includes(CORE_REL)) return xml;
  const ids = new Set([...xml.matchAll(/\sId\s*=\s*["']([^"']*)["']/g)].map((m) => m[1]));
  let rid = "rIdUmcCore";
  for (let n = 1; ids.has(rid); ) rid = `rIdUmcCore${++n}`;
  const add = `<Relationship Id="${rid}" Type="${CORE_REL}" Target="${CORE_PART}"/>`;
  const m = /<\/(?:[\w.-]+:)?Relationships\s*>/.exec(xml);
  if (!m) throw new DocumentError(BROKEN_TEXT);
  return xml.slice(0, m.index) + add + xml.slice(m.index);
}

/** ``META-INF/manifest.xml`` with an entry for a new meta.xml. */
export function manifest(xml: string): string {
  if (/full-path\s*=\s*["']meta\.xml["']/.test(xml)) return xml;
  const p = prefixOf(xml, MANIFEST) ?? "manifest";
  const add = `<${p}:file-entry ${p}:full-path="meta.xml" ${p}:media-type="text/xml"/>`;
  const m = new RegExp(`</${esc(p)}:manifest\\s*>`).exec(xml);
  if (!m) throw new DocumentError(BROKEN_TEXT);
  return xml.slice(0, m.index) + add + xml.slice(m.index);
}

/** Where ``_rels/.rels`` says the core properties are (most files: docProps/core.xml). */
export function coreTarget(rels: string | null | undefined): string {
  for (const [rel] of (rels ?? "").matchAll(/<(?:[\w.-]+:)?Relationship\b[^>]*>/g)) {
    if (rel.includes(CORE_REL)) {
      const m = /\sTarget\s*=\s*["']([^"']*)["']/.exec(rel);
      if (m) return unescape(m[1]).replace(/^\/+/, "");
    }
  }
  return CORE_PART;
}

/** {part name: new text} for an Office / ODF file whose parts ``read`` holds. */
export function changedParts(kind: string, read: Parts, values: Values): Record<string, string> {
  if (kind === "office") {
    const target = coreTarget(read["_rels/.rels"]);
    const core = read[target] ?? null;
    const out: Record<string, string> = { [target]: coreXml(core, values) };
    if (core === null) {
      out["[Content_Types].xml"] = contentTypes(read["[Content_Types].xml"] ?? "");
      out["_rels/.rels"] = packageRels(read["_rels/.rels"] ?? "");
    }
    return out;
  }
  const meta = read["meta.xml"] ?? null;
  const out: Record<string, string> = { "meta.xml": odfMeta(meta, values) };
  const man = read["META-INF/manifest.xml"];
  if (meta === null && man !== undefined && man !== null) out["META-INF/manifest.xml"] = manifest(man);
  return out;
}

export function partsNeeded(kind: string, rels: string | null): string[] {
  if (kind === "office") return ["_rels/.rels", coreTarget(rels), "[Content_Types].xml"];
  return ["meta.xml", "META-INF/manifest.xml"];
}

export function detailsFromParts(kind: string, read: Parts): DocumentDetails {
  const d = emptyDetails(kind);
  const xml = (kind === "office" ? read[coreTarget(read["_rels/.rels"])] : read["meta.xml"]) ?? "";
  const table = kind === "office" ? CORE_FIELDS : ODF_FIELDS;
  for (const name of FIELDS) {
    const [uri, local] = table[name];
    const p = prefixOf(xml, uri);
    if (p === null) continue;
    const texts = elementTexts(xml, `${p}:${local}`).map(wsTrim).filter((t) => t);
    d[name] = name === "keywords" ? texts.join(", ") : (texts[0] ?? "");
  }
  return d;
}

// ----------------------------------------------------------------------------- zips
interface Entry {
  name: string;
  nameBytes: Uint8Array;
  central: Uint8Array; // the whole central directory record
  method: number;
  flags: number;
  size: number;
  compressedSize: number;
  offset: number; // of the local header
  crc: number;
}

interface Zip {
  entries: Entry[];
  centralStart: number;
  comment: Uint8Array;
}

const OPEN_TEXT = (name: string) => `“${name}” can't be read as a document — it may be damaged or password-locked.`;

function readZip(bytes: Uint8Array, fileName: string): Zip {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = (at: number) => view.getUint16(at, true);
  const u32 = (at: number) => view.getUint32(at, true);
  let end = bytes.length - 22;
  const stop = Math.max(0, bytes.length - 65557);
  while (end >= stop && u32(end) !== 0x06054b50) end--;
  if (end < stop) throw new DocumentError(OPEN_TEXT(fileName));
  const count = u16(end + 10);
  const centralStart = u32(end + 16);
  if (count === 0xffff || centralStart === 0xffffffff || (end >= 20 && u32(end - 20) === 0x07064b50)) {
    throw new DocumentError("This document is too big for the website — use the desktop app.");
  }
  const entries: Entry[] = [];
  let at = centralStart;
  for (let i = 0; i < count; i++) {
    if (at + 46 > bytes.length || u32(at) !== 0x02014b50) throw new DocumentError(OPEN_TEXT(fileName));
    const nameLen = u16(at + 28);
    const len = 46 + nameLen + u16(at + 30) + u16(at + 32);
    const nameBytes = bytes.subarray(at + 46, at + 46 + nameLen);
    const flags = u16(at + 8);
    entries.push({
      name: new TextDecoder(flags & 0x800 ? "utf-8" : "latin1").decode(nameBytes),
      nameBytes,
      central: bytes.subarray(at, at + len),
      method: u16(at + 10),
      flags,
      crc: u32(at + 16),
      compressedSize: u32(at + 20),
      size: u32(at + 24),
      offset: u32(at + 42),
    });
    at += len;
  }
  return { entries, centralStart, comment: bytes.subarray(end + 22, end + 22 + u16(end + 20)) };
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
function crc32(data: Uint8Array): number {
  let c = ~0;
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return ~c >>> 0;
}

/** One entry's text (UTF-8, a BOM dropped); null when it's missing. */
function partText(bytes: Uint8Array, zip: Zip, name: string): string | null {
  const e = zip.entries.find((x) => x.name === name);
  if (!e) return null;
  if (e.size > MAX_PART_BYTES) throw new DocumentError(BROKEN_TEXT);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const start = e.offset + 30 + view.getUint16(e.offset + 26, true) + view.getUint16(e.offset + 28, true);
  const raw = bytes.subarray(start, start + e.compressedSize);
  let data: Uint8Array;
  if (e.method === 0) data = raw;
  else if (e.method === 8) {
    const chunks: Uint8Array[] = [];
    let length = 0;
    const inflate = new Inflate((chunk) => {
      chunks.push(chunk);
      length += chunk.length;
      if (length > MAX_PART_BYTES) throw new DocumentError(BROKEN_TEXT);
    });
    inflate.push(raw, true);
    data = new Uint8Array(length);
    let o = 0;
    for (const c of chunks) {
      data.set(c, o);
      o += c.length;
    }
  } else throw new DocumentError(BROKEN_TEXT);
  if (crc32(data) !== e.crc) throw new DocumentError(BROKEN_TEXT);
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(data);
  } catch {
    throw new DocumentError(BROKEN_TEXT);
  }
}

export function readParts(bytes: Uint8Array, kind: string, fileName: string): Parts {
  const zip = readZip(bytes, fileName);
  const rels = kind === "office" ? partText(bytes, zip, "_rels/.rels") : null;
  const out: Parts = {};
  for (const name of partsNeeded(kind, rels)) out[name] = partText(bytes, zip, name);
  return out;
}

const le16 = (v: number) => [v & 0xff, (v >>> 8) & 0xff];
const le32 = (v: number) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];

/** ``bytes`` (a zip) with ``parts`` (name → text) replaced or, when missing, added at the end;
 * every other entry is copied byte for byte. */
export function rewriteZip(bytes: Uint8Array, parts: Record<string, string>, fileName: string): Uint8Array {
  const zip = readZip(bytes, fileName);
  const starts = [...zip.entries.map((e) => e.offset), zip.centralStart].sort((a, b) => a - b);
  const spanEnd = (offset: number) => starts.find((s) => s > offset) ?? zip.centralStart;
  const out: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let at = 0;
  const push = (b: Uint8Array) => {
    out.push(b);
    at += b.length;
  };
  const enc = new TextEncoder();
  const write = (name: Uint8Array, text: string, stored: boolean, flags: number, time: number, date: number, old?: Uint8Array) => {
    const data = enc.encode(text);
    const body = stored ? data : deflateSync(data, { level: 6 });
    const method = stored ? 0 : 8;
    const crc = crc32(data);
    const offset = at;
    push(new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...le16(20), ...le16(flags), ...le16(method), ...le16(time),
      ...le16(date), ...le32(crc), ...le32(body.length), ...le32(data.length), ...le16(name.length), 0, 0]));
    push(name);
    push(body);
    if (old) {
      const c = old.slice();
      const v = new DataView(c.buffer);
      v.setUint16(8, flags, true);
      v.setUint16(10, method, true);
      v.setUint32(16, crc, true);
      v.setUint32(20, body.length, true);
      v.setUint32(24, data.length, true);
      v.setUint32(42, offset, true);
      central.push(c);
    } else {
      central.push(new Uint8Array([0x50, 0x4b, 0x01, 0x02, ...le16(20), ...le16(20), ...le16(flags), ...le16(method),
        ...le16(time), ...le16(date), ...le32(crc), ...le32(body.length), ...le32(data.length), ...le16(name.length),
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ...le32(offset), ...name]));
    }
  };
  const done = new Set<string>();
  for (const e of zip.entries) {
    done.add(e.name);
    const text = parts[e.name];
    if (text !== undefined) {
      const v = new DataView(e.central.buffer, e.central.byteOffset, e.central.byteLength);
      write(e.nameBytes, text, e.method === 0, e.flags & 0x0806, v.getUint16(12, true), v.getUint16(14, true), e.central);
      continue;
    }
    const c = e.central.slice();
    new DataView(c.buffer).setUint32(42, at, true);
    central.push(c);
    push(bytes.subarray(e.offset, spanEnd(e.offset)));
  }
  for (const [name, text] of Object.entries(parts)) {
    if (!done.has(name)) write(enc.encode(name), text, false, 0, 0, 0x21); // 1980-01-01, as the desktop
  }
  const cdStart = at;
  for (const c of central) push(c);
  const cdSize = at - cdStart;
  const n = central.length;
  if (n >= 0xffff || at >= 0xffffffff) throw new DocumentError("This document is too big for the website — use the desktop app.");
  push(new Uint8Array([0x50, 0x4b, 0x05, 0x06, 0, 0, 0, 0, ...le16(n), ...le16(n), ...le32(cdSize), ...le32(cdStart),
    ...le16(zip.comment.length)]));
  push(zip.comment);
  const all = new Uint8Array(at);
  let o = 0;
  for (const b of out) {
    all.set(b, o);
    o += b.length;
  }
  return all;
}

/** An Office / ODF file's details (bytes in memory). */
export function readZipDetails(bytes: Uint8Array, kind: string, fileName: string): DocumentDetails {
  return detailsFromParts(kind, readParts(bytes, kind, fileName));
}

/** An Office / ODF file with ``values`` (cleaned here) written into it. */
export function saveZip(bytes: Uint8Array, kind: string, values: Values, fileName: string): Uint8Array {
  const clean = cleanValues(values);
  return rewriteZip(bytes, changedParts(kind, readParts(bytes, kind, fileName), clean), fileName);
}
