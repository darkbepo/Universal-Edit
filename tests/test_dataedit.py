"""The data editor model (umc.dataedit; web/tests/dataedit.test.ts checks the same shared
cases: tests/fixtures/dataedit_cases.json, made by scripts/make_dataedit_cases.py)."""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from umc.codeedit.search import SearchError
from umc.dataedit import cells, csvtext, io, jsontree, table, xlsx, xmltree, yamltree
from umc.dataedit.cells import MISSING, Num, Raw
from umc.dataedit.history import History

CASES = json.loads((Path(__file__).parent / "fixtures" / "dataedit_cases.json").read_text("utf-8"))


def dec_cell(c):
    if isinstance(c, dict):
        if "missing" in c:
            return MISSING
        return Num(c["num"]) if "num" in c else Raw(c["raw"])
    return c


def dec_sheet(s) -> table.Sheet:
    rows = tuple(tuple(dec_cell(c) for c in r) for r in s["rows"])
    return table.Sheet(s["name"], tuple(s["columns"]), rows, s["typed"], s["header"], s["tree"])


def enc_sheet(s: table.Sheet) -> dict:
    def enc(c):
        if c is MISSING:
            return {"missing": True}
        if isinstance(c, Num):
            return {"num": c.text}
        return {"raw": c.text} if isinstance(c, Raw) else c

    return {
        "name": s.name,
        "columns": list(s.columns),
        "rows": [[enc(c) for c in r] for r in s.rows],
        "typed": s.typed,
        "header": s.header,
        "tree": s.tree,
    }


def dec_value(v):
    if isinstance(v, dict) and "map" in v:
        return {k: dec_value(x) for k, x in v["map"]}
    if isinstance(v, list):
        return [dec_value(x) for x in v]
    return dec_cell(v)


def apply(sheet: table.Sheet, op: str, a: dict):
    o = {k: a[k] for k in ("case", "whole_word", "regex") if k in a}
    if op == "find":
        return [list(x) for x in table.find(sheet, a["query"], **o)]
    if op == "replace_all":
        new, n = table.replace_all(sheet, a["query"], a["replacement"], **o)
        return {"sheet": enc_sheet(new), "count": n}
    args = {k: v for k, v in a.items() if k not in o}
    if op == "set_cells":
        args["changes"] = [tuple(x) for x in args["changes"]]
    return enc_sheet(getattr(table, op)(sheet, **args))


@pytest.mark.parametrize("case", CASES["from_text"], ids=lambda c: f"{c['text']!r}-{c['typed']}")
def test_from_text(case) -> None:
    cell = cells.from_text(case["text"], case["typed"])
    assert cell == dec_cell(case["cell"])
    assert cells.from_text(cells.edit_text(cell, case["typed"]), case["typed"]) == cell


@pytest.mark.parametrize("case", CASES["cells"], ids=lambda c: repr(c["cell"]))
def test_cells(case) -> None:
    cell = dec_cell(case["cell"])
    assert cells.display(cell) == case["display"]
    assert cells.edit_text(cell, True) == case["edit"]
    back = cells.from_text(case["edit"], True)
    assert back == cell or (cell is MISSING and back is None)  # absent: shown blank, typed null


@pytest.mark.parametrize("case", CASES["csv"], ids=lambda c: repr(c["text"][:20]))
def test_csv(case) -> None:
    assert csvtext.sniff(case["text"]) == case["sep"]
    p = csvtext.parse(case["text"], case["sep"])
    assert (p.rows, p.quote_all, p.final_newline) == (
        case["rows"],
        case["quote_all"],
        case["final_newline"],
    )
    assert csvtext.write(p.rows, case["sep"], p.quote_all, p.final_newline) == case["written"]


@pytest.mark.parametrize("case", CASES["json"], ids=lambda c: repr(c["text"][:20]))
def test_json(case) -> None:
    value = jsontree.parse(case["text"])
    assert jsontree.detect_indent(case["text"]) == case["indent"]
    assert jsontree.dumps(value, None) == case["compact"]
    assert jsontree.dumps(value, case["indent"] or "  ") == case["pretty"]


@pytest.mark.parametrize("case", CASES["edits"], ids=lambda c: f"{c['op']}-{c['args']}")
def test_edits(case) -> None:
    base = dec_sheet(CASES["base"])
    want = case["result"]
    if isinstance(want, dict) and "error" in want:
        with pytest.raises(
            (table.EditError, SearchError), match="^" + __import__("re").escape(want["error"]) + "$"
        ):
            apply(base, case["op"], case["args"])
    else:
        assert apply(base, case["op"], case["args"]) == want


@pytest.mark.parametrize("case", CASES["sheet_edits"], ids=lambda c: f"{c['op']}-{c['args']}")
def test_spreadsheet_edits(case) -> None:
    base = dec_sheet(CASES["sheet_base"])
    want = case["result"]
    if isinstance(want, dict) and "error" in want:
        with pytest.raises(table.EditError, match=re.escape(want["error"])):
            apply(base, case["op"], case["args"])
    else:
        assert apply(base, case["op"], case["args"]) == want


def test_yaml_values() -> None:
    for text, cell in CASES["yaml_resolve"]:
        assert yamltree.resolve(text) == dec_cell(cell), text
    for case in CASES["yaml_dump"]:
        assert yamltree.dumps(dec_value(case["value"]), case["indent"]) == case["text"]
        assert yamltree.parse(case["text"]).root == dec_value(case["value"])  # reads back


@pytest.mark.parametrize("case", CASES["xml"], ids=lambda c: c["text"][:30])
def test_xml(case) -> None:
    p = xmltree.parse(case["text"])
    assert p.root == dec_value(case["root"])
    assert (p.declaration, p.indent, p.lost) == (case["declaration"], case["indent"], case["lost"])
    assert xmltree.dumps(p.root, p.declaration, p.indent) == case["written"]


def test_excel_dates() -> None:
    for code, kind in CASES["date_kinds"]:
        assert xlsx.format_kind(code) == kind, code
    for text, kind, d1904, shown in CASES["serials"]:
        assert xlsx.serial_text(text, kind, d1904) == shown, text
    for text, d1904, serial in CASES["typed_dates"]:
        assert xlsx.text_serial(text, d1904) == serial, text


def make_zip(parts: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in parts.items():
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


@pytest.mark.parametrize("case", CASES["xlsx"], ids=lambda c: c["name"])
def test_xlsx(case) -> None:
    data = make_zip(case["parts"])
    opened = io.open_bytes(data, "xlsx")
    assert [enc_sheet(s) for s in opened.doc.sheets] == case["doc"]
    doc = opened.doc
    for i, op, a in case["steps"]:
        doc = table.with_sheet(doc, i, getattr(table, op)(doc.sheets[i], **a))
    saved = io.to_bytes(opened, doc)
    if not case["steps"]:
        assert saved == data
    z = zipfile.ZipFile(BytesIO(saved))
    assert {n: z.read(n).decode("utf-8") for n in z.namelist()} == case["saved"]


def test_letters() -> None:
    for i, name in CASES["letters"]:
        assert table.letter(i) == name


@pytest.mark.parametrize("case", CASES["files"], ids=lambda c: c["name"])
def test_files(case) -> None:
    opened = io.open_bytes(case["text"].encode("utf-8"), case["ext"])
    assert [enc_sheet(s) for s in opened.doc.sheets] == case["doc"]
    doc = opened.doc
    for op, a in case["steps"]:
        doc = table.with_sheet(doc, 0, dec_sheet(_step(doc.sheets[0], op, a)))
    assert io.to_bytes(opened, doc).decode("utf-8") == case["saved"]


def _step(sheet, op, a):
    out = apply(sheet, op, a)
    return out["sheet"] if op == "replace_all" else out


@pytest.mark.parametrize("case", CASES["errors"], ids=lambda c: c["ext"] + c["text"][:8])
def test_open_errors(case) -> None:
    with pytest.raises(io.DataError) as e:
        io.open_bytes(case["text"].encode(), case["ext"])
    assert str(e.value) == case["error"]


# --------------------------------------------------------------------------- desktop only
def test_untouched_files_save_byte_for_byte_and_keep_their_encoding() -> None:
    data = "naïve;x\r\n1;2\r\n".encode("utf-16")  # with a BOM
    opened = io.open_bytes(data, "csv")
    assert io.to_bytes(opened, opened.doc) == data
    sheet = table.set_cell(opened.doc.sheets[0], 0, 1, "é")
    saved = io.to_bytes(opened, table.with_sheet(opened.doc, 0, sheet))
    assert saved == "naïve;x\r\n1;é\r\n".encode("utf-16")


def test_text_that_does_not_fit_the_encoding_is_refused() -> None:
    opened = io.open_bytes("a\ncaf\xe9\n".encode("cp1252"), "csv")
    sheet = table.set_cell(opened.doc.sheets[0], 0, 0, "日本")
    doc = table.with_sheet(opened.doc, 0, sheet)
    with pytest.raises(ValueError, match="encoding"):
        io.to_bytes(opened, doc)
    assert io.to_bytes(opened, doc, utf8=True) == "a\n日本\n".encode()


def test_new_tables_save_even_untouched() -> None:
    for fmt, want in (
        ("csv", "column1,column2\n,\n"),
        ("json", "[\n  {}\n]\n"),
        ("yaml", "- {}\n"),
        ("xml", '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n  <row/>\n</root>\n'),
    ):
        opened = io.new_document(fmt, columns=2, rows=1)
        assert io.to_bytes(opened, opened.doc).decode() == want


def test_too_many_cells() -> None:
    with pytest.raises(io.DataError, match="more than 1,000,000 cells"):
        io.open_bytes(b"a,b\n" * 500_001, "csv")
    sheet = table.Sheet("s", ("a",), (("",),) * 1000, typed=False)
    with pytest.raises(table.EditError):
        table.insert_rows(sheet, 0, 1_000_000)


def test_history_merges_typing_in_one_cell() -> None:
    opened = io.open_bytes(b"a\n1\n", "csv")
    h = History(opened.doc)
    for text in ("2", "23"):
        sheet = table.set_cell(h.doc.sheets[0], 0, 0, text)
        h.push(table.with_sheet(h.doc, 0, sheet), "Type", merge="cell:0:0")
    assert h.dirty and h.undo() == "Type" and h.doc == opened.doc and not h.dirty
    assert h.redo() == "Type" and h.doc.sheets[0].rows == (("23",),)


def test_excel_can_open_what_we_save() -> None:
    import datetime

    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(["name", "age", "born", "double"])
    ws.append(["Ann", 3, datetime.date(2020, 1, 2), "=B2*2"])
    ws.append(["Bob", 10.5, datetime.date(2021, 5, 6), "=B3*2"])
    ws["A1"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 30
    buf = BytesIO()
    wb.save(buf)
    opened = io.open_bytes(buf.getvalue(), "xlsx")
    sheet = opened.doc.sheets[0]
    assert sheet.rows[0][2] == "2020-01-02"
    sheet = table.set_cell(table.set_cell(sheet, 0, 1, "4"), 1, 2, "2022-02-03")
    saved = io.to_bytes(opened, table.with_sheet(opened.doc, 0, sheet))
    back = load_workbook(BytesIO(saved))
    ws2 = back.active
    assert [c.value for c in ws2[2]] == ["Ann", 4, datetime.datetime(2020, 1, 2), "=B2*2"]
    assert ws2["C3"].value == datetime.datetime(2022, 2, 3)
    assert ws2["A1"].font.b and ws2.column_dimensions["A"].width == 30


def test_lost_parts_are_listed() -> None:
    assert io.open_bytes(b"a: 1  # note\n", "yaml").lost == ("comments",)
    assert io.open_bytes(b"url: http://x/#top\n", "yaml").lost == ()
    assert io.open_bytes(b"<a><!-- c --><b>1</b></a>", "xml").lost == ("comments",)
    with pytest.raises(io.DataError, match="several YAML documents"):
        io.open_bytes(b"a: 1\n---\nb: 2\n", "yaml")


@pytest.mark.parametrize("case", CASES["export"], ids=lambda c: c["ext"])
def test_export_values(case) -> None:
    from umc.dataedit import export

    data = make_zip(case["parts"]) if "parts" in case else case["text"].encode()
    opened = io.open_bytes(data, case["ext"])
    assert json.loads(json.dumps(export.to_value(opened, opened.doc))) == case["value"]
