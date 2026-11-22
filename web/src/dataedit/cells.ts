// One table cell — a port of src/umc/dataedit/cells.py (shared cases:
// tests/fixtures/dataedit_cases.json). CSV / TSV cells are always text. Cells of typed sheets
// (JSON, …) keep their kind: text, a bool, null, a number with its spelling (Num), a list /
// empty object as compact JSON (Raw), or MISSING when the row has no such key.

import { JsonError, scanJson } from "../codeedit/check";

export const WS = " \t\r\n"; // what counts as blank around a number / a JSON file
export const NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

export interface Num {
  readonly num: string; // exactly as written ("1.0" stays "1.0")
}
export interface Raw {
  readonly raw: string; // compact JSON of a list or an empty object
}
export const MISSING = Object.freeze({ missing: true as const });
export type Missing = typeof MISSING;
export type Cell = string | boolean | null | Num | Raw | Missing;

export const isNum = (c: unknown): c is Num => typeof c === "object" && c !== null && "num" in c;
export const isRaw = (c: unknown): c is Raw => typeof c === "object" && c !== null && "raw" in c;
export const isMissing = (c: unknown): c is Missing => typeof c === "object" && c !== null && "missing" in c;

/** The desktop's ==. */
export function sameCell(a: Cell, b: Cell): boolean {
  if (a === b) return true;
  if (isNum(a)) return isNum(b) && a.num === b.num;
  if (isRaw(a)) return isRaw(b) && a.raw === b.raw;
  return isMissing(a) && isMissing(b);
}

/** Python's str.strip(WS). */
export function strip(text: string): string {
  let a = 0, b = text.length;
  while (a < b && WS.includes(text[a])) a++;
  while (b > a && WS.includes(text[b - 1])) b--;
  return text.slice(a, b);
}

/** What the grid shows. */
export function display(cell: Cell): string {
  if (cell === null || isMissing(cell)) return "";
  if (typeof cell === "boolean") return cell ? "true" : "false";
  if (isNum(cell)) return cell.num;
  if (isRaw(cell)) return cell.raw;
  return cell;
}

/** `text` as compact JSON if it is a valid list or object, else null. */
export function compactJson(text: string): string | null {
  let tokens;
  try {
    tokens = scanJson(text);
  } catch (e) {
    if (e instanceof JsonError) return null;
    throw e;
  }
  if (!tokens.length || (tokens[0].kind !== "{" && tokens[0].kind !== "[")) return null;
  return tokens.map((t) => t.text).join("");
}

/** A typed cell: a number, true / false / null, a JSON list / object, else text; a leading '
 * forces text. Untyped sheets (CSV) keep the text as it is. */
export function fromText(text: string, typed: boolean): Cell {
  if (!typed) return text;
  if (text === "" || text === "null") return null;
  if (text === "true" || text === "false") return text === "true";
  if (NUMBER.test(text)) return { num: text };
  if (text.startsWith("'")) return text.slice(1);
  if (text.startsWith("[") || text.startsWith("{")) {
    const raw = compactJson(text);
    if (raw !== null) return { raw };
  }
  return text;
}

/** A value read from a file that only holds text (XML): typed like fromText when that keeps
 * exactly what it shows ("12" → a number, "true" → a bool, "" → null), else text. */
export function guess(text: string): Cell {
  const cell = fromText(text, true);
  return !isRaw(cell) && display(cell) === text ? cell : text;
}

/** The number a cell sorts by: a Num in JSON spelling, or (untyped) text that reads as one.
 * YAML spellings like 0x1F or .inf are numbers too but sort as text. */
export function numberValue(cell: Cell, typed: boolean): number | null {
  const text = isNum(cell) ? cell.num : !typed ? strip(display(cell)) : "";
  return NUMBER.test(text) ? Number(text) : null;
}

/** What the edit box shows: fromText(editText(c), typed) equals c. */
export function editText(cell: Cell, typed: boolean): string {
  if (typed && typeof cell === "string" && cell && !sameCell(fromText(cell, true), cell)) return "'" + cell; // (empty text: shown empty)
  return display(cell);
}
