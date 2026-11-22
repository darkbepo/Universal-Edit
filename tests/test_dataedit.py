"""The data editor model (umc.dataedit; web/tests/dataedit.test.ts checks the same shared
cases: tests/fixtures/dataedit_cases.json, made by scripts/make_dataedit_cases.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umc.codeedit.search import SearchError
from umc.dataedit import cells, csvtext, io, jsontree, table
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
    return table.Sheet(s["name"], tuple(s["columns"]), rows, s["typed"], s["header"])


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
    }


def apply(sheet: table.Sheet, op: str, a: dict):
    o = {k: a[k] for k in ("case", "whole_word", "regex") if k in a}
    if op == "find":
        return [list(x) for x in table.find(sheet, a["query"], **o)]
    if op == "replace_all":
        new, n = table.replace_all(sheet, a["query"], a["replacement"], **o)
        return {"sheet": enc_sheet(new), "count": n}
    return enc_sheet(getattr(table, op)(sheet, **{k: v for k, v in a.items() if k not in o}))


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
    with pytest.raises(ValueError, match="encoding"):
        io.to_bytes(opened, table.with_sheet(opened.doc, 0, sheet))


def test_new_tables_save_even_untouched() -> None:
    for fmt, want in (("csv", "column1,column2\n,\n"), ("json", "[\n  {}\n]\n")):
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
