// Data files ↔ documents — a port of src/umc/dataedit/io.py. The text goes through
// codeedit/textfile, so a save keeps the file's encoding, BOM and line endings; an untouched
// document saves the bytes as read.

import { JsonError } from "../codeedit/check";
import * as textfile from "../codeedit/textfile";
import { display, MISSING, strip } from "./cells";
import * as csvtext from "./csvtext";
import { detectIndent, dumps, parse, type Value } from "./jsontree";
import { type DataDoc, MAX_CELLS, type Row, sameDoc, type Sheet } from "./table";
import * as tree from "./tree";

export const MAX_OPEN_BYTES = 20 * 1024 * 1024;
export const FORMATS = ["csv", "tsv", "json"]; // what this step opens (YAML / XML / XLSX come next)
export const SHEET = "Sheet1";

export class DataError extends Error {}

export interface Opened {
  doc: DataDoc; // as read (the history's first snapshot)
  file: textfile.TextFile;
  isNew: boolean; // made by newDocument: never saved as the (empty) bytes read
  sep: string; // CSV / TSV
  quoteAll: boolean;
  finalNewline: boolean;
  root: Value; // JSON: the whole tree, and where the table sits in it
  located: tree.Located | null;
  indent: string | null;
}

const DEFAULTS = { isNew: false, sep: ",", quoteAll: false, finalNewline: true, root: null, located: null, indent: "  " };

function tooBig(columns: number, rows: number): void {
  if (columns * rows > MAX_CELLS) throw new DataError(`This file has more than ${MAX_CELLS.toLocaleString("en-US")} cells: too many for the editor.`);
}

function lineCol(text: string, pos: number): [number, number] {
  const before = text.slice(0, pos);
  return [before.split("\n").length, pos - (before.lastIndexOf("\n") + 1) + 1];
}

/** Throws DataError with a message for the user. */
export function openBytes(data: Uint8Array, ext: string): Opened {
  ext = ext.toLowerCase().replace(/^\.+/, "");
  if (!FORMATS.includes(ext)) throw new DataError(`The data editor can't open .${ext} files yet.`);
  if (data.length > MAX_OPEN_BYTES) throw new DataError("This file is too big for the data editor (over 20 MB).");
  let f: textfile.TextFile;
  try {
    f = textfile.decode(data);
  } catch {
    throw new DataError("This isn't a text file.");
  }
  if (ext === "csv" || ext === "tsv") {
    const sep = ext === "tsv" ? "\t" : csvtext.sniff(f.text);
    const parsed = csvtext.parse(f.text, sep);
    let width = 0; // (a loop: Math.max(...rows) overflows the stack on big files)
    for (const r of parsed.rows) width = Math.max(width, r.length);
    tooBig(width, parsed.rows.length);
    const rows = parsed.rows.map((r) => [...r, ...Array<string>(width - r.length).fill("")]);
    const sheet: Sheet = { name: SHEET, columns: rows[0] ?? [], rows: rows.slice(1), typed: false, header: true };
    return { ...DEFAULTS, doc: { format: ext, sheets: [sheet] }, file: f, sep, quoteAll: parsed.quoteAll, finalNewline: parsed.finalNewline || !f.text };
  }
  let root: Value;
  try {
    root = parse(f.text);
  } catch (e) {
    if (!(e instanceof JsonError)) throw e;
    const [line, col] = lineCol(f.text, e.pos);
    throw new DataError(`This JSON has a mistake on line ${line}, column ${col}: ${e.message}.`);
  }
  if (root === null && !strip(f.text)) root = [];
  const located = tree.locate(root);
  const [columns, rows] = tree.tableOf(root, located);
  tooBig(columns.length, rows.length);
  const sheet: Sheet = { name: SHEET, columns, rows, typed: true, header: true };
  return {
    ...DEFAULTS,
    doc: { format: ext, sheets: [sheet] },
    file: f,
    root,
    located,
    indent: detectIndent(f.text),
    finalNewline: f.text.endsWith("\n") || !strip(f.text),
  };
}

/** The document as the file's text ("\n" line endings). */
export function textOf(opened: Opened, doc: DataDoc): string {
  const sheet = doc.sheets[0];
  if (doc.format === "csv" || doc.format === "tsv") {
    const rows = sheet.rows.map((r) => r.map(display));
    if (sheet.header) rows.unshift([...sheet.columns]);
    return csvtext.write(rows, opened.sep, opened.quoteAll, opened.finalNewline);
  }
  const root = tree.put(opened.root, opened.located!, sheet.columns, sheet.rows);
  return dumps(root, opened.indent) + (opened.finalNewline ? "\n" : "");
}

/** Throws Error("encoding") when the text doesn't fit the file's encoding (the page then
 * offers UTF-8: textfile.encode(…, { encoding: "utf-8" })). */
export function toBytes(opened: Opened, doc: DataDoc): Uint8Array {
  if (sameDoc(doc, opened.doc) && !opened.isNew) return opened.file.raw;
  return textfile.encode(opened.file, textOf(opened, doc));
}

/** An empty table to type into (the hub tile). */
export function newDocument(fmt = "csv", columns = 3, rows = 20): Opened {
  const typed = fmt === "json";
  const names = Array.from({ length: columns }, (_, i) => `column${i + 1}`);
  const blank = typed ? MISSING : "";
  const sheet: Sheet = { name: SHEET, columns: names, rows: Array.from({ length: rows }, (): Row => names.map(() => blank)), typed, header: true };
  return {
    ...DEFAULTS,
    doc: { format: fmt, sheets: [sheet] },
    file: textfile.decode(new Uint8Array()),
    isNew: true,
    sep: fmt === "tsv" ? "\t" : ",",
    root: typed ? [] : null,
    located: typed ? { path: [], kind: "list", plain: false } : null,
  };
}
