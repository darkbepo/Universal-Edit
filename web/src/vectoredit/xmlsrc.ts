// An XML tree that remembers its source text — a line-by-line port of
// src/umc/vectoredit/xmlsrc.py. Untouched nodes are written back as they were read; a changed
// element rebuilds only its start tag. Same bytes as the desktop.

export const MAX_DEPTH = 256;

const NAME = "[A-Za-z_:\\u00C0-\\uFFFF][^ \\t\\r\\n/>=<\"'&]*";
const START = new RegExp(`<(${NAME})`, "y");
const ATTR = new RegExp(`[ \\t\\r\\n]+(${NAME})[ \\t\\r\\n]*=[ \\t\\r\\n]*(?:"([^"<]*)"|'([^'<]*)')`, "y");
const TAG_END = /[ \t\r\n]*(\/?)>/y;
const END = new RegExp(`</(${NAME})[ \\t\\r\\n]*>`, "y");
const REF = /&(#x[0-9A-Fa-f]+|#[0-9]+|[A-Za-z]+);/g;
const LONE_AMP = /&(?!(#x[0-9A-Fa-f]+|#[0-9]+|[A-Za-z]+);)/;
const NAMED: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

export class XmlError extends Error {}

export class Text {
  readonly kind = "text";
  constructor(readonly raw: string) {}
  get value(): string {
    return unescape(this.raw);
  }
}

/** A comment, processing instruction, CDATA section or doctype, kept as written. */
export class Other {
  readonly kind = "other";
  constructor(readonly raw: string) {}
  get cdata(): string | null {
    return this.raw.startsWith("<![CDATA[") ? this.raw.slice(9, -3) : null;
  }
}

export type Attr = [string, string];
export type Node = Element | Text | Other;

export class Element {
  readonly kind = "element";
  constructor(
    readonly name: string,
    readonly attrs: readonly Attr[] = [],
    readonly children: readonly Node[] = [],
    readonly startSrc: string | null = null,
    readonly endSrc: string | null = null,
    readonly src: string | null = null,
  ) {}

  get(name: string, fallback: string | null = null): string | null {
    for (const [k, v] of this.attrs) if (k === name) return v;
    return fallback;
  }

  /** Set (or with null remove) one attribute; a new one goes last. */
  set(name: string, value: string | null): Element {
    const attrs: Attr[] = this.attrs.filter(([k]) => k !== name || value !== null).map((a) => [a[0], a[1]]);
    if (value !== null) {
      const i = attrs.findIndex(([k]) => k === name);
      if (i >= 0) attrs[i] = [name, value];
      else attrs.push([name, value]);
    }
    if (sameAttrs(attrs, this.attrs)) return this;
    return new Element(this.name, attrs, this.children, null, this.endSrc, null);
  }

  withChildren(children: readonly Node[]): Element {
    if (children.length === this.children.length && children.every((c, i) => c === this.children[i])) {
      return this;
    }
    return new Element(this.name, this.attrs, [...children], this.startSrc, this.endSrc, null);
  }

  get elements(): Element[] {
    return this.children.filter((c): c is Element => c instanceof Element);
  }

  /** All text inside (CDATA included), as a browser's `textContent`. */
  text(): string {
    let out = "";
    for (const c of this.children) {
      if (c instanceof Text) out += c.value;
      else if (c instanceof Element) out += c.text();
      else if (c.cdata !== null) out += c.cdata;
    }
    return out;
  }
}

function sameAttrs(a: readonly Attr[], b: readonly Attr[]): boolean {
  return a.length === b.length && a.every(([k, v], i) => b[i][0] === k && b[i][1] === v);
}

export class Document {
  constructor(
    readonly before: readonly Node[],
    readonly root: Element,
    readonly after: readonly Node[],
  ) {}

  withRoot(root: Element): Document {
    return new Document(this.before, root, this.after);
  }
}

export function unescape(raw: string): string {
  if (raw.includes("&") && LONE_AMP.test(raw)) throw new XmlError("A lone '&' in the text.");
  return raw.replace(REF, (_m, ref: string) => {
    if (ref.startsWith("#x")) return char(parseInt(ref.slice(2), 16));
    if (ref.startsWith("#")) return char(parseInt(ref.slice(1), 10));
    if (ref in NAMED) return NAMED[ref];
    throw new XmlError(`Unknown entity &${ref};`);
  });
}

function char(code: number): string {
  if (code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) {
    throw new XmlError(`Bad character reference ${code}.`);
  }
  return String.fromCodePoint(code);
}

const attrValue = (raw: string) =>
  unescape(raw.replace(/\r\n/g, "\n").replace(/\t/g, " ").replace(/\n/g, " ").replace(/\r/g, " "));

export const escapeAttr = (v: string) =>
  v
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;")
    .replace(/\t/g, "&#9;")
    .replace(/\n/g, "&#10;")
    .replace(/\r/g, "&#13;");

export const escapeText = (v: string) => v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const line = (text: string, pos: number) => text.slice(0, pos).split("\n").length;

/** End of the comment / PI / CDATA / doctype starting at `pos`. */
function other(text: string, pos: number): number {
  for (const [opener, closer] of [
    ["<!--", "-->"],
    ["<![CDATA[", "]]>"],
    ["<?", "?>"],
  ]) {
    if (text.startsWith(opener, pos)) {
      const end = text.indexOf(closer, pos + opener.length);
      if (end < 0) throw new XmlError(`Unclosed ${opener} on line ${line(text, pos)}.`);
      return end + closer.length;
    }
  }
  if (text.startsWith("<!DOCTYPE", pos)) {
    let depth = 0;
    for (let k = pos; k < text.length; k++) {
      const ch = text[k];
      if (ch === "[") depth++;
      else if (ch === "]") depth--;
      else if (ch === ">" && depth <= 0) {
        if (text.slice(pos, k + 1).includes("<!ENTITY")) {
          throw new XmlError("Files that declare their own entities aren't opened.");
        }
        return k + 1;
      }
    }
    throw new XmlError("Unclosed <!DOCTYPE.");
  }
  throw new XmlError(`Unexpected '<!' on line ${line(text, pos)}.`);
}

const sticky = (re: RegExp, text: string, pos: number) => {
  re.lastIndex = pos;
  return re.exec(text);
};

type Open = { name: string; attrs: Attr[]; startSrc: string; startPos: number; children: Node[] };

export function parse(text: string): Document {
  if (text.startsWith("﻿")) throw new XmlError("Decode the file first (it starts with a byte order mark).");
  const before: Node[] = [];
  const after: Node[] = [];
  let root: Element | null = null;
  const stack: Open[] = [];
  const top = (node: Node) => (root === null ? before : after).push(node);
  let pos = 0;
  const n = text.length;
  while (pos < n) {
    let lt = text.indexOf("<", pos);
    if (lt < 0) lt = n;
    if (lt > pos) {
      const raw = text.slice(pos, lt);
      if (stack.length) {
        unescape(raw);
        stack[stack.length - 1].children.push(new Text(raw));
      } else if (raw.replace(/[ \t\r\n]/g, "")) {
        throw new XmlError(`Text outside the root element on line ${line(text, pos)}.`);
      } else top(new Text(raw));
      pos = lt;
      continue;
    }
    if (text.startsWith("</", pos)) {
      const m = sticky(END, text, pos);
      if (!m || !stack.length || stack[stack.length - 1].name !== m[1]) {
        throw new XmlError(`Unexpected end tag on line ${line(text, pos)}.`);
      }
      const o = stack.pop()!;
      const end = pos + m[0].length;
      const el = new Element(o.name, o.attrs, o.children, o.startSrc, m[0], text.slice(o.startPos, end));
      pos = end;
      if (stack.length) stack[stack.length - 1].children.push(el);
      else root = el;
      continue;
    }
    if (text.startsWith("<!", pos) || text.startsWith("<?", pos)) {
      const end = other(text, pos);
      const node = new Other(text.slice(pos, end));
      if (stack.length) {
        if (node.raw.startsWith("<!DOCTYPE") || node.raw.startsWith("<?xml ")) {
          throw new XmlError(`Misplaced declaration on line ${line(text, pos)}.`);
        }
        stack[stack.length - 1].children.push(node);
      } else if (node.raw.startsWith("<![CDATA[")) throw new XmlError("CDATA outside the root element.");
      else top(node);
      pos = end;
      continue;
    }
    const m = sticky(START, text, pos);
    if (!m) throw new XmlError(`Bad '<' on line ${line(text, pos)}.`);
    if (root !== null && !stack.length) throw new XmlError(`A second root element on line ${line(text, pos)}.`);
    const name = m[1];
    let k = pos + m[0].length;
    const attrs: Attr[] = [];
    for (;;) {
      const a = sticky(ATTR, text, k);
      if (!a) break;
      const key = a[1];
      if (attrs.some(([old]) => old === key)) {
        throw new XmlError(`Attribute ${key} twice on line ${line(text, pos)}.`);
      }
      attrs.push([key, attrValue(a[2] !== undefined ? a[2] : a[3])]);
      k += a[0].length;
    }
    const e = sticky(TAG_END, text, k);
    if (!e) throw new XmlError(`Bad start tag <${name}> on line ${line(text, pos)}.`);
    const tagEnd = k + e[0].length;
    const startSrc = text.slice(pos, tagEnd);
    if (e[1]) {
      const el = new Element(name, attrs, [], startSrc, "", startSrc);
      if (stack.length) stack[stack.length - 1].children.push(el);
      else root = el;
    } else {
      if (stack.length >= MAX_DEPTH) throw new XmlError("The drawing is nested too deeply.");
      stack.push({ name, attrs, startSrc, startPos: pos, children: [] });
    }
    pos = tagEnd;
  }
  if (stack.length) throw new XmlError(`<${stack[stack.length - 1].name}> is never closed.`);
  if (root === null) throw new XmlError("No root element.");
  return new Document(before, root, after);
}

export function writeNode(node: Node): string {
  if (!(node instanceof Element)) return node.raw;
  if (node.src !== null) return node.src;
  const closedItself = node.endSrc === "";
  let start = node.startSrc;
  if (start === null || (closedItself && node.children.length)) {
    start = "<" + node.name + node.attrs.map(([k, v]) => ` ${k}="${escapeAttr(v)}"`).join("");
    start += node.children.length ? ">" : "/>";
  }
  if (start.endsWith("/>")) return start;
  const inner = node.children.map(writeNode).join("");
  const end = node.endSrc ? node.endSrc : `</${node.name}>`;
  return start + inner + end;
}

export function write(doc: Document): string {
  return doc.before.map(writeNode).join("") + writeNode(doc.root) + doc.after.map(writeNode).join("");
}

/** A new element (no source text: written from its parts). */
export function element(name: string, attrs: Attr[] = [], children: Node[] = []): Element {
  return new Element(name, attrs, children);
}
