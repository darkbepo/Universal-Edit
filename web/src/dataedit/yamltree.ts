// YAML text ↔ a tree of values — a port of src/umc/dataedit/yamltree.py. The `yaml` package
// only reads the structure (failsafe schema: every scalar arrives as its text plus whether it
// was quoted, like PyYAML's compose on the desktop); our own `resolve` decides each plain
// scalar's kind, the same in both apps. Numbers keep their spelling (Num). Written by our own
// block-style writer; a string is quoted only when `resolve` would read it back differently.

import { type Document, isAlias, isMap, isScalar, isSeq, type Node, parseAllDocuments } from "yaml";
import { type Num, strip } from "./cells";
import { quote, type Value } from "./jsontree";

export const MAX_DEPTH = 256;

const NULLS = new Set(["", "~", "null", "Null", "NULL"]);
const TRUES = new Set(["true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"]);
const FALSES = new Set(["false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"]);
const NUMBER = new RegExp(
  "^(?:[-+]?[0-9][0-9_]*(?:\\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?" + // 12, 1_000, 1.5, 1., 2e3
    "|[-+]?\\.[0-9][0-9_]*(?:[eE][-+]?[0-9]+)?" + // .5
    "|[-+]?0x[0-9a-fA-F_]+|[-+]?0o[0-7_]+|[-+]?0b[01_]+" +
    "|[-+]?\\.(?:inf|Inf|INF)|\\.(?:nan|NaN|NAN))$",
);

export class YamlError extends Error {
  constructor(readonly line: number, message: string) {
    super(message); // line: 1-based, 0 = unknown
  }
}

export interface Parsed {
  root: Value;
  indent: string;
  startMarker: boolean; // the file began with "---"
  lost: string[];
}

/** A plain (unquoted) scalar's value. */
export function resolve(text: string): Value {
  if (NULLS.has(text)) return null;
  if (TRUES.has(text)) return true;
  if (FALSES.has(text)) return false;
  if (NUMBER.test(text)) return { num: text } as Num;
  return text;
}

/** Throws YamlError (several documents, odd keys, the library's own complaints). */
export function parse(text: string): Parsed {
  const docs = parseAllDocuments(text, { schema: "failsafe", uniqueKeys: false });
  const list = Array.isArray(docs) ? docs : [];
  for (const d of list) {
    const e = d.errors[0];
    if (e) throw new YamlError(e.linePos?.[0]?.line ?? 0, e.message.split("\n")[0].replace(/ at line \d+, column \d+:?$/, ""));
  }
  if (list.length > 1) throw new YamlError(0, "The file holds several YAML documents; the editor opens one");
  const root = list.length ? nodeValue(list[0].contents as Node | null, list[0], 0) : null;
  return { root, indent: detectIndent(text), startMarker: startsWithMarker(text), lost: lostParts(text) };
}

function nodeValue(node: Node | null, doc: Document, depth: number): Value {
  if (depth > MAX_DEPTH) throw new YamlError(0, `Nested too deeply (more than ${MAX_DEPTH} levels)`);
  if (node === null) return null;
  if (isAlias(node)) return nodeValue((node.resolve(doc) ?? null) as Node | null, doc, depth + 1);
  if (isScalar(node)) {
    const text = String(node.value ?? "");
    return node.type === "PLAIN" || node.type === undefined ? resolve(text) : text;
  }
  if (isSeq(node)) return node.items.map((x) => nodeValue(x as Node | null, doc, depth + 1));
  const out = new Map<string, Value>();
  if (isMap(node)) {
    for (const pair of node.items) {
      const key = pair.key as Node | null;
      if (key !== null && !isScalar(key)) {
        throw new YamlError((key.range ? lineOf(doc, key.range[0]) : 0), "A key that is a list or a map can't be shown");
      }
      out.set(key === null ? "" : String(key.value ?? ""), nodeValue(pair.value as Node | null, doc, depth + 1));
    }
  }
  return out;
}

function lineOf(doc: Document, offset: number): number {
  const src = String((doc as unknown as { source?: string }).source ?? "");
  return src ? src.slice(0, offset).split("\n").length : 0;
}

function startsWithMarker(text: string): boolean {
  for (const line of text.split("\n")) {
    const s = strip(line);
    if (s && !s.startsWith("#") && !s.startsWith("%")) return s === "---" || s.startsWith("--- ");
  }
  return false;
}

/** What saving an edited file can't keep (a # at a line start or after a space). */
export const lostParts = (text: string): string[] => (/(?:^|[ \t])#/m.test(text) ? ["comments"] : []);

export function detectIndent(text: string): string {
  const m = /\n( +)[^ \n#]/.exec(text);
  return m ? m[1] : "  ";
}

// ---------------------------------------------------------------------------- writing
const PLAIN_START = /^[^\s\-?:,[\]{}#&*!|>'"%@`]/;

/** Whether `text` can be written unquoted and read back as this same text (a key only needs to
 * be safe to write: whether it was quoted isn't kept). */
function plainOk(text: string, key = false): boolean {
  if (!text || !PLAIN_START.test(text) || text !== strip(text)) return false;
  for (const ch of text) {
    const c = ch.codePointAt(0)!;
    if (c < 0x20 || c === 0x7f || c === 0xfeff || (c >= 0xd800 && c <= 0xdfff)) return false;
  }
  if (text.includes(": ") || text.includes(" #") || text.endsWith(":")) return false;
  if (text.startsWith("---") || text.startsWith("...")) return false;
  return key || resolve(text) === text;
}

export function scalar(value: Value): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object" && "num" in value) return value.num;
  return plainOk(value as string) ? (value as string) : quote(value as string);
}

function inline(value: Value): string | null {
  if (value instanceof Map) return value.size ? null : "{}";
  if (Array.isArray(value)) return value.length ? null : "[]";
  return scalar(value);
}

/** A non-empty map or list, each line starting with `pad`. */
function lines(value: Value, pad: string, indent: string): string[] {
  const out: string[] = [];
  if (value instanceof Map) {
    for (const [k, v] of value) {
      const key = plainOk(k, true) ? k : quote(k);
      const one = inline(v);
      if (one !== null) out.push(`${pad}${key}: ${one}`);
      else out.push(`${pad}${key}:`, ...lines(v, pad + indent, indent));
    }
  } else {
    for (const v of value as Value[]) {
      const one = inline(v);
      if (one !== null) out.push(`${pad}- ${one}`);
      else {
        const sub = lines(v, pad + "  ", indent);
        out.push(`${pad}- ${sub[0].slice(pad.length + 2)}`, ...sub.slice(1));
      }
    }
  }
  return out;
}

/** The tree as YAML text with a final newline. */
export function dumps(root: Value, indent = "  ", startMarker = false): string {
  const one = inline(root);
  const body = one !== null ? [one] : lines(root, "", indent);
  return (startMarker ? "---\n" : "") + body.join("\n") + "\n";
}
