// Sheets, documents and the edits on them — a port of src/umc/dataedit/table.py. Nothing is
// changed in place: an edit returns a new sheet (unchanged rows are shared), so undo keeps
// plain snapshots.

import { findAll, replaceAll as replaceText, type SearchOptions } from "../codeedit/search";
import { type Cell, display, editText, fromText, guess, MISSING, numberValue, sameCell } from "./cells";

export type Row = readonly Cell[];
export const MAX_CELLS = 1_000_000;

export class EditError extends Error {}

export interface Sheet {
  readonly name: string;
  readonly columns: readonly string[];
  readonly rows: readonly Row[];
  readonly typed: boolean; // cells keep their kind (JSON …); false = every cell is text (CSV / TSV)
  readonly header: boolean; // CSV / TSV / XLSX: the first row holds the column names
  readonly tree: boolean; // JSON / YAML / XML: columns are keys (named, unique; no header switch)
}

export interface DataDoc {
  readonly format: string; // "csv", "tsv", "json"
  readonly sheets: readonly Sheet[];
}

/** What a new cell holds: absent in a tree, empty in a spreadsheet, "" in CSV. */
export const blankOf = (sheet: Sheet): Cell => (sheet.tree ? MISSING : sheet.typed ? null : "");

const sameRow = (a: Row, b: Row) => a === b || (a.length === b.length && a.every((c, i) => sameCell(c, b[i])));

/** The desktop's dataclass ==: unchanged rows are the same arrays, so it's quick. */
export function sameSheet(a: Sheet, b: Sheet): boolean {
  if (a === b) return true;
  if (a.name !== b.name || a.typed !== b.typed || a.header !== b.header || a.tree !== b.tree) return false;
  if (a.columns.length !== b.columns.length || a.columns.some((c, i) => c !== b.columns[i])) return false;
  return a.rows.length === b.rows.length && a.rows.every((r, i) => sameRow(r, b.rows[i]));
}

export function sameDoc(a: DataDoc, b: DataDoc): boolean {
  return a === b || (a.format === b.format && a.sheets.length === b.sheets.length && a.sheets.every((s, i) => sameSheet(s, b.sheets[i])));
}

export function withSheet(doc: DataDoc, i: number, sheet: Sheet): DataDoc {
  return { ...doc, sheets: [...doc.sheets.slice(0, i), sheet, ...doc.sheets.slice(i + 1)] };
}

/** Spreadsheet column letters: 0 → A, 25 → Z, 26 → AA. */
export function letter(i: number): string {
  let out = "";
  i += 1;
  while (i) {
    const r = (i - 1) % 26;
    i = Math.floor((i - 1) / 26);
    out = String.fromCharCode(65 + r) + out;
  }
  return out;
}

function checkSize(columns: number, rows: number): void {
  if (columns * rows > MAX_CELLS) throw new EditError(`That would make more than ${MAX_CELLS.toLocaleString("en-US")} cells.`);
}

// ---------------------------------------------------------------------------- cells
/** The cell typed as `text` (unchanged when that's what it shows already). */
export function setCell(sheet: Sheet, r: number, c: number, text: string): Sheet {
  const old = sheet.rows[r][c];
  if (text === editText(old, sheet.typed)) return sheet;
  const row = sheet.rows[r];
  const next = [...row.slice(0, c), fromText(text, sheet.typed), ...row.slice(c + 1)];
  return { ...sheet, rows: [...sheet.rows.slice(0, r), next, ...sheet.rows.slice(r + 1)] };
}

// ---------------------------------------------------------------------------- rows
export function insertRows(sheet: Sheet, at: number, count = 1): Sheet {
  at = Math.max(0, Math.min(at, sheet.rows.length));
  checkSize(sheet.columns.length, sheet.rows.length + count);
  const empty: Row = sheet.columns.map(() => blankOf(sheet));
  return { ...sheet, rows: [...sheet.rows.slice(0, at), ...Array.from({ length: count }, () => empty), ...sheet.rows.slice(at)] };
}

export function deleteRows(sheet: Sheet, indices: Iterable<number>): Sheet {
  const gone = new Set(indices);
  return { ...sheet, rows: sheet.rows.filter((_, i) => !gone.has(i)) };
}

// ---------------------------------------------------------------------------- columns
export function newColumnName(sheet: Sheet): string {
  let n = sheet.columns.length + 1;
  while (sheet.columns.includes(`column${n}`)) n++;
  return `column${n}`;
}

function checkName(sheet: Sheet, name: string, skip: number | null = null): void {
  if (sheet.tree && !name) throw new EditError("A column needs a name.");
  if (sheet.tree && sheet.columns.some((n, i) => n === name && i !== skip)) {
    throw new EditError(`There is already a column called “${name}”.`);
  }
}

export function insertColumn(sheet: Sheet, at: number, name: string | null = null): Sheet {
  at = Math.max(0, Math.min(at, sheet.columns.length));
  if (!sheet.header) name = "";
  else if (name === null) name = newColumnName(sheet);
  else checkName(sheet, name);
  checkSize(sheet.columns.length + 1, sheet.rows.length);
  const blank = blankOf(sheet);
  return {
    ...sheet,
    columns: [...sheet.columns.slice(0, at), name, ...sheet.columns.slice(at)],
    rows: sheet.rows.map((r) => [...r.slice(0, at), blank, ...r.slice(at)]),
  };
}

export function deleteColumns(sheet: Sheet, indices: Iterable<number>): Sheet {
  const gone = new Set(indices);
  const keep = sheet.columns.map((_, i) => i).filter((i) => !gone.has(i));
  return { ...sheet, columns: keep.map((i) => sheet.columns[i]), rows: sheet.rows.map((r) => keep.map((i) => r[i])) };
}

export function renameColumn(sheet: Sheet, c: number, name: string): Sheet {
  if (!sheet.header) throw new EditError("Turn on “First row is the header” to name the columns.");
  if (name === sheet.columns[c]) return sheet;
  checkName(sheet, name, c);
  return { ...sheet, columns: [...sheet.columns.slice(0, c), name, ...sheet.columns.slice(c + 1)] };
}

/** CSV / TSV / XLSX: the first row becomes the column names, or the names become the first row. */
export function setHeader(sheet: Sheet, on: boolean): Sheet {
  if (sheet.tree || on === sheet.header) return sheet;
  if (on) {
    const first = sheet.rows.length ? sheet.rows[0] : sheet.columns.map(() => "");
    return { ...sheet, header: true, columns: first.map(display), rows: sheet.rows.slice(1) };
  }
  const firstRow: Row = sheet.columns.map((n) => (sheet.typed ? guess(n) : n));
  return { ...sheet, header: false, columns: sheet.columns.map(() => ""), rows: sheet.columns.length ? [firstRow, ...sheet.rows] : sheet.rows };
}

// ---------------------------------------------------------------------------- sorting
type Part = [number, number, string];
type Key = [number, number, Part[], string];

/** "item2" before "item10": digit runs compare as numbers, the rest ignoring case. */
function natural(text: string): Part[] {
  return (text.match(/[0-9]+|[^0-9]+/g) ?? []).map((p): Part => ("0123456789".includes(p[0]) ? [0, Number(p), ""] : [1, 0, p.toLowerCase()]));
}

/** Numbers by value, then text in natural order (blanks are kept apart: always last). */
export function sortKey(cell: Cell, typed: boolean): Key {
  const value = numberValue(cell, typed);
  if (value !== null) return [0, value, [], ""];
  const text = display(cell);
  return [1, 0, natural(text), text];
}

const cmp = <T extends number | string>(a: T, b: T) => (a < b ? -1 : a > b ? 1 : 0);

function compareParts(a: Part[], b: Part[]): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const d = cmp(a[i][0], b[i][0]) || cmp(a[i][1], b[i][1]) || cmp(a[i][2], b[i][2]);
    if (d) return d;
  }
  return cmp(a.length, b.length);
}

/** Python's tuple comparison of two sort keys. */
export function compareKeys(a: Key, b: Key): number {
  return cmp(a[0], b[0]) || cmp(a[1], b[1]) || compareParts(a[2], b[2]) || cmp(a[3], b[3]);
}

export const isBlank = (cell: Cell) => display(cell) === "";

/** Stable: equal cells keep their order; blank cells go last either way. */
export function sortRows(sheet: Sheet, c: number, descending = false): Sheet {
  const full = sheet.rows.filter((r) => !isBlank(r[c])).map((r) => [sortKey(r[c], sheet.typed), r] as const);
  const empty = sheet.rows.filter((r) => isBlank(r[c]));
  full.sort((a, b) => (descending ? -1 : 1) * compareKeys(a[0], b[0]));
  return { ...sheet, rows: [...full.map(([, r]) => r), ...empty] };
}

// ---------------------------------------------------------------------------- find
/** [row, column] of every cell whose text matches, row by row. */
export function find(sheet: Sheet, query: string, o: SearchOptions): [number, number][] {
  if (!query) return [];
  const out: [number, number][] = [];
  sheet.rows.forEach((row, r) =>
    row.forEach((cell, c) => {
      const text = display(cell);
      if (text && findAll(text, query, o).length) out.push([r, c]);
    }),
  );
  return out;
}

/** Every match replaced; text stays text, other cells are typed again from the new text. */
export function replaceAll(sheet: Sheet, query: string, replacement: string, o: SearchOptions): [Sheet, number] {
  if (!query) return [sheet, 0];
  let count = 0;
  const rows = sheet.rows.map((row) => {
    let next: Cell[] | null = null;
    row.forEach((cell, c) => {
      const text = display(cell);
      if (!text) return;
      const [changed, n] = replaceText(text, query, replacement, o);
      if (!n) return;
      count += n;
      next ??= [...row];
      next[c] = typeof cell === "string" ? changed : fromText(changed, sheet.typed);
    });
    return next ?? row;
  });
  return [count ? { ...sheet, rows } : sheet, count];
}
