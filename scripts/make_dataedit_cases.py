"""Write tests/fixtures/dataedit_cases.json from the desktop data-editor model.

Both test suites (tests/test_dataedit.py, web/tests/dataedit.test.ts) check these cases, so the
web port must read, edit and write the same tables and files. Regenerate only on purpose:
    python scripts/make_dataedit_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umc.codeedit.search import SearchError
from umc.dataedit import cells, csvtext, io, jsontree, table
from umc.dataedit.cells import MISSING, Num, Raw

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dataedit_cases.json"


def enc_cell(c: Any) -> Any:
    if c is MISSING:
        return {"missing": True}
    if isinstance(c, Num):
        return {"num": c.text}
    if isinstance(c, Raw):
        return {"raw": c.text}
    return c


def enc_sheet(s: table.Sheet) -> dict:
    return {
        "name": s.name,
        "columns": list(s.columns),
        "rows": [[enc_cell(c) for c in r] for r in s.rows],
        "typed": s.typed,
        "header": s.header,
    }


CELL_TEXTS = [
    "",
    "null",
    "true",
    "false",
    "12",
    "-0.5e3",
    "01",
    "1.",
    "'123",
    "''",
    "abc",
    "[1, 2]",
    '{"a": 1}',
    "[1,",
    "{}",
    " 5",
    "TRUE",
]
CELLS = [
    "",
    "123",
    "true",
    "null",
    "'x",
    "[1]",
    "plain",
    True,
    None,
    Num("1.0"),
    Raw("[1,2]"),
    MISSING,
]

CSV = [
    "a,b\n1,2\n",
    'name;note\n"Ann";"said ""hi"""\nBob;x"y\n',
    '"x","y"\n"1","2"',
    "one\ttwo\n1\t2\n",
    "a|b|c\n1|2|3\n4|5|6\n",
    '"line\nbreak",b\n',
    '"never closed,a\nb\n',
    '"a"tail,b\n',
    "a,b,c\n1\n\n2,3\n",
    "single\n",
    "",
]

FILES: list[tuple[str, str, str, list]] = [
    ("csv untouched", "csv", '﻿name;age\r\n"Ann";3\r\nBob;10\r\n', []),
    (
        "csv sort numbers",
        "csv",
        "name;age\r\nAnn;3\r\nBob;10\r\nCy;2\r\nDee;\r\n",
        [["sort_rows", {"c": 1}]],
    ),
    (
        "csv natural sort, descending",
        "csv",
        "item\nitem10\nItem2\nitem1\nb\n",
        [["sort_rows", {"c": 0, "descending": True}]],
    ),
    (
        "csv quote all kept",
        "csv",
        '"a","b"\n"1","2"\n',
        [["set_cell", {"r": 0, "c": 1, "text": "x,y"}]],
    ),
    (
        "csv header off and on",
        "csv",
        "a,b\n1,2\n",
        [
            ["set_header", {"on": False}],
            ["insert_rows", {"at": 0}],
            ["set_cell", {"r": 0, "c": 0, "text": "new"}],
        ],
    ),
    (
        "csv columns",
        "csv",
        "a,b\n1,2\n",
        [
            ["insert_column", {"at": 1}],
            ["rename_column", {"c": 0, "name": "first"}],
            ["delete_columns", {"indices": [2]}],
            ["set_cell", {"r": 0, "c": 1, "text": "multi\nline"}],
        ],
    ),
    (
        "tsv",
        "tsv",
        "a\tb\n1\t2",
        [["delete_rows", {"indices": [0]}], ["insert_rows", {"at": 0, "count": 2}]],
    ),
    (
        "csv replace",
        "csv",
        "a,b\ncat,dog\nCat,catalog\n",
        [
            [
                "replace_all",
                {
                    "query": "cat",
                    "replacement": "bird",
                    "case": False,
                    "whole_word": True,
                    "regex": False,
                },
            ]
        ],
    ),
    (
        "json numbers keep their spelling",
        "json",
        '[\n  {"name": "A", "n": 1.0, "big": 12345678901234567890},\n'
        '  {"name": "B", "n": 2e3}\n]\n',
        [["set_cell", {"r": 1, "c": 0, "text": "Bee"}]],
    ),
    (
        "json wrapper kept",
        "json",
        '{"library": {"name": "x", "book": '
        '[{"t": "A", "a": {"x": 1}}, {"t": "B", "tags": [1, 2]}]}}',
        [
            ["set_cell", {"r": 1, "c": 1, "text": "7"}],
            ["set_cell", {"r": 0, "c": 2, "text": "[3, 4]"}],
        ],
    ),
    (
        "json typing",
        "json",
        '[\n\t{"a": "1", "b": true}\n]\n',
        [
            ["set_cell", {"r": 0, "c": 1, "text": "'true"}],
            ["insert_rows", {"at": 1}],
            ["set_cell", {"r": 1, "c": 0, "text": "null"}],
            ["insert_column", {"at": 2, "name": "c.d"}],
            ["set_cell", {"r": 1, "c": 2, "text": "x"}],
        ],
    ),
    ("json plain values", "json", '{"tags": ["b", "a", 3]}\n', [["sort_rows", {"c": 0}]]),
    (
        "json one object",
        "json",
        '{"name": "Ann", "age": 3, "keys": {"10": 1, "2": 2}}\n',
        [["set_cell", {"r": 0, "c": 1, "text": "4"}]],
    ),
    ("json lone value", "json", '"just text"', [["set_cell", {"r": 0, "c": 0, "text": "other"}]]),
    (
        "json escapes",
        "json",
        '[{"s": "a\\u0001\\"b\\\\ \\u00e9 \\ud83d\\ude00 \\ud800 \\/", "n": 1}]',
        [["set_cell", {"r": 0, "c": 1, "text": "2"}]],
    ),
    (
        "json new text",
        "json",
        '[{"s": "x"}]',
        [["set_cell", {"r": 0, "c": 0, "text": "tab\there"}]],
    ),
    (
        "json empty file",
        "json",
        "",
        [
            ["insert_column", {"at": 0, "name": "a"}],
            ["insert_rows", {"at": 0}],
            ["set_cell", {"r": 0, "c": 0, "text": "1"}],
        ],
    ),
    (
        "json duplicate key",
        "json",
        '[{"a": 1, "b": 2, "a": 3}]',
        [["set_cell", {"r": 0, "c": 1, "text": "5"}]],
    ),
]

EDIT_BASE = table.Sheet(
    "Sheet1",
    ("name", "n", "note"),
    (("b", Num("10"), MISSING), ("a", Num("2"), "x"), ("c", None, "Y"), ("a10", Num("2.0"), "")),
    typed=True,
)
EDITS: list[tuple[str, dict]] = [
    ("sort_rows", {"c": 0}),
    ("sort_rows", {"c": 1}),
    ("sort_rows", {"c": 1, "descending": True}),
    ("sort_rows", {"c": 2}),
    ("set_cell", {"r": 0, "c": 0, "text": "b"}),
    ("set_cell", {"r": 2, "c": 1, "text": ""}),
    ("insert_column", {"at": 9}),
    ("insert_column", {"at": 0, "name": "n"}),
    ("insert_column", {"at": 0, "name": ""}),
    ("rename_column", {"c": 0, "name": "note"}),
    ("rename_column", {"c": 0, "name": "title"}),
    ("delete_rows", {"indices": [0, 2]}),
    ("insert_rows", {"at": 99, "count": 1}),
    ("set_header", {"on": False}),
    ("find", {"query": "a", "case": False, "whole_word": False, "regex": False}),
    ("find", {"query": "^\\d+$", "case": False, "whole_word": False, "regex": True}),
    ("find", {"query": "(", "case": False, "whole_word": False, "regex": True}),
    (
        "replace_all",
        {"query": "2", "replacement": "3", "case": True, "whole_word": False, "regex": False},
    ),
    (
        "replace_all",
        {
            "query": "(\\w)",
            "replacement": "[\\1]",
            "case": True,
            "whole_word": False,
            "regex": True,
        },
    ),
]


def run(op: str, sheet: table.Sheet, a: dict) -> Any:
    o = {k: a[k] for k in ("case", "whole_word", "regex") if k in a}
    if op == "find":
        return [list(x) for x in table.find(sheet, a["query"], **o)]
    if op == "replace_all":
        new, n = table.replace_all(sheet, a["query"], a["replacement"], **o)
        return {"sheet": enc_sheet(new), "count": n}
    args = {k: v for k, v in a.items() if k not in o}
    return enc_sheet(getattr(table, op)(sheet, **args))


def attempt(fn) -> Any:
    try:
        return fn()
    except (table.EditError, SearchError, io.DataError) as exc:
        return {"error": str(exc)}


def main() -> None:
    out: dict[str, Any] = {"base": enc_sheet(EDIT_BASE)}
    out["from_text"] = [
        {"text": t, "typed": ty, "cell": enc_cell(cells.from_text(t, ty))}
        for t in CELL_TEXTS
        for ty in (True, False)
    ]
    out["cells"] = [
        {"cell": enc_cell(c), "display": cells.display(c), "edit": cells.edit_text(c, True)}
        for c in CELLS
    ]
    out["csv"] = []
    for text in CSV:
        sep = csvtext.sniff(text)
        p = csvtext.parse(text, sep)
        out["csv"].append(
            {
                "text": text,
                "sep": sep,
                "rows": p.rows,
                "quote_all": p.quote_all,
                "final_newline": p.final_newline,
                "written": csvtext.write(p.rows, sep, p.quote_all, p.final_newline),
            }
        )
    out["json"] = [
        {
            "text": t,
            "indent": jsontree.detect_indent(t),
            "compact": jsontree.dumps(jsontree.parse(t), None),
            "pretty": jsontree.dumps(jsontree.parse(t), jsontree.detect_indent(t) or "  "),
        }
        for t in ['{"b": [1, {"c": []}], "a": {}}', '[\n    1,\n    "x"\n]', '{\n"a": 1\n}', "5"]
    ]
    out["edits"] = [
        {"op": op, "args": a, "result": attempt(lambda op=op, a=a: run(op, EDIT_BASE, a))}
        for op, a in EDITS
    ]
    out["letters"] = [[i, table.letter(i)] for i in (0, 25, 26, 51, 52, 701, 702)]
    out["files"] = []
    for name, ext, text, steps in FILES:
        opened = io.open_bytes(text.encode("utf-8"), ext)
        doc = opened.doc
        for op, a in steps:
            if op == "replace_all":
                o = {k: a[k] for k in ("case", "whole_word", "regex")}
                sheet, _ = table.replace_all(doc.sheets[0], a["query"], a["replacement"], **o)
            else:
                sheet = getattr(table, op)(doc.sheets[0], **a)
            doc = table.with_sheet(doc, 0, sheet)
        out["files"].append(
            {
                "name": name,
                "ext": ext,
                "text": text,
                "steps": steps,
                "doc": [enc_sheet(s) for s in opened.doc.sheets],
                "saved": io.to_bytes(opened, doc).decode("utf-8"),
            }
        )
    out["errors"] = [
        {
            "ext": ext,
            "text": text,
            "error": attempt(lambda e=ext, t=text: io.open_bytes(t.encode(), e))["error"],
        }
        for ext, text in [
            ("json", '{"a": 1,}'),
            ("json", "[1, 2"),
            ("xml", "<a/>"),
            ("csv", "a\x00b"),
        ]
    ]
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
