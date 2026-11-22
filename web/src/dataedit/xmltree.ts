// XML text ↔ a tree of values — a port of src/umc/dataedit/xmltree.py. Our own small reader /
// writer, so both apps read and write the same: attributes are "@name" keys, text beside child
// elements is "#text", repeated child elements become a list, an element with only text is its
// value (typed by cells.guess). The root is a Map { tag → value }.

import { guess, isNum, strip } from "./cells";
import type { Value } from "./jsontree";

export class XmlError extends Error {
  constructor(readonly pos: number, message: string) {
    super(message);
  }
}

export interface Parsed {
  root: Map<string, Value>;
  declaration: string; // "<?xml …?>" as written, or ""
  indent: string | null; // null = all on one line
  lost: string[];
}

const TOKEN = new RegExp(
  "(?<comment><!--.*?-->)" +
    "|<!\\[CDATA\\[(?<cdata>.*?)\\]\\]>" +
    "|(?<pi><\\?.*?\\?>)" +
    "|(?<doctype><!DOCTYPE(?:\"[^\"]*\"|'[^']*'|\\[[^\\]]*\\]|[^>])*>)" +
    "|</(?<end>[^\\s>]+)\\s*>" +
    "|<(?<start>[^\\s/>!?]+)(?<attrs>(?:\\s+[^\\s=/>]+\\s*=\\s*(?:\"[^\"]*\"|'[^']*'))*)\\s*(?<close>/?)>" +
    "|(?<text>[^<]+)",
  "ys",
);
const ATTR = /([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const ENTITY = /&(?:(amp|lt|gt|quot|apos)|#([0-9]+)|#x([0-9a-fA-F]+));/g;
const NAMED: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

export function unescape(text: string): string {
  if (!text.includes("&")) return text;
  return text.replace(ENTITY, (whole, name: string | undefined, dec: string | undefined, hex: string | undefined) => {
    if (name) return NAMED[name];
    const code = dec ? parseInt(dec, 10) : parseInt(hex!, 16);
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : whole;
  });
}

interface XNode {
  tag: string;
  attrs: [string, string][];
  children: [string, Value][];
  texts: string[];
  mixed: boolean; // a child tag came back after another one (order can't be kept)
}

function nodeValue(node: XNode): Value {
  const text = node.texts.join("");
  if (!node.attrs.length && !node.children.length) return guess(strip(text) ? text : "") as Value;
  const out = new Map<string, Value>(node.attrs.map(([k, v]) => [`@${k}`, guess(v) as Value]));
  const listed = new Set<string>();
  for (const [tag, value] of node.children) {
    if (listed.has(tag)) (out.get(tag) as Value[]).push(value);
    else if (out.has(tag)) {
      out.set(tag, [out.get(tag)!, value]);
      listed.add(tag);
    } else out.set(tag, value);
  }
  const trimmed = strip(text);
  if (trimmed) out.set("#text", guess(trimmed) as Value);
  return out;
}

function addChild(parent: XNode, child: [string, Value]): void {
  const tags = parent.children.map(([t]) => t);
  if (tags.length && tags.includes(child[0]) && tags[tags.length - 1] !== child[0]) parent.mixed = true;
  parent.children.push(child);
}

/** Throws XmlError at the first mistake. */
export function parse(text: string): Parsed {
  const stack: XNode[] = [];
  let root: [string, Value] | null = null;
  let declaration = "";
  const lost = new Set<string>();
  let pos = 0;
  const n = text.length;
  const finish = (node: XNode) => {
    const done: [string, Value] = [node.tag, nodeValue(node)];
    if (stack.length) addChild(stack[stack.length - 1], done);
    else root = done;
  };
  while (pos < n) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(text);
    if (!m) throw new XmlError(pos, "A tag isn't closed or has a mistake");
    const g = m.groups!;
    if (g.comment !== undefined) lost.add("comments");
    else if (g.pi !== undefined) {
      if (pos === 0 && g.pi.slice(0, 5) === "<?xml" && " \t\r\n".includes(g.pi[5] ?? "x")) declaration = g.pi;
      else lost.add("processing instructions");
    } else if (g.doctype !== undefined) lost.add("the DOCTYPE");
    else if (g.cdata !== undefined) {
      if (!stack.length) throw new XmlError(pos, "Text outside the root element");
      stack[stack.length - 1].texts.push(g.cdata);
    } else if (g.text !== undefined) {
      if (stack.length) stack[stack.length - 1].texts.push(unescape(g.text));
      else if (strip(g.text)) throw new XmlError(pos, "Text outside the root element");
    } else if (g.end !== undefined) {
      if (!stack.length || stack[stack.length - 1].tag !== g.end) {
        const want = stack.length ? `</${stack[stack.length - 1].tag}>` : "nothing";
        throw new XmlError(pos, `</${g.end}> found where ${want} was expected`);
      }
      const node = stack.pop()!;
      if (node.mixed) lost.add("the order of differently named elements mixed together");
      if (node.children.length && strip(node.texts.join(""))) lost.add("where text sits between child elements");
      finish(node);
    } else {
      if (root !== null && !stack.length) throw new XmlError(pos, "There is more than one root element");
      const attrs: [string, string][] = [...g.attrs.matchAll(ATTR)].map((a) => [a[1], unescape((a[2] ?? a[3]).replace(/[\t\n]/g, " "))]);
      const node: XNode = { tag: g.start, attrs, children: [], texts: [], mixed: false };
      if (g.close) finish(node);
      else stack.push(node);
    }
    pos = TOKEN.lastIndex;
  }
  if (stack.length) throw new XmlError(n, `<${stack[stack.length - 1].tag}> is never closed`);
  if (root === null) throw new XmlError(n, "There is no root element");
  const [tag, value] = root as [string, Value];
  return { root: new Map([[tag, value]]), declaration, indent: detectIndent(text, declaration), lost: [...lost].sort() };
}

/** The indentation step of the first indented element: "" when children start lines without
 * one, null when the root is all on one line. */
export function detectIndent(text: string, declaration = ""): string | null {
  const body = strip(text.slice(declaration.length));
  const m = /\n([ \t]+)</.exec(body);
  if (m) return m[1];
  return body.includes("\n") ? "" : null;
}

// ---------------------------------------------------------------------------- writing
function scalar(value: Value): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (isNum(value)) return value.num;
  return String(value);
}

const escape = (text: string) => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const escapeAttr = (text: string) =>
  escape(text).replaceAll('"', "&quot;").replaceAll("\n", "&#10;").replaceAll("\t", "&#9;").replaceAll("\r", "&#13;");

function element(tag: string, value: Value, indent: string | null, level: number): string {
  if (Array.isArray(value)) value = new Map([["item", value]]); // a list inside a list: <item>s
  let attrs = "", text = "";
  const children: [string, Value][] = [];
  if (value instanceof Map) {
    for (const [k, v] of value) {
      if (k === "#text") text = scalar(v);
      else if (k.startsWith("@")) attrs += ` ${k.slice(1)}="${escapeAttr(scalar(v))}"`;
      else if (Array.isArray(v)) for (const x of v) children.push([k, x]);
      else children.push([k, v]);
    }
  } else text = scalar(value);
  if (!children.length) return text ? `<${tag}${attrs}>${escape(text)}</${tag}>` : `<${tag}${attrs}/>`;
  const pad = indent === null ? "" : "\n" + indent.repeat(level + 1);
  const body = children.map(([t, v]) => pad + element(t, v, indent, level + 1)).join("");
  const end = indent === null ? "" : "\n" + indent.repeat(level);
  return `<${tag}${attrs}>${escape(text)}${body}${end}</${tag}>`;
}

/** The tree as XML text ("\n" line endings, no final newline). */
export function dumps(root: Value, declaration: string, indent: string | null): string {
  let tag = "root", value = root;
  if (root instanceof Map && root.size === 1 && !Array.isArray([...root.values()][0])) [tag, value] = [...root][0];
  const out = element(tag, value, indent, 0);
  return declaration ? `${declaration}\n${out}` : out;
}

