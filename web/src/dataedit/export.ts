// Save as another format — a port of src/umc/dataedit/export.py. The document as plain values,
// written by the data converter (converters/data.ts), so "Save as" gives exactly what converting
// would. Tree formats are rebuilt with the rest of their file; tables become a list of rows (one
// list per sheet when a workbook has several). Numbers become real numbers, CSV text that reads
// as a number or true / false too (the converter's rule).

import { dataConverter, scalar, unflatten, write } from "../converters/data";
import { defaults } from "../core/options";
import { type Cell, isMissing, isNum, isRaw, NUMBER } from "./cells";
import type { Opened } from "./io";
import { TREES } from "./io";
import type { Value } from "./jsontree";
import type { DataDoc, Sheet } from "./table";
import * as tree from "./tree";

export const TABLE_FORMATS = ["csv", "tsv", "xlsx"];

function numberOf(text: string): unknown {
  if (NUMBER.test(text)) return Number(text);
  const plain = text.replaceAll("_", "");
  const sign = plain.startsWith("-") ? -1 : 1;
  const body = plain.replace(/^[-+]/, "");
  if (/^0[xob]/i.test(body)) {
    const n = Number(body);
    return Number.isNaN(n) ? text : sign * n;
  }
  if (/^\.(inf|Inf|INF)$/.test(body)) return sign * Infinity;
  if (/^\.(nan|NaN|NAN)$/.test(body)) return NaN;
  const n = Number(plain);
  return plain.trim() !== "" && !Number.isNaN(n) ? n : text;
}

/** A tree of editor values (Num, Maps …) as ordinary values. */
export function plain(value: Value): unknown {
  if (value instanceof Map) return Object.fromEntries([...value].map(([k, v]) => [k, plain(v)]));
  if (Array.isArray(value)) return value.map(plain);
  if (isNum(value)) return numberOf(value.num);
  return value;
}

/** Column names for rows: "column3" where a name is blank, "name_2" where it repeats. */
function names(sheet: Sheet): string[] {
  const out: string[] = [];
  sheet.columns.forEach((name, i) => {
    const base = sheet.header && name ? name : `column${i + 1}`;
    let n = 2;
    let unique = base;
    while (out.includes(unique)) unique = `${base}_${n++}`;
    out.push(unique);
  });
  return out;
}

function cellValue(cell: Cell, typed: boolean): unknown {
  if (isMissing(cell)) return null;
  if (isRaw(cell)) return plain(tree.valueOf(cell));
  if (!typed && typeof cell === "string") return cell ? scalar(cell) : null;
  return plain(cell as Value);
}

function rowsOf(sheet: Sheet): Record<string, unknown>[] {
  const ns = names(sheet);
  return sheet.rows.map((row) => {
    const out: Record<string, unknown> = {};
    row.forEach((c, i) => {
      if (!isMissing(c)) out[ns[i]] = cellValue(c, sheet.typed);
    });
    return out;
  });
}

/** The document as plain values (what the data converter reads from a file). */
export function toValue(opened: Opened, doc: DataDoc): unknown {
  if (TREES.includes(doc.format)) {
    const sheet = doc.sheets[0];
    return plain(tree.put(opened.root, opened.located!, sheet.columns, sheet.rows));
  }
  if (doc.sheets.length > 1) return Object.fromEntries(doc.sheets.map((s) => [s.name, rowsOf(s)]));
  return rowsOf(doc.sheets[0]);
}

/** The document as an `ext` file, written the way the converter writes it. */
export async function exportBytes(opened: Opened, doc: DataDoc, ext: string): Promise<Uint8Array> {
  let value = toValue(opened, doc);
  if (TABLE_FORMATS.includes(doc.format) && TREES.includes(ext)) { // columns like a.b become nesting
    value = Array.isArray(value)
      ? value.map((r) => unflatten(r as Record<string, unknown>))
      : Object.fromEntries(Object.entries(value as Record<string, Record<string, unknown>[]>).map(([k, v]) => [k, v.map(unflatten)]));
  }
  return write(value, ext, defaults(dataConverter.options));
}
