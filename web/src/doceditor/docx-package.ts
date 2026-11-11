/**
 * The OPC package under a DOCX file, shared by the DOCX writer and reader: the zip, part
 * names (case-insensitive, maybe percent-encoded), relationships and content types, plus the
 * small XML helpers both use. Needs DOMParser / XMLSerializer (the page, not a worker).
 */

import { strFromU8, unzipSync, zipSync, type Zippable } from "fflate";
import { NS, XML_HEAD } from "./docx-template";

const XMLNS = "http://www.w3.org/2000/xmlns/";
const FIXED_MTIME = new Date(2000, 0, 1);
const CONTENT_TYPES = "[Content_Types].xml";

// ----- XML --------------------------------------------------------------------------------

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
  return data ? strFromU8(data) : null;
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
    for (const rel of new Rels(strFromU8(data)).all()) {
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

/** The files of a zip (throws when `bytes` isn't one). */
export function unzipPackage(bytes: Uint8Array): Files {
  return new Map(Object.entries(unzipSync(bytes)));
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
