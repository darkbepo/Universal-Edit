// JSON text ↔ values — a port of src/umc/dataedit/jsontree.py. Read through the code editor's
// scanner, so both apps accept the same files and every number keeps its spelling (Num).
// Objects are Maps (a plain object would put "2" before "10" and lose the file's key order).

import { scanJson, type Token } from "../codeedit/check";
import { isNum, type Num, strip } from "./cells";

export type Value = Map<string, Value> | Value[] | string | boolean | null | Num;

/** Throws codeedit/check JsonError at the first mistake; an empty text is null. */
export function parse(text: string): Value {
  const tokens = scanJson(text);
  if (!tokens.length) return null;
  return readValue(tokens, 0)[0];
}

function readValue(tokens: Token[], i: number): [Value, number] {
  const tok = tokens[i];
  if (tok.kind === "{") {
    const out = new Map<string, Value>();
    i++;
    while (tokens[i].kind !== "}") {
      const key = JSON.parse(tokens[i].text) as string;
      const [v, next] = readValue(tokens, i + 2); // past the key and ":"
      out.set(key, v);
      i = next;
      if (tokens[i].kind === ",") i++;
    }
    return [out, i + 1];
  }
  if (tok.kind === "[") {
    const items: Value[] = [];
    i++;
    while (tokens[i].kind !== "]") {
      const [v, next] = readValue(tokens, i);
      items.push(v);
      i = next;
      if (tokens[i].kind === ",") i++;
    }
    return [items, i + 1];
  }
  if (tok.kind === "string") return [JSON.parse(tok.text) as string, i + 1];
  if (tok.kind === "number") return [{ num: tok.text }, i + 1];
  return [tok.text === "true" ? true : tok.text === "false" ? false : null, i + 1];
}

const ESCAPE = /["\\\u0000-\u001f]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;
const SHORT: Record<string, string> = { '"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t" };

/** A JSON string; control characters and lone surrogates as \u codes (lower-case hex). */
export function quote(text: string): string {
  return '"' + text.replace(ESCAPE, (ch) => SHORT[ch] ?? `\\u${ch.charCodeAt(0).toString(16).padStart(4, "0")}`) + '"';
}

/** `indent` null = minified (no spaces at all). */
export function dumps(value: Value, indent: string | null, level = 0): string {
  if (value instanceof Map) {
    if (!value.size) return "{}";
    const entries = [...value];
    if (indent === null) return "{" + entries.map(([k, v]) => `${quote(k)}:${dumps(v, null)}`).join(",") + "}";
    const pad = "\n" + indent.repeat(level + 1);
    return "{" + entries.map(([k, v]) => `${pad}${quote(k)}: ${dumps(v, indent, level + 1)}`).join(",") + "\n" + indent.repeat(level) + "}";
  }
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    if (indent === null) return "[" + value.map((v) => dumps(v, null)).join(",") + "]";
    const pad = "\n" + indent.repeat(level + 1);
    return "[" + value.map((v) => pad + dumps(v, indent, level + 1)).join(",") + "\n" + indent.repeat(level) + "]";
  }
  if (typeof value === "string") return quote(value);
  if (isNum(value)) return value.num;
  if (value === null) return "null";
  return value ? "true" : "false";
}

/** The file's indentation step: null when it is all on one line. */
export function detectIndent(text: string): string | null {
  const body = strip(text);
  if (!body.includes("\n")) return null;
  const m = /\n([ \t]+)[^ \t\r\n]/.exec(body);
  return m ? m[1] : "  ";
}
