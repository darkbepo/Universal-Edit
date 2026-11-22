# Data editor (both apps) — design

Date: 2026-11-22. Roadmap item 16.4.

## Why

CSV, TSV, JSON, XML, YAML and XLSX files can be converted into each other, but changing one
value means opening the file in the code editor (and, for CSV, counting commas) or installing a
spreadsheet program. The start screen already shows a "Data editor — Coming soon" tile; this
fills it.

## Goals

- **Where:** the hub tile opens an empty editor ("New table" / "Open…"); Edit opens one CSV,
  TSV or XLSX file in it (CSV / TSV keep the code editor as the second choice under Edit's ▾).
  JSON / YAML / XML are usually configuration files, so Edit keeps the code editor first for
  them and offers "Edit as table" under ▾ (decided while building step 3).
- **Layout:** a grid (row numbers, column names as the header, one tab per sheet for XLSX),
  a formula-bar-like edit box above it showing the chosen cell's full text, a find bar.
- **Editing:** type into a cell; add / delete rows and columns (above / below / left / right),
  rename a column, sort by a column (A→Z, Z→A: numbers by value, text in natural order —
  "item2" before "item10" —, blanks always last), find & replace (match case, whole word, regex —
  the code editor's rules), CSV "First row is the header" switch, undo / redo.
- **Values:** CSV / TSV cells are text, always. JSON / YAML / XML / XLSX cells keep their kind:
  typing a number, `true` / `false` or `null` makes that kind, anything else is text; a leading
  `'` forces text (`'123` is the text "123"), as in spreadsheets. Nested data is flattened into
  `a.b` columns — the converter's convention — and rebuilt when saving; lists stay one cell
  shown as JSON (`[1,2]`), and typing valid JSON there keeps it a list. A key a row never had
  stays absent when saving (it isn't written as `null`).
- **Where the table is:** a tree file's table is found like the converter does (follow
  single-key wrappers down to the one list, `{"library": {"book": [...]}}`); everything around it
  is kept as it was when saving. A list of plain values is one "value" column.
- **Saving:** Save writes the same format back; Save as offers the other data formats through
  the data converter's writers. Untouched files save byte for byte. CSV keeps its separator,
  quoting style (all fields quoted or only where needed), encoding, BOM and line endings; JSON
  keeps its indentation (or minified) and every number exactly as written (`1.0` stays `1.0`).
- Same model, behaviour and shortcuts in both apps; shared cases checked by both test suites.

## Non-goals (first version)

- Formulas (XLSX cells show their last computed value; a changed cell loses its formula), cell
  formatting, charts, merged cells, filters, several files at once, freeze panes.

## Model (`umc/dataedit/` ↔ `web/src/dataedit/`, line-by-line ports, no Qt / DOM)

- `cells`: a cell is text, a bool, null, `Num` (a number with its spelling), `Raw` (a list /
  empty object as compact JSON) or `MISSING` (the row has no such key). `display`, `edit_text`
  (adds the `'` where needed) and `from_text` (typing rules above);
  `from_text(edit_text(c)) == c` for every cell.
- `table`: `Sheet` (name, columns, rows of cells, typed, header) and `DataDoc` (format, sheets),
  frozen; `edits` = pure functions returning a new sheet (set cell, insert / delete rows and
  columns, rename, sort, header switch, replace all) + `find`. `history`: snapshot undo (typing
  in one cell is one step).
- `csvtext`: our own RFC 4180 reader / writer (both apps alike) + separator sniffing
  (`,` `;` tab `|`: the one that gives the same field count on most of the first rows).
- `jsontree`: JSON → values through the code editor's `scan_json` (so numbers keep their
  spelling) and back, with the file's indentation; `tree`: find the table in a tree, flatten /
  unflatten rows, put the table back.
- `io`: bytes ↔ document through `codeedit.textfile`; `Opened` keeps what saving needs (CSV
  style, the tree around the table, indentation).
- Limits: 20 MB, 1 000 000 cells.
- Shared cases `tests/fixtures/dataedit_cases.json` (`python scripts/make_dataedit_cases.py`,
  only on purpose).

## Steps (one plan each, ship gate after each)

1. Model for CSV / TSV / JSON (both apps) + shared cases.
2. YAML, XML and XLSX in the model. As built: XML = our own reader / writer in both apps
   (`xmltree`); YAML = each app's library reads only the structure (PyYAML `compose`, the
   `yaml` package's failsafe schema) and our own `resolve` + block writer decide the rest
   (`yamltree`; YAML 1.1 booleans like PyYAML, numbers keep their spelling); XLSX = our own
   reader (`xlsx`: shared / inline strings, booleans, cached formula results, date-formatted
   numbers as ISO text), saving rewrites only the changed sheets' `<sheetData>`: a cell that
   still holds what was at its place keeps its XML (formula, style), a changed one keeps the
   place's style, ISO text in a date place is stored as a date, `calcChain.xml` is dropped.
   Comments / processing instructions / mixed XML order are listed in `Opened.lost`.
3. Desktop page: add-on "data" (`umc.gui.dataeditor`: `QTableView` over a model reading the
   sheet, edit box, find bar, tabs), Edit + hub tile, self-test step.
4. Website page `web/src/ui/dataeditor/` (virtual grid: only visible rows are in the DOM), loaded
   on demand; e2e; tile available in both apps. All four steps done (2026-11-22).
