/**
 * The OPC package under a DOCX file, shared by the DOCX writer and reader: the zip, part
 * names (case-insensitive, maybe percent-encoded), relationships and content types, plus the
 * small XML helpers both use. Needs DOMParser / XMLSerializer (the page, not a worker).
 */

import { Inflate, strFromU8, zipSync, type Zippable } from "fflate";
import { NS, XML_HEAD } from "./docx-template";
import { MAX_UNPACKED_BYTES, TOO_LARGE } from "./types";

const XMLNS = "http://www.w3.org/2000/xmlns/";
const FIXED_MTIME = new Date(2000, 0, 1);
const CONTENT_TYPES = "[Content_Types].xml";

// ----- XML --------------------------------------------------------------------------------

// Encodings a declaration can't mean for bytes that start with "<?xml" in ASCII: UTF-16
// (TextDecoder also gives it for "ucs-2" and "unicode"), "x-user-defined" and "replacement".
const NOT_ASCII_BASED = new Set(["utf-16le", "utf-16be", "x-user-defined", "replacement"]);

/**
 * The text of an XML part, decoded like an XML parser does: by its byte order mark, else
 * UTF-16 by its first bytes, else by the encoding its declaration names (Latin-1,
 * windows-1252, …), else UTF-8. A declared UTF-16 part without a byte order mark (or one of
 * the labels TextDecoder maps to UTF-16 or to odd bytes) is read as UTF-8. A part written
 * back is always UTF-8 (`serialise` writes that declaration).
 */
export function decodeXml(data: Uint8Array): string {
  const decode = (label: string, from = 0) => new TextDecoder(label).decode(data.subarray(from));
  const [a, b, c] = data;
  if (a === 0xef && b === 0xbb && c === 0xbf) return decode("utf-8", 3);
  if (a === 0xff && b === 0xfe) return decode("utf-16le", 2);
  if (a === 0xfe && b === 0xff) return decode("utf-16be", 2);
  if (a === 0x3c && b === 0x00) return decode("utf-16le");
  if (a === 0x00 && b === 0x3c) return decode("utf-16be");
  // the declaration is ASCII; only a short prefix is looked at
  let head = "";
  for (let i = 0; i < Math.min(data.length, 200) && data[i] !== 0x3e; i++) {
    head += String.fromCharCode(data[i]);
  }
  const m = /^<\?xml\s[^?]*encoding\s*=\s*["']([A-Za-z][A-Za-z0-9._:-]*)["']/.exec(head);
  if (m) {
    let decoder: TextDecoder | null = null;
    try {
      decoder = new TextDecoder(m[1]);
    } catch {
      /* a name TextDecoder doesn't know: read it as UTF-8 */
    }
    if (decoder && !NOT_ASCII_BASED.has(decoder.encoding)) return decoder.decode(data);
  }
  return decode("utf-8");
}

export function parseXml(text: string): Document {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("Not well-formed XML");
  return doc;
}

export const serialise = (doc: Document): string =>
  XML_HEAD + new XMLSerializer().serializeToString(doc);

/** Creates elements in one part, with the prefixes that part already uses. */
export class Xml {
  private prefixes = new Map<string, string>();
  constructor(readonly doc: Document) {}

  private prefix(key: keyof typeof NS): string {
    let p = this.prefixes.get(key);
    if (p === undefined) {
      const root = this.doc.documentElement;
      const bound = root.lookupPrefix(NS[key]);
      if (bound) p = bound;
      else {
        // a fresh prefix: the template may bind ours to something else
        p = key;
        for (let n = 1; root.lookupNamespaceURI(p) !== null; n++) p = `${key}${n}`;
        root.setAttributeNS(XMLNS, `xmlns:${p}`, NS[key]);
      }
      this.prefixes.set(key, p);
    }
    return p;
  }

  /** `el("w:p", {"w:val": "x", cx: "1"})`: prefixed names are in that namespace. */
  el(name: string, attrs: Record<string, string> = {}, ...children: Element[]): Element {
    const [key, local] = name.split(":") as [keyof typeof NS, string];
    const el = this.doc.createElementNS(NS[key], `${this.prefix(key)}:${local}`);
    for (const [k, val] of Object.entries(attrs)) {
      const at = k.indexOf(":");
      if (at < 0) el.setAttribute(k, val);
      else {
        const ns = k.slice(0, at) as keyof typeof NS;
        el.setAttributeNS(NS[ns], `${this.prefix(ns)}:${k.slice(at + 1)}`, val);
      }
    }
    for (const child of children) el.appendChild(child);
    return el;
  }
}

export const isW = (node: Node | null | undefined, local: string): node is Element =>
  node?.nodeType === 1 &&
  (node as Element).namespaceURI === NS.w &&
  (node as Element).localName === local;
export const wKids = (el: Element, local: string): Element[] =>
  Array.from(el.children).filter((c) => isW(c, local));
export const wAttr = (el: Element | undefined | null, name: string): string | null =>
  el?.getAttributeNS(NS.w, name) ?? null;

// ----- part names -------------------------------------------------------------------------

export type Files = Map<string, Uint8Array>;

export const dirOf = (name: string): string => name.slice(0, name.lastIndexOf("/") + 1);
export const relsOf = (name: string): string =>
  `${dirOf(name)}_rels/${name.slice(dirOf(name).length)}.rels`;

/** The part a relationship of part `from` ("/" for the package) points at. */
export function resolve(from: string, target: string): string {
  let raw = target;
  try {
    raw = decodeURIComponent(target);
  } catch {
    /* keep it as it is */
  }
  const parts = raw.startsWith("/") ? [] : dirOf(from).split("/").filter(Boolean);
  for (const seg of raw.split("/")) {
    if (seg === "..") parts.pop();
    else if (seg && seg !== ".") parts.push(seg);
  }
  return parts.join("/");
}

/** OPC part names are case-insensitive and may be percent-encoded in rels / Overrides. */
export function partKey(name: string): string {
  let out = name;
  try {
    out = decodeURIComponent(name);
  } catch {
    /* keep it as it is */
  }
  return out.toLowerCase();
}

/** The zip entry `name` refers to (`name` itself when there is none). */
export function actualName(files: Files, name: string): string {
  if (files.has(name)) return name;
  const key = partKey(name);
  for (const have of files.keys()) if (partKey(have) === key) return have;
  return name;
}

/** The text of part `name` (matched like `actualName`), or null when there is none. */
export function partText(files: Files, name: string): string | null {
  const data = files.get(actualName(files, name));
  return data ? decodeXml(data) : null;
}

// ----- relationships ----------------------------------------------------------------------

export interface Rel {
  id: string;
  type: string;
  target: string;
  external: boolean;
}

/** A part's relationships (`_rels/*.rels`), with python-docx's rId numbering. */
export class Rels {
  readonly xml: Xml;
  private ids = new Set<string>();
  private next = 1;
  private links = new Map<string, string>();
  constructor(text: string | null) {
    this.xml = new Xml(parseXml(text ?? `<Relationships xmlns="${NS.rels}"/>`));
    for (const r of this.all()) this.ids.add(r.getAttribute("Id") ?? "");
  }
  all(): Element[] {
    return Array.from(this.xml.doc.documentElement.children).filter(
      (c) => c.localName === "Relationship",
    );
  }
  /** Every relationship, by id (the first one of an id wins). */
  entries(): Map<string, Rel> {
    const out = new Map<string, Rel>();
    for (const r of this.all()) {
      const id = r.getAttribute("Id") ?? "";
      if (out.has(id)) continue;
      out.set(id, {
        id,
        type: r.getAttribute("Type") ?? "",
        target: r.getAttribute("Target") ?? "",
        external: r.getAttribute("TargetMode") === "External",
      });
    }
    return out;
  }
  add(type: string, target: string, external = false): string {
    if (external && this.links.has(target)) return this.links.get(target) as string;
    while (this.ids.has(`rId${this.next}`)) this.next++;
    const id = `rId${this.next}`;
    this.ids.add(id);
    const rel = this.xml.doc.createElementNS(NS.rels, "Relationship");
    rel.setAttribute("Id", id);
    rel.setAttribute("Type", type);
    rel.setAttribute("Target", target);
    if (external) {
      rel.setAttribute("TargetMode", "External");
      this.links.set(target, id);
    }
    this.xml.doc.documentElement.appendChild(rel);
    return id;
  }
  target(type: string): string | null {
    const rel = this.all().find((r) => r.getAttribute("Type") === type);
    return rel?.getAttribute("Target") ?? null;
  }
}

/** The relationships of part `name` ("" for the package's own). */
export function relsFor(files: Files, name: string): Rels {
  return new Rels(partText(files, name ? relsOf(name) : "_rels/.rels"));
}

/** Every part reachable from the package relationships (python-docx saves only those). */
export function reachable(files: Files): Set<string> {
  const seen = new Set<string>();
  const stack = [""];
  while (stack.length) {
    const from = stack.pop() as string;
    const relsName = from ? relsOf(from) : "_rels/.rels";
    const data = files.get(relsName);
    if (!data) continue;
    seen.add(relsName);
    for (const rel of new Rels(decodeXml(data)).all()) {
      if (rel.getAttribute("TargetMode") === "External") continue;
      const name = actualName(files, resolve(from || "/", rel.getAttribute("Target") ?? ""));
      if (files.has(name) && !seen.has(name)) {
        seen.add(name);
        stack.push(name);
      }
    }
  }
  return seen;
}

// ----- content types and the zip ----------------------------------------------------------

/** The content type of part `name`: its Override, else the Default for its extension. */
export function contentTypeOf(types: Document, name: string): string | null {
  const key = partKey(name.startsWith("/") ? name.slice(1) : name);
  const base = name.slice(name.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
  let byExt: string | null = null;
  for (const el of Array.from(types.documentElement.children)) {
    if (el.localName === "Override") {
      const part = el.getAttribute("PartName") ?? "";
      if (partKey(part.startsWith("/") ? part.slice(1) : part) === key) {
        return el.getAttribute("ContentType");
      }
    } else if (el.localName === "Default" && byExt === null) {
      if ((el.getAttribute("Extension") ?? "").toLowerCase() === ext) {
        byExt = el.getAttribute("ContentType");
      }
    }
  }
  return byExt;
}

interface ZipEntry {
  name: string;
  method: number;
  compressedSize: number;
  size: number; // the uncompressed size the central directory declares
  offset: number; // of the local header
  crc: number; // CRC-32 of the uncompressed bytes
}

/** The entries of a zip's central directory (ZIP64 included); throws when it has none. */
function zipEntries(bytes: Uint8Array): ZipEntry[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = (at: number) => view.getUint16(at, true);
  const u32 = (at: number) => view.getUint32(at, true);
  const u64 = (at: number) => Number(view.getBigUint64(at, true));
  let end = bytes.length - 22;
  const stop = Math.max(0, bytes.length - 65557);
  while (end >= stop && u32(end) !== 0x06054b50) end--;
  if (end < stop) throw new Error("Not a zip file");
  let count = u16(end + 10);
  let at = u32(end + 16);
  if (end >= 20 && u32(end - 20) === 0x07064b50) {
    const z = u64(end - 12);
    if (z + 56 <= bytes.length && u32(z) === 0x06064b50) {
      count = u64(z + 32);
      at = u64(z + 48);
    }
  }
  const out: ZipEntry[] = [];
  for (let i = 0; i < count; i++) {
    if (at + 46 > bytes.length || u32(at) !== 0x02014b50) throw new Error("Bad zip directory");
    const nameLen = u16(at + 28);
    const extraLen = u16(at + 30);
    const entry: ZipEntry = {
      name: strFromU8(bytes.subarray(at + 46, at + 46 + nameLen), !(u16(at + 8) & 0x800)),
      method: u16(at + 10),
      compressedSize: u32(at + 20),
      size: u32(at + 24),
      offset: u32(at + 42),
      crc: u32(at + 16),
    };
    // ZIP64: the values written as 0xFFFFFFFF follow in the extra field, in this order
    for (let x = at + 46 + nameLen; x + 4 <= at + 46 + nameLen + extraLen; ) {
      const id = u16(x);
      const len = u16(x + 2);
      if (id === 1) {
        let y = x + 4;
        for (const key of ["size", "compressedSize", "offset"] as const) {
          if (entry[key] === 0xffffffff && y + 8 <= x + 4 + len) {
            entry[key] = u64(y);
            y += 8;
          }
        }
      }
      x += 4 + len;
    }
    out.push(entry);
    at += 46 + nameLen + extraLen + u16(at + 32);
  }
  return out;
}

/** The entry names of a zip's central directory only (nothing is inflated); throws when
 * `bytes` isn't a zip. */
export const zipEntryNames = (bytes: Uint8Array): string[] => zipEntries(bytes).map((e) => e.name);

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

/** The running CRC-32 (start at 0) after `data`; feed the chunks in order. */
function crcUpdate(crc: number, data: Uint8Array): number {
  let c = ~crc;
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return ~c >>> 0;
}

export const crc32 = (data: Uint8Array): number => crcUpdate(0, data);

// Compressed bytes inflated at a time: at most ~1000 times this comes out before the output
// is checked against the declared size.
const INFLATE_STEP = 16 * 1024;

/** One entry's bytes; a deflated entry that inflates past its declared size throws as soon
 * as it does (a lying header), so it costs neither memory nor time. The bytes must match the
 * CRC-32 the zip declares, else it throws (a damaged file). */
function entryData(bytes: Uint8Array, e: ZipEntry): Uint8Array {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const local = view.getUint16(e.offset + 26, true) + view.getUint16(e.offset + 28, true);
  const start = e.offset + 30 + local;
  const data = bytes.subarray(start, start + e.compressedSize);
  if (e.method === 0) {
    if (crc32(data) !== e.crc) throw new Error(`${e.name} fails its checksum`);
    return data.slice();
  }
  if (e.method !== 8) throw new Error(`Unknown compression ${e.method} for ${e.name}`);
  const parts: Uint8Array[] = [];
  let length = 0;
  let crc = 0;
  const inflate = new Inflate((chunk) => {
    parts.push(chunk);
    length += chunk.length;
    crc = crcUpdate(crc, chunk);
  });
  for (let i = 0; i < data.length || i === 0; i += INFLATE_STEP) {
    inflate.push(data.subarray(i, i + INFLATE_STEP), i + INFLATE_STEP >= data.length);
    if (length > e.size) throw new Error(`${e.name} inflates past its declared size`);
  }
  if (crc !== e.crc) throw new Error(`${e.name} fails its checksum`);
  const out = new Uint8Array(length);
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

/**
 * The files of a zip (throws when `bytes` isn't one). A package whose entries declare more
 * than MAX_UNPACKED_BYTES unpacked throws TOO_LARGE before anything is inflated (a zip bomb),
 * and an entry is inflated a little at a time and stopped as soon as it passes its declared
 * size, so a lying header costs neither memory nor time.
 */
export function unzipPackage(bytes: Uint8Array): Files {
  const entries = zipEntries(bytes);
  let total = 0;
  for (const e of entries) total += e.size;
  if (total > MAX_UNPACKED_BYTES) throw new Error(TOO_LARGE);
  const files: Files = new Map();
  for (const e of entries) files.set(e.name, entryData(bytes, e));
  return files;
}

/** Zip `files`: content types first, pictures stored, fixed dates (same input, same bytes). */
export function zipPackage(files: Files): Uint8Array {
  const out: Zippable = {};
  const types = files.get(CONTENT_TYPES);
  if (types) out[CONTENT_TYPES] = types;
  for (const [name, data] of files) {
    if (name === CONTENT_TYPES) continue;
    out[name] = name.includes("/media/") ? [data, { level: 0 }] : data;
  }
  return zipSync(out, { level: 6, mtime: FIXED_MTIME });
}
