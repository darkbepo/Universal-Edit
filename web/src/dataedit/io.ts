// Data files ↔ documents — a port of src/umc/dataedit/io.py. Text formats go through
// codeedit/textfile, so a save keeps the file's encoding, BOM and line endings; XLSX through
// xlsx.ts (only changed sheets are rewritten). An untouched document saves the bytes as read.

import { JsonError } from "../codeedit/check";
import * as textfile from "../codeedit/textfile";
import { display, MISSING, strip } from "./cells";
import * as csvtext from "./csvtext";
import * as jsontree from "./jsontree";
import type { Value } from "./jsontree";
import { type DataDoc, MAX_CELLS, sameDoc, type Sheet } from "./table";
import * as tree from "./tree";
import * as xlsx from "./xlsx";
import * as xmltree from "./xmltree";
import * as yamltree from "./yamltree";

export const MAX_OPEN_BYTES = 20 * 1024 * 1024;
export const FORMATS = ["csv", "tsv", "json", "yaml", "xml", "xlsx"];
export const TREES = ["json", "yaml", "xml"];
export const NEW_FORMATS = ["csv", "tsv", "json", "yaml", "xml"]; // an empty table (the hub tile)
export const SHEET = "Sheet1";
export const NAMES: Record<string, string> = { csv: "CSV", tsv: "TSV", json: "JSON", yaml: "YAML", xml: "XML", xlsx: "Excel" };

export class DataError extends Error {}

export interface Opened {
  doc: DataDoc; // as read (the history's first snapshot)
  file: textfile.TextFile; // (XLSX: empty; the workbook is `book`)
  isNew: boolean; // made by newDocument: never saved as the (empty) bytes read
  sep: string; // CSV / TSV
  quoteAll: boolean;
  finalNewline: boolean;
  root: Value; // JSON / YAML / XML: the whole tree, and where the table sits in it
  located: tree.Located | null;
  indent: string | null;
  declaration: string; // XML
  startMarker: boolean; // YAML "---"
  book: xlsx.Book | null;
  data: Uint8Array; // XLSX: the workbook as read
  lost: string[]; // what saving an edited file can't keep
}

const DEFAULTS = {
  isNew: false, sep: ",", quoteAll: false, finalNewline: true, root: null, located: null, indent: "  ",
  declaration: "", startMarker: false, book: null, data: new Uint8Array(), lost: [],
};

function tooBig(columns: number, rows: number): void {
  if (columns * rows > MAX_CELLS) throw new DataError(`This file has more than ${MAX_CELLS.toLocaleString("en-US")} cells: too many for the editor.`);
}

function lineCol(text: string, pos: number): [number, number] {
  const before = text.slice(0, pos);
  return [before.split("\n").length, pos - (before.lastIndexOf("\n") + 1) + 1];
}

function treeDoc(ext: string, root: Value): [DataDoc, tree.Located] {
  const located = tree.locate(root);
  const [columns, rows] = tree.tableOf(root, located);
  tooBig(columns.length, rows.length);
  const sheet: Sheet = { name: SHEET, columns, rows, typed: true, header: true, tree: true };
  return [{ format: ext, sheets: [sheet] }, located];
}

/** Throws DataError with a message for the user. */
export function openBytes(data: Uint8Array, extension: string): Opened {
  let ext = extension.toLowerCase().replace(/^\.+/, "");
  if (ext === "yml") ext = "yaml";
  if (!FORMATS.includes(ext)) throw new DataError(`The data editor can't open .${ext} files.`);
  if (data.length > MAX_OPEN_BYTES) throw new DataError("This file is too big for the data editor (over 20 MB).");
  if (ext === "xlsx") {
    let book: xlsx.Book;
    try {
      book = xlsx.read(data);
    } catch (e) {
      if (e instanceof xlsx.XlsxError) throw new DataError(e.message);
      throw e;
    }
    for (const s of book.sheets) tooBig(s.columns.length, s.rows.length + 1);
    return { ...DEFAULTS, doc: { format: ext, sheets: book.sheets }, file: textfile.decode(new Uint8Array()), book, data };
  }
  let f: textfile.TextFile;
  try {
    f = textfile.decode(data);
  } catch {
    throw new DataError("This isn't a text file.");
  }
  const text = f.text;
  if (ext === "csv" || ext === "tsv") {
    const sep = ext === "tsv" ? "\t" : csvtext.sniff(text);
    const parsed = csvtext.parse(text, sep);
    let width = 0; // (a loop: Math.max(...rows) overflows the stack on big files)
    for (const r of parsed.rows) width = Math.max(width, r.length);
    tooBig(width, parsed.rows.length);
    const rows = parsed.rows.map((r) => [...r, ...Array<string>(width - r.length).fill("")]);
    const sheet: Sheet = { name: SHEET, columns: rows[0] ?? [], rows: rows.slice(1), typed: false, header: true, tree: false };
    return { ...DEFAULTS, doc: { format: ext, sheets: [sheet] }, file: f, sep, quoteAll: parsed.quoteAll, finalNewline: parsed.finalNewline || !text };
  }
  const finalNewline = text.endsWith("\n") || !strip(text);
  if (ext === "json") {
    let root: Value;
    try {
      root = jsontree.parse(text);
    } catch (e) {
      if (!(e instanceof JsonError)) throw e;
      const [line, col] = lineCol(text, e.pos);
      throw new DataError(`This JSON has a mistake on line ${line}, column ${col}: ${e.message}.`);
    }
    if (root === null && !strip(text)) root = [];
    const [doc, located] = treeDoc(ext, root);
    return { ...DEFAULTS, doc, file: f, root, located, indent: jsontree.detectIndent(text), finalNewline };
  }
  if (ext === "yaml") {
    let parsed: yamltree.Parsed;
    try {
      parsed = yamltree.parse(text);
    } catch (e) {
      if (!(e instanceof yamltree.YamlError)) throw e;
      throw new DataError(`This YAML has a mistake${e.line ? ` on line ${e.line}` : ""}: ${e.message}.`);
    }
    const root = parsed.root === null && !strip(text) ? [] : parsed.root;
    const [doc, located] = treeDoc(ext, root);
    return { ...DEFAULTS, doc, file: f, root, located, indent: parsed.indent, startMarker: parsed.startMarker, lost: parsed.lost };
  }
  let parsed: xmltree.Parsed;
  try {
    parsed = xmltree.parse(text);
  } catch (e) {
    if (!(e instanceof xmltree.XmlError)) throw e;
    const [line, col] = lineCol(text, e.pos);
    throw new DataError(`This XML has a mistake on line ${line}, column ${col}: ${e.message}.`);
  }
  const [doc, located] = treeDoc(ext, parsed.root);
  return {
    ...DEFAULTS, doc, file: f, root: parsed.root, located, indent: parsed.indent,
    declaration: parsed.declaration, finalNewline, lost: parsed.lost,
  };
}

/** A text format's document as the file's text ("\n" line endings). */
export function textOf(opened: Opened, doc: DataDoc): string {
  const sheet = doc.sheets[0];
  if (doc.format === "csv" || doc.format === "tsv") {
    const rows = sheet.rows.map((r) => r.map(display));
    if (sheet.header) rows.unshift([...sheet.columns]);
    return csvtext.write(rows, opened.sep, opened.quoteAll, opened.finalNewline);
  }
  const root = tree.put(opened.root, opened.located!, sheet.columns, sheet.rows);
  const end = opened.finalNewline ? "\n" : "";
  if (doc.format === "yaml") return yamltree.dumps(root, opened.indent || "  ", opened.startMarker);
  if (doc.format === "xml") return xmltree.dumps(root, opened.declaration, opened.indent) + end;
  return jsontree.dumps(root, opened.indent) + end;
}

/** Throws Error("encoding") when the text doesn't fit the file's encoding (the page then
 * offers UTF-8: textfile.encode(…, { encoding: "utf-8" })). */
export function toBytes(opened: Opened, doc: DataDoc): Uint8Array {
  if (sameDoc(doc, opened.doc) && !opened.isNew) return opened.book ? opened.data : opened.file.raw;
  if (opened.book) return xlsx.write(opened.book, doc.sheets);
  return textfile.encode(opened.file, textOf(opened, doc));
}

/** An empty table to type into (the hub tile). */
export function newDocument(fmt = "csv", columns = 3, rows = 20): Opened {
  if (!NEW_FORMATS.includes(fmt)) throw new DataError(`A new table can't be made as .${fmt}.`);
  const isTree = TREES.includes(fmt);
  const names = Array.from({ length: columns }, (_, i) => `column${i + 1}`);
  const blank = isTree ? MISSING : "";
  const sheet: Sheet = { name: SHEET, columns: names, rows: Array.from({ length: rows }, () => names.map(() => blank)), typed: isTree, header: true, tree: isTree };
  const root: Value = fmt === "xml" ? new Map([["root", new Map([["row", []]])]]) : isTree ? [] : null;
  return {
    ...DEFAULTS,
    doc: { format: fmt, sheets: [sheet] },
    file: textfile.decode(new Uint8Array()),
    isNew: true,
    sep: fmt === "tsv" ? "\t" : ",",
    root,
    located: isTree ? tree.locate(root) : null,
    declaration: fmt === "xml" ? '<?xml version="1.0" encoding="UTF-8"?>' : "",
  };
}
