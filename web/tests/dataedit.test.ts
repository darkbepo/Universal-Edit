// The data editor model against the shared cases (tests/test_dataedit.py checks the same file;
// made by scripts/make_dataedit_cases.py). Read with ?raw: a lone-surrogate case breaks Vite's
// JSON loader.
import { describe, expect, it } from "vitest";
import raw from "../../tests/fixtures/dataedit_cases.json?raw";
import { SearchError } from "../src/codeedit/search";
import { type Cell, display, editText, fromText, isMissing, sameCell } from "../src/dataedit/cells";
import * as csvtext from "../src/dataedit/csvtext";
import { History } from "../src/dataedit/history";
import { DataError, newDocument, openBytes, toBytes } from "../src/dataedit/io";
import { detectIndent, dumps, parse } from "../src/dataedit/jsontree";
import * as table from "../src/dataedit/table";

/* eslint-disable @typescript-eslint/no-explicit-any */
const cases = JSON.parse(raw) as any;
const enc = (s: string) => new TextEncoder().encode(s);
const dec = (b: Uint8Array) => new TextDecoder("utf-8", { ignoreBOM: true }).decode(b);

type A = Record<string, any>;
const opts = (a: A) => ({ caseSensitive: a.case, wholeWord: a.whole_word, regex: a.regex });

function apply(sheet: table.Sheet, op: string, a: A): unknown {
  switch (op) {
    case "find": return table.find(sheet, a.query, opts(a));
    case "replace_all": {
      const [s, count] = table.replaceAll(sheet, a.query, a.replacement, opts(a));
      return { sheet: s, count };
    }
    case "sort_rows": return table.sortRows(sheet, a.c, a.descending ?? false);
    case "set_cell": return table.setCell(sheet, a.r, a.c, a.text);
    case "insert_column": return table.insertColumn(sheet, a.at, a.name ?? null);
    case "rename_column": return table.renameColumn(sheet, a.c, a.name);
    case "delete_columns": return table.deleteColumns(sheet, a.indices);
    case "delete_rows": return table.deleteRows(sheet, a.indices);
    case "insert_rows": return table.insertRows(sheet, a.at, a.count ?? 1);
    case "set_header": return table.setHeader(sheet, a.on);
  }
  throw new Error(`unknown op ${op}`);
}

// the fixture's sheets have the same shape as ours (cells: {num} / {raw} / {missing: true})
const plain = (x: unknown) => JSON.parse(JSON.stringify(x));

describe("cells", () => {
  for (const c of cases.from_text) {
    it(`from text ${JSON.stringify(c.text)} typed ${c.typed}`, () => {
      const cell = fromText(c.text, c.typed);
      expect(plain(cell)).toEqual(c.cell);
      expect(sameCell(fromText(editText(cell, c.typed), c.typed), cell)).toBe(true);
    });
  }
  for (const c of cases.cells) {
    it(`shows ${JSON.stringify(c.cell)}`, () => {
      const cell = c.cell as Cell;
      expect(display(cell)).toBe(c.display);
      expect(editText(cell, true)).toBe(c.edit);
      const back = fromText(c.edit, true);
      expect(sameCell(back, cell) || (isMissing(cell) && back === null)).toBe(true);
    });
  }
});

describe("csv", () => {
  for (const c of cases.csv) {
    it(JSON.stringify(c.text.slice(0, 20)), () => {
      expect(csvtext.sniff(c.text)).toBe(c.sep);
      const p = csvtext.parse(c.text, c.sep);
      expect([p.rows, p.quoteAll, p.finalNewline]).toEqual([c.rows, c.quote_all, c.final_newline]);
      expect(csvtext.write(p.rows, c.sep, p.quoteAll, p.finalNewline)).toBe(c.written);
    });
  }
});

describe("json", () => {
  for (const c of cases.json) {
    it(JSON.stringify(c.text.slice(0, 20)), () => {
      const value = parse(c.text);
      expect(detectIndent(c.text)).toBe(c.indent);
      expect(dumps(value, null)).toBe(c.compact);
      expect(dumps(value, c.indent ?? "  ")).toBe(c.pretty);
    });
  }
});

describe("edits", () => {
  for (const c of cases.edits) {
    it(`${c.op} ${JSON.stringify(c.args)}`, () => {
      const base = cases.base as table.Sheet;
      if (c.result?.error) {
        let error: unknown;
        try {
          apply(base, c.op, c.args);
        } catch (e) {
          error = e;
        }
        expect(error instanceof table.EditError || error instanceof SearchError).toBe(true);
        if (!(error instanceof SearchError)) expect((error as Error).message).toBe(c.result.error); // JS words regex errors its own way
      } else {
        expect(plain(apply(base, c.op, c.args))).toEqual(c.result);
      }
    });
  }
  it("letters", () => {
    for (const [i, name] of cases.letters) expect(table.letter(i)).toBe(name);
  });
});

describe("files", () => {
  for (const c of cases.files) {
    it(c.name, () => {
      const opened = openBytes(enc(c.text), c.ext);
      expect(plain(opened.doc.sheets)).toEqual(c.doc);
      let doc = opened.doc;
      for (const [op, a] of c.steps) {
        const out = apply(doc.sheets[0], op, a) as any;
        doc = table.withSheet(doc, 0, op === "replace_all" ? out.sheet : out);
      }
      expect(dec(toBytes(opened, doc))).toBe(c.saved);
    });
  }
  for (const c of cases.errors) {
    it(`refuses ${c.ext} ${JSON.stringify(c.text)}`, () => {
      expect(() => openBytes(enc(c.text), c.ext)).toThrow(new DataError(c.error));
    });
  }
});

// ------------------------------------------------------------------ the desktop's own tests
describe("io", () => {
  it("saves untouched files byte for byte", () => {
    const data = new Uint8Array([0xef, 0xbb, 0xbf, ...enc("naïve;x\r\n1;2\r\n")]);
    const opened = openBytes(data, "csv");
    expect(toBytes(opened, opened.doc)).toBe(opened.file.raw);
    const doc = table.withSheet(opened.doc, 0, table.setCell(opened.doc.sheets[0], 0, 1, "é"));
    expect(Array.from(toBytes(opened, doc))).toEqual([0xef, 0xbb, 0xbf, ...enc("naïve;x\r\n1;é\r\n")]);
  });

  it("saves new tables even untouched", () => {
    for (const [fmt, want] of [["csv", "column1,column2\n,\n"], ["json", "[\n  {}\n]\n"]]) {
      const opened = newDocument(fmt, 2, 1);
      expect(dec(toBytes(opened, opened.doc))).toBe(want);
    }
  });

  it("refuses too many cells", () => {
    expect(() => openBytes(enc("a,b\n".repeat(500_001)), "csv")).toThrow("more than 1,000,000 cells");
    const sheet: table.Sheet = { name: "s", columns: ["a"], rows: Array.from({ length: 1000 }, () => [""]), typed: false, header: true };
    expect(() => table.insertRows(sheet, 0, 1_000_000)).toThrow(table.EditError);
  });

  it("merges typing in one cell into one undo step", () => {
    const opened = openBytes(enc("a\n1\n"), "csv");
    const h = new History(opened.doc);
    for (const text of ["2", "23"]) h.push(table.withSheet(h.doc, 0, table.setCell(h.doc.sheets[0], 0, 0, text)), "Type", "cell:0:0");
    expect(h.dirty).toBe(true);
    expect(h.undo()).toBe("Type");
    expect(table.sameDoc(h.doc, opened.doc) && !h.dirty).toBe(true);
    expect(h.redo()).toBe("Type");
    expect(h.doc.sheets[0].rows).toEqual([["23"]]);
  });
});
