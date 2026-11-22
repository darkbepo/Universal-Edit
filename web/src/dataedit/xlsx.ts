// Excel workbooks (.xlsx) ↔ sheets — a port of src/umc/dataedit/xlsx.py. Our own reader (zip +
// a few regular expressions, the same in both apps): numbers keep the spelling Excel stored,
// shared / inline strings, booleans, formula results and date-formatted numbers as ISO text.
// Saving rewrites only the <sheetData> of changed sheets; every other part is copied as it was.
// A cell still holding what the file had at that place is written back exactly as it was
// (formula, style); a changed one keeps its place's style; ISO text typed where a date was is
// stored as a date. When anything changed, calcChain.xml is dropped (Excel rebuilds it).

import { unzipSync, zipSync } from "fflate";
import { type Cell, display, isMissing, isNum, NUMBER, sameCell } from "./cells";
import { letter, type Row, type Sheet, sameSheet } from "./table";
import { unescape } from "./xmltree";

export const MAX_UNPACKED = 200 * 1024 * 1024; // a bigger workbook is refused (zip bombs)
export const MAX_DAYS = 2958465; // 9999-12-31

export class XlsxError extends Error {}

export interface Original {
  cell: Cell;
  xml: string; // the <c> element exactly as written
  style: string; // its s="…" value ("" = none)
}

export interface SheetPart {
  path: string;
  xml: string;
  data: [number, number]; // where <sheetData …> … </sheetData> sits in it
  prefix: string; // "x:" when the file writes its elements with a prefix
  rowAttrs: Map<number, string>;
  cells: Map<string, Original>; // "row:column" → what was there
}

export interface Book {
  entries: [string, Uint8Array][]; // every zip entry, in order
  parts: SheetPart[];
  sheets: Sheet[]; // as read
  dates: string[]; // per cell style index: "" / "date" / "time"
  date1904: boolean;
}

// ---------------------------------------------------------------------------- dates
const BUILTIN_DATES: Record<number, string> = {
  14: "date", 15: "date", 16: "date", 17: "date", 18: "time", 19: "time",
  20: "time", 21: "time", 22: "date", 45: "time", 46: "time", 47: "time",
};

/** "date", "time" (no day in it) or "" for a number format code. */
export function formatKind(code: string): string {
  const bare = code.replace(/"[^"]*"|\\.|\[[^\]]*\]/g, "").toLowerCase();
  if (!/[dmyhs]/.test(bare) || bare === "general") return "";
  return /[dy]/.test(bare) ? "date" : "time";
}

const DAY_MS = 86400000;
const baseMs = (date1904: boolean) => (date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, 31));
const pad = (n: number, w = 2) => String(n).padStart(w, "0");

/** Python's round(): halves go to the even neighbour. */
function roundHalfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

function isoDay(ms: number): string {
  const d = new Date(ms);
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

/** A date cell's ISO text, or null when the number isn't a date we can show. */
export function serialText(text: string, kind: string, date1904: boolean): string | null {
  const value = Number(text);
  if (value < 0) return null;
  const seconds = roundHalfEven(value * 86400);
  let days = Math.floor(seconds / 86400);
  const secs = seconds - days * 86400;
  if (days > MAX_DAYS - (date1904 ? 1462 : 0)) return null;
  const clock = `${pad(Math.floor(secs / 3600))}:${pad(Math.floor(secs / 60) % 60)}:${pad(secs % 60)}`;
  if (kind === "time" && days === 0) return clock;
  if (!date1904 && days >= 61) days -= 1; // Excel counts a 29 February 1900 that never was
  const day = isoDay(baseMs(date1904) + days * DAY_MS);
  return secs === 0 ? day : `${day} ${clock}`;
}

const ISO = /^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?)?$/;
const CLOCK = /^([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?$/;

function utcDay(y: number, m: number, d: number): number | null {
  if (y < 1) return null;
  const date = new Date(0);
  date.setUTCFullYear(y, m - 1, d);
  date.setUTCHours(0, 0, 0, 0);
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d ? date.getTime() : null;
}

/** ISO text typed into a date cell → the number Excel stores (null: not a date). */
export function textSerial(text: string, date1904: boolean): string | null {
  let days: number;
  let clock: (string | undefined)[];
  let m = ISO.exec(text);
  if (m) {
    const ms = utcDay(Number(m[1]), Number(m[2]), Number(m[3]));
    if (ms === null) return null;
    days = Math.round((ms - baseMs(date1904)) / DAY_MS);
    if (days < (date1904 ? 0 : 1)) return null;
    if (!date1904 && days >= 60) days += 1;
    clock = m.slice(4, 7);
  } else {
    m = CLOCK.exec(text);
    if (!m) return null;
    days = 0;
    clock = m.slice(1, 4);
  }
  const [hh, mm, ss] = clock.map((x) => (x ? Number(x) : 0));
  if (hh > 23 || mm > 59 || ss > 59) return null;
  const secs = hh * 3600 + mm * 60 + ss;
  if (secs === 0) return String(days);
  return (days + secs / 86400).toFixed(10).replace(/0+$/, "").replace(/\.$/, "");
}

// ---------------------------------------------------------------------------- reading
const tag = (name: string) => `<(?:[\\w.-]+:)?${name}\\b`;
const end = (name: string) => `</(?:[\\w.-]+:)?${name}\\s*>`;
const re = (source: string, flags = "gs") => new RegExp(source, flags);

const ATTR = /([\w.:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const SHEET_DATA = re(tag("sheetData") + "[^>]*?(?:/>|>.*?" + end("sheetData") + ")", "s");
const ROW = re(tag("row") + "([^>]*?)(?:/>|>(.*?)" + end("row") + ")");
const CELL = re(tag("c") + "([^>]*?)(?:/>|>(.*?)" + end("c") + ")");
const V = re(tag("v") + "[^>]*>(.*?)" + end("v"), "s");
const IS = re(tag("is") + "[^>]*>(.*?)" + end("is"), "s");
const T = re(tag("t") + "[^>]*?(?:/>|>(.*?)" + end("t") + ")");
const RPH = re(tag("rPh") + ".*?" + end("rPh"));
const SI = re(tag("si") + "[^>]*?(?:/>|>(.*?)" + end("si") + ")");
const NUMFMT = re(tag("numFmt") + "([^>]*?)/?>");
const CELL_XFS = re(tag("cellXfs") + "[^>]*>(.*?)" + end("cellXfs"), "s");
const XF = re(tag("xf") + "([^>]*?)/?>");
const SHEET = re(tag("sheet") + "([^>]*?)/?>");
const REL = re(tag("Relationship") + "([^>]*?)/?>");
const PR = re(tag("workbookPr") + "([^>]*?)/?>", "s");
const ESCAPED = /_x([0-9A-Fa-f]{4})_/g;
const REF = /^([A-Za-z]{1,3})([0-9]+)$/;

/** Plain 0-9 digits as a number. */
const int = (text: string): number | null => (/^[0-9]+$/.test(text) ? Number(text) : null);

export function attrs(text: string): Map<string, string> {
  return new Map([...text.matchAll(ATTR)].map((m) => [m[1], m[2] ?? m[3]]));
}

const unEscaped = (text: string) => text.replace(ESCAPED, (_, hex: string) => String.fromCharCode(parseInt(hex, 16)));

/** The text of <t> runs (phonetic guides left out), XML and Excel escapes undone. */
function runText(xml: string): string {
  const raw = [...xml.replace(RPH, "").matchAll(T)].map((m) => unescape(m[1] ?? "")).join("");
  return unEscaped(raw);
}

export function columnIndex(letters: string): number {
  let n = 0;
  for (const ch of letters.toUpperCase()) n = n * 26 + ch.charCodeAt(0) - 64;
  return n - 1;
}

function resolveTarget(target: string): string {
  if (target.startsWith("/")) return target.slice(1);
  const parts = ["xl"];
  for (const piece of target.split("/")) {
    if (piece === "..") parts.pop();
    else if (piece !== "" && piece !== ".") parts.push(piece);
  }
  return parts.join("/");
}

const decoder = new TextDecoder("utf-8");

/** Throws XlsxError with a message for the user. */
export function read(data: Uint8Array): Book {
  let entries: Record<string, Uint8Array>;
  let total = 0;
  try {
    entries = unzipSync(data, {
      filter: (f) => {
        total += f.originalSize;
        if (total > MAX_UNPACKED) throw new XlsxError("This workbook unpacks to more than 200 MB: too big for the editor.");
        return !f.name.endsWith("/");
      },
    });
  } catch (e) {
    if (e instanceof XlsxError) throw e;
    throw new XlsxError("This isn't a valid Excel (XLSX) file.");
  }
  const part = (name: string) => (entries[name] ? decoder.decode(entries[name]) : "");
  const workbook = part("xl/workbook.xml");
  if (!workbook) throw new XlsxError("This isn't a valid Excel (XLSX) file.");
  const shared = [...part("xl/sharedStrings.xml").matchAll(SI)].map((m) => runText(m[1] ?? ""));
  const styles = part("xl/styles.xml");
  const formats = new Map<number | null, string>();
  for (const m of styles.matchAll(NUMFMT)) {
    const a = attrs(m[1]);
    formats.set(int(a.get("numFmtId") ?? ""), formatKind(unescape(a.get("formatCode") ?? "")));
  }
  const xfs = CELL_XFS.exec(styles);
  const dates = [...(xfs ? xfs[1] : "").matchAll(XF)].map((m) => {
    const n = int(attrs(m[1]).get("numFmtId") ?? "0") || 0;
    return n ? (formats.get(n) ?? BUILTIN_DATES[n] ?? "") : "";
  });
  const pr = PR.exec(workbook);
  const date1904 = !!pr && ["1", "true"].includes(attrs(pr[1]).get("date1904") ?? "");
  const rels = new Map<string, string>();
  for (const m of part("xl/_rels/workbook.xml.rels").matchAll(REL)) {
    const a = attrs(m[1]);
    rels.set(a.get("Id") ?? "", a.get("Target") ?? "");
  }
  const parts: SheetPart[] = [];
  const sheets: Sheet[] = [];
  for (const m of workbook.matchAll(SHEET)) {
    const a = attrs(m[1]);
    const rid = [...a].find(([k]) => k.endsWith(":id"))?.[1] ?? "";
    const path = resolveTarget(rels.get(rid) ?? "");
    if (!(path in entries)) continue;
    const [p, s] = readSheet(path, part(path), unescape(a.get("name") ?? "Sheet"), shared, dates, date1904);
    parts.push(p);
    sheets.push(s);
  }
  if (!sheets.length) throw new XlsxError("This workbook has no sheets the editor can show.");
  return { entries: Object.entries(entries), parts, sheets, dates, date1904 };
}

function styleKind(style: string, dates: string[]): string {
  const n = int(style);
  return n !== null && n < dates.length ? dates[n] : "";
}

function cellValue(a: Map<string, string>, body: string, shared: string[], kind: string, date1904: boolean): Cell {
  const t = a.get("t") ?? "n";
  const v = V.exec(body);
  const raw = v ? unescape(v[1]) : null;
  if (t === "s") {
    const index = int((raw ?? "").trim());
    return index !== null && index < shared.length ? shared[index] : "";
  }
  if (t === "inlineStr") {
    const m = IS.exec(body);
    return m ? runText(m[1]) : "";
  }
  if (t === "b") return raw === null ? null : raw.trim() === "1";
  if (t === "str" || t === "e" || t === "d") return unEscaped(raw ?? "");
  if (raw === null || !raw.trim()) return null;
  const text = raw.trim();
  if (!NUMBER.test(text)) return text;
  if (kind) {
    const shown = serialText(text, kind, date1904);
    if (shown !== null) return shown;
  }
  return { num: text };
}

function readSheet(path: string, xml: string, name: string, shared: string[], dates: string[], date1904: boolean): [SheetPart, Sheet] {
  const m = SHEET_DATA.exec(xml);
  const data: [number, number] = m ? [m.index, m.index + m[0].length] : [xml.length, xml.length];
  const prefix = m ? (/^<([\w.-]+:)?/.exec(m[0])?.[1] ?? "") : "";
  const rows = new Map<number, Map<number, Cell>>();
  const rowAttrs = new Map<number, string>();
  const cells = new Map<string, Original>();
  let r = 0;
  for (const rm of (m ? m[0] : "").matchAll(ROW)) {
    const ra = attrs(rm[1]);
    r = int(ra.get("r") ?? "") || r + 1;
    const kept = [...ra].filter(([k]) => k !== "r" && k !== "spans").map(([k, v]) => ` ${k}="${v}"`).join("");
    if (kept) rowAttrs.set(r, kept);
    let c = -1;
    for (const cm of (rm[2] ?? "").matchAll(CELL)) {
      const ca = attrs(cm[1]);
      const ref = REF.exec(ca.get("r") ?? "");
      c = ref ? columnIndex(ref[1]) : c + 1;
      const style = ca.get("s") ?? "";
      const cell = cellValue(ca, cm[2] ?? "", shared, styleKind(style, dates), date1904);
      cells.set(`${r}:${c}`, { cell, xml: cm[0], style });
      if (!rows.has(r)) rows.set(r, new Map());
      rows.get(r)!.set(c, cell);
    }
  }
  let height = 0, width = 0;
  for (const [i, cs] of rows) {
    height = Math.max(height, i);
    for (const j of cs.keys()) width = Math.max(width, j + 1);
  }
  const grid: Row[] = Array.from({ length: height }, (_, i) => Array.from({ length: width }, (_, j) => rows.get(i + 1)?.get(j) ?? null));
  const columns = grid.length ? grid[0].map(display) : Array<string>(width).fill("");
  const sheet: Sheet = { name, columns, rows: grid.slice(1), typed: true, header: true, tree: false };
  return [{ path, xml, data, prefix, rowAttrs, cells }, sheet];
}

// ---------------------------------------------------------------------------- writing
function escape(text: string): string {
  return text
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\x00-\x08\x0b-\x1f]|_(?=x[0-9A-Fa-f]{4}_)/g, (ch) => `_x${ch.charCodeAt(0).toString(16).toUpperCase().padStart(4, "0")}_`);
}

function newCell(p: string, ref: string, cell: Cell, orig: Original | undefined, book: Book): string {
  const style = orig?.style ?? "";
  const s = style ? ` s="${style}"` : "";
  if (cell === null || isMissing(cell)) return s ? `<${p}c r="${ref}"${s}/>` : "";
  if (typeof cell === "boolean") return `<${p}c r="${ref}"${s} t="b"><${p}v>${cell ? 1 : 0}</${p}v></${p}c>`;
  if (isNum(cell) && NUMBER.test(cell.num)) return `<${p}c r="${ref}"${s}><${p}v>${cell.num}</${p}v></${p}c>`;
  const text = display(cell);
  if (styleKind(style, book.dates)) {
    const serial = textSerial(text, book.date1904);
    if (serial !== null) return `<${p}c r="${ref}"${s}><${p}v>${serial}</${p}v></${p}c>`;
  }
  if (!text) return s ? `<${p}c r="${ref}"${s}/>` : "";
  return `<${p}c r="${ref}"${s} t="inlineStr"><${p}is><${p}t xml:space="preserve">${escape(text)}</${p}t></${p}is></${p}c>`;
}

/** The sheet part with its cells rewritten from `sheet`. */
export function sheetXml(part: SheetPart, sheet: Sheet, book: Book): string {
  const p = part.prefix;
  const grid: Row[] = sheet.header ? [sheet.columns.map((n) => (n ? n : null)), ...sheet.rows] : [...sheet.rows];
  const out: string[] = [];
  let lastR = 0, lastC = -1;
  grid.forEach((row, i) => {
    const r = i + 1;
    const cells: string[] = [];
    row.forEach((cell, c) => {
      const orig = part.cells.get(`${r}:${c}`);
      const same = orig !== undefined && (sameCell(orig.cell, cell) || (sheet.header && i === 0 && display(orig.cell) === display(cell)));
      const xml = same ? orig!.xml : newCell(p, `${letter(c)}${r}`, cell, orig, book);
      if (xml) {
        cells.push(xml);
        lastR = r;
        lastC = Math.max(lastC, c);
      }
    });
    const extra = part.rowAttrs.get(r) ?? "";
    if (cells.length || extra) out.push(cells.length ? `<${p}row r="${r}"${extra}>${cells.join("")}</${p}row>` : `<${p}row r="${r}"${extra}/>`);
  });
  const data = out.length ? `<${p}sheetData>${out.join("")}</${p}sheetData>` : `<${p}sheetData/>`;
  const [a, b] = part.data;
  const xml = part.xml.slice(0, a) + data + part.xml.slice(b);
  const ref = lastC >= 0 && !(lastR === 1 && lastC === 0) ? `A1:${letter(lastC)}${lastR}` : "A1";
  return xml.replace(new RegExp("(" + tag("dimension") + '[^>]*?\\bref=)"[^"]*"'), (_, head: string) => `${head}"${ref}"`);
}

const encoder = new TextEncoder();

function withoutCalcChain(entries: Map<string, Uint8Array>): void {
  if (!entries.has("xl/calcChain.xml")) return;
  entries.delete("xl/calcChain.xml");
  const fixes: [string, RegExp][] = [
    ["xl/_rels/workbook.xml.rels", re(tag("Relationship") + "[^>]*?calcChain[^>]*?/>")],
    ["[Content_Types].xml", re(tag("Override") + "[^>]*?/xl/calcChain\\.xml[^>]*?/>")],
  ];
  for (const [name, pattern] of fixes) {
    const data = entries.get(name);
    if (data) entries.set(name, encoder.encode(decoder.decode(data).replace(pattern, "")));
  }
}

/** Every zip entry after saving `sheets` (unchanged ones as they were). */
export function changedParts(book: Book, sheets: readonly Sheet[]): Map<string, Uint8Array> {
  const entries = new Map(book.entries);
  let changed = false;
  book.parts.forEach((part, i) => {
    if (!sameSheet(sheets[i], book.sheets[i])) {
      entries.set(part.path, encoder.encode(sheetXml(part, sheets[i], book)));
      changed = true;
    }
  });
  if (changed) withoutCalcChain(entries);
  return entries;
}

export function write(book: Book, sheets: readonly Sheet[]): Uint8Array {
  return zipSync(Object.fromEntries(changedParts(book, sheets)), { level: 6 });
}
