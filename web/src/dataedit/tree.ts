// The table inside a tree of values, and putting it back — a port of src/umc/dataedit/tree.py.
// Found like the data converter: single-key wrappers are followed down to the one list; a
// dict without one list is a single row, a lone value a single "value" cell. Rows are flattened
// into a.b columns; lists and empty objects stay one Raw cell.

import { type Cell, isMissing, isRaw, MISSING } from "./cells";
import { dumps, parse, type Value } from "./jsontree";
import type { Row } from "./table";

export const VALUE = "value";

export interface Located {
  path: string[]; // dict keys from the root to the table
  kind: "list" | "object" | "value";
  plain: boolean; // a list of plain values (no dicts): one "value" column
}

const at = (root: Value, path: string[]) => path.reduce((v, k) => (v as Map<string, Value>).get(k)!, root);

export function locate(root: Value): Located {
  const path: string[] = [];
  let value = root;
  while (value instanceof Map) {
    const lists = [...value].filter(([, v]) => Array.isArray(v)).map(([k]) => k);
    if (lists.length === 1) {
      path.push(lists[0]);
      value = value.get(lists[0])!;
    } else if (value.size === 1 && [...value.values()][0] instanceof Map) {
      const [key, v] = [...value][0];
      path.push(key);
      value = v;
    } else {
      return { path, kind: "object", plain: false };
    }
  }
  if (Array.isArray(value)) return { path, kind: "list", plain: value.length > 0 && !value.some((x) => x instanceof Map) };
  return { path, kind: "value", plain: false };
}

export function cellOf(value: Value): Cell {
  if (Array.isArray(value) || value instanceof Map) return { raw: dumps(value, null) };
  return value;
}

export function valueOf(cell: Cell): Value {
  if (isMissing(cell)) return null;
  if (isRaw(cell)) return parse(cell.raw);
  return cell;
}

export function flatten(row: Map<string, Value>, prefix = ""): Map<string, Cell> {
  const out = new Map<string, Cell>();
  for (const [k, v] of row) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v instanceof Map && v.size) for (const [fk, fv] of flatten(v, key)) out.set(fk, fv);
    else out.set(key, cellOf(v));
  }
  return out;
}

export function unflatten(columns: readonly string[], row: Row): Map<string, Value> {
  const out = new Map<string, Value>();
  columns.forEach((name, i) => {
    const cell = row[i];
    if (isMissing(cell)) return;
    const parts = name.split(".");
    const last = parts.pop()!;
    let node = out;
    for (const p of parts) {
      let next = node.get(p);
      if (!(next instanceof Map)) {
        next = new Map();
        node.set(p, next);
      }
      node = next;
    }
    node.set(last, valueOf(cell));
  });
  return out;
}

export function tableOf(root: Value, loc: Located): [string[], Row[]] {
  const value = at(root, loc.path);
  let records: Map<string, Cell>[];
  if (loc.kind === "list") records = (value as Value[]).map((x) => (x instanceof Map ? flatten(x) : new Map([[VALUE, cellOf(x)]])));
  else if (loc.kind === "object") records = [flatten(value as Map<string, Value>)];
  else records = [new Map([[VALUE, cellOf(value)]])];
  const names: string[] = [];
  const seen = new Set<string>();
  for (const rec of records) for (const k of rec.keys()) if (!seen.has(k)) (seen.add(k), names.push(k));
  return [names, records.map((rec) => names.map((c) => (rec.has(c) ? rec.get(c)! : MISSING)))];
}

function setAt(root: Value, path: string[], value: Value): Value {
  if (!path.length) return value;
  const out = new Map(root as Map<string, Value>);
  out.set(path[0], setAt((root as Map<string, Value>).get(path[0])!, path.slice(1), value));
  return out;
}

/** The root with the edited table in its place. */
export function put(root: Value, loc: Located, columns: readonly string[], rows: readonly Row[]): Value {
  const items = columns.length === 1 && columns[0] === VALUE && (loc.plain || loc.kind === "value")
    ? rows.map((r) => valueOf(r[0]))
    : rows.map((r) => unflatten(columns, r));
  let table: Value;
  if (loc.kind === "list") table = items;
  else if (items.length === 1) table = items[0];
  else if (!items.length) table = loc.kind === "object" ? new Map() : null;
  else table = items;
  return setAt(root, loc.path, table);
}
