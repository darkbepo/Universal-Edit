# ruff: noqa: E501  (the cases hold long lines of XML and YAML on purpose)
"""Write tests/fixtures/dataedit_cases.json from the desktop data-editor model.

Both test suites (tests/test_dataedit.py, web/tests/dataedit.test.ts) check these cases, so the
web port must read, edit and write the same tables and files. Regenerate only on purpose:
    python scripts/make_dataedit_cases.py
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from umc.codeedit.search import SearchError
from umc.dataedit import cells, csvtext, io, jsontree, table, xlsx, xmltree, yamltree
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
        "tree": s.tree,
    }


def enc_value(v: Any) -> Any:
    """A tree value for the fixture: maps keep their key order as [key, value] pairs."""
    if isinstance(v, dict):
        return {"map": [[k, enc_value(x)] for k, x in v.items()]}
    if isinstance(v, list):
        return [enc_value(x) for x in v]
    return enc_cell(v)


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
    tree=True,
)
SHEET_BASE = table.Sheet(  # a spreadsheet (XLSX): typed, no tree; blank = null
    "Data", ("a", ""), ((Num("1"), "x"), (None, True)), typed=True
)
SHEET_EDITS: list[tuple[str, dict]] = [
    ("insert_rows", {"at": 1}),
    ("insert_column", {"at": 1}),
    ("insert_column", {"at": 0, "name": ""}),
    ("rename_column", {"c": 0, "name": ""}),
    ("set_header", {"on": False}),
    ("sort_rows", {"c": 0, "descending": True}),
]
EDITS: list[tuple[str, dict]] = [
    ("set_cells", {"changes": [[0, 0, "z"], [1, 1, "7"], [9, 0, "out"], [2, 2, "Y"]]}),
    ("set_cells", {"changes": [[0, 0, "b"]]}),
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


YAML_PLAIN = ["", "~", "null", "NULL", "true", "Yes", "off", "12", "+5", "1_000", "1.", ".5",
              "-2e3", "0x1F", "0o17", "0b101", ".inf", "-.Inf", ".nan", "1.2.3", "2024-01-02",
              "y", "abc", "1e"]  # fmt: skip
YAML_DUMP: list[tuple[Any, str]] = [
    ({"a": Num("1"), "b": [], "c": {}, "d": None, "e": True}, "  "),
    ([{"name": "x", "tags": ["a", "b"]}, [Num("1"), [Num("2")]], "plain"], "  "),
    (
        {
            "s": [
                "yes",
                "12",
                "a: b",
                "a #b",
                " lead",
                "tail ",
                "-x",
                "#x",
                "",
                "multi\nline",
                "é ok",
                "---",
                "x:",
                "0x1F",
                "tab\there",
            ]
        },
        "    ",
    ),  # fmt: skip
    ({"key with: colon": Num("1"), "": Num("2"), "#k": Num("3"), "1": Num("4")}, "  "),
    ("lone", "  "),
    ({}, "  "),
]
XML_TEXTS = [
    '<?xml version="1.0" encoding="UTF-8"?>\n<!-- list -->\n<library name="L &amp; co">\n'
    '  <book id="1"><title>A &lt;B&gt;</title><n>1.0</n><empty/></book>\n'
    '  <book id="2"><title><![CDATA[C & D]]></title><tag>x</tag><tag>y</tag></book>\n'
    "  <note>hi <b>there</b> you</note>\n</library>\n",
    "<a><b>1</b><c>2</c><b>3</b></a>",
    '<r>\n<x>&#65;&#x42;&bogus;</x>\n<y a=\'q"uote\' b="tab\tnl\n"/>\n</r>',
    "<?xml version='1.0'?><?pi here?><!DOCTYPE r><r xmlns:n=\"urn:x\"><n:i>  spaced  </n:i></r>",
]
FORMAT_CODES = ["General", "0.00", "yyyy-mm-dd", "d/m/yy h:mm", "h:mm:ss", '"Day "0', "[Red]0",
                "[$-409]mmm d", "0.00E+00", "mm:ss", "\\d0"]  # fmt: skip
SERIALS = [("43832", "date", False), ("44322.297326388886", "date", False), ("0.5", "time", False),
           ("59", "date", False), ("61", "date", False), ("1", "date", True), ("-1", "date", False),
           ("3000000", "date", False), ("0", "date", False)]  # fmt: skip
TYPED_DATES = [("2020-01-02", False), ("2022-02-03 07:08:09", False), ("1900-03-01", False),
               ("1900-02-28", False), ("12:30", False), ("2020-02-30", False), ("1904-01-02", True),
               ("25:00", False), ("2020-01-02T00:00:01", False), ("x", False)]  # fmt: skip

FILES += [
    ("yaml edit keeps the rest", "yaml",
     "---\n# people\nversion: 2\npeople:\n  - name: Ann\n    age: 3\n    born: 2020-01-02\n"
     "    ok: yes\n    hex: 0x1F\n  - name: Bob\n    age: 10.50\n    note: '12'\n",
     [["set_cell", {"r": 1, "c": 1, "text": "11"}], ["sort_rows", {"c": 0, "descending": True}]]),
    ("yaml untouched", "yaml", "a: 1   # kept\nb: [x, y]\n", []),
    ("yaml typing", "yaml", "- a: x\n", [["insert_rows", {"at": 1}],
     ["set_cell", {"r": 1, "c": 0, "text": "'yes"}], ["insert_column", {"at": 1, "name": "b.c"}],
     ["set_cell", {"r": 0, "c": 1, "text": "[1, 2]"}]]),
    ("yaml empty", "yml", "", [["insert_column", {"at": 0, "name": "a"}],
                               ["insert_rows", {"at": 0}], ["set_cell", {"r": 0, "c": 0, "text": "1"}]]),
    ("xml edit", "xml",
     '<?xml version="1.0"?>\n<library>\n    <book id="1"><t>A</t></book>\n'
     '    <book id="2"><t>B &amp; C</t><n>1.0</n></book>\n</library>\n',
     [["set_cell", {"r": 0, "c": 1, "text": "A2"}], ["insert_rows", {"at": 2}],
      ["set_cell", {"r": 2, "c": 0, "text": "3"}]]),
    ("xml one line", "xml", "<r><i a=\"1\">x</i><i a=\"2\">y</i></r>",
     [["sort_rows", {"c": 0, "descending": True}]]),
    ("xml text column", "xml", "<r><i>one</i><i>two</i></r>\n",
     [["set_cell", {"r": 1, "c": 0, "text": "2 < 3"}]]),
]  # fmt: skip

_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
       '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
       '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
       "</Types>")  # fmt: skip
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
         '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet2.xml"/>'
         '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
         '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/>'
         "</Relationships>")  # fmt: skip
_STYLES = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd&quot;.&quot;mm&quot;.&quot;yyyy"/></numFmts>'
           '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="164" applyNumberFormat="1"/>'
           '<xf numFmtId="20"/><xf numFmtId="2"/></cellXfs></styleSheet>')  # fmt: skip
_SHARED = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3">'
           "<si><t>name</t></si><si><r><t>Ann</t></r><r><rPr><b/></rPr><t xml:space=\"preserve\"> Lee</t></r>"
           "<rPh><t>ignored</t></rPh></si><si><t>line_x000D_two &amp; more</t></si></sst>")  # fmt: skip
_WB = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{pr}'
       '<sheets><sheet name="People" sheetId="1" r:id="rId1"/><sheet name="More" sheetId="2" r:id="rId2"/>'
       "</sheets></workbook>")  # fmt: skip
_SHEET1 = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<dimension ref="A1:D4"/><cols><col min="1" max="1" width="30" customWidth="1"/></cols>'
           '<sheetData><row r="1" spans="1:4" ht="20" customHeight="1"><c r="A1" t="s"><v>0</v></c>'
           '<c r="B1" t="inlineStr"><is><t>born</t></is></c><c r="C1" t="str"><v>when</v></c><c r="D1" t="s"><v>0</v></c></row>'
           '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" s="1"><v>43832</v></c><c r="C2" s="2"><v>0.25</v></c>'
           '<c r="D2"><f>B2*2</f><v>87664</v></c></row>'
           '<row r="4"><c r="A4" t="s"><v>2</v></c><c r="B4" t="b"><v>1</v></c><c r="C4" t="e"><v>#N/A</v></c>'
           '<c r="D4" s="3"><v>1.5E-3</v></c></row></sheetData>'
           '<mergeCells count="1"><mergeCell ref="A5:B5"/></mergeCells></worksheet>')  # fmt: skip
_SHEET2 = ('<x:worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<x:sheetData><x:row><x:c><x:v>1</x:v></x:c><x:c t="inlineStr"><x:is><x:t>a</x:t></x:is></x:c></x:row>'
           "<x:row><x:c><x:v>2</x:v></x:c></x:row></x:sheetData></x:worksheet>")  # fmt: skip


def _book(pr: str = "") -> dict[str, str]:
    return {"[Content_Types].xml": _CT, "xl/_rels/workbook.xml.rels": _RELS,
            "xl/workbook.xml": _WB.format(pr=pr), "xl/styles.xml": _STYLES,
            "xl/sharedStrings.xml": _SHARED, "xl/worksheets/sheet1.xml": _SHEET1,
            "xl/worksheets/sheet2.xml": _SHEET2, "xl/calcChain.xml": '<calcChain><c r="D2" i="1"/></calcChain>'}  # fmt: skip


XLSX_FILES: list[tuple[str, dict[str, str], list]] = [
    ("untouched", _book(), []),
    ("edit cells", _book(), [
        [0, "set_cell", {"r": 0, "c": 1, "text": "2021-03-04"}],
        [0, "set_cell", {"r": 2, "c": 2, "text": "fixed & <done>"}],
        [0, "set_cell", {"r": 1, "c": 0, "text": "new row"}],
        [0, "set_cell", {"r": 2, "c": 3, "text": ""}],
    ]),
    ("sort and header", _book(), [[0, "sort_rows", {"c": 0}], [0, "rename_column", {"c": 3, "name": "twice"}]]),
    ("prefixed sheet", _book('<workbookPr date1904="1"/>'), [
        [1, "set_header", {"on": False}], [1, "insert_rows", {"at": 0}],
        [1, "set_cell", {"r": 0, "c": 1, "text": "top\x01"}],
    ]),
]  # fmt: skip


def make_zip(parts: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


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
            ("xml", "<a><b></a>"),
            ("xml", "<a/><b/>"),
            ("xml", ""),
            ("xlsx", "not a zip"),
            ("toml", "a = 1"),
            ("csv", "a\x00b"),
        ]
    ]
    out["sheet_base"] = enc_sheet(SHEET_BASE)
    out["sheet_edits"] = [
        {"op": op, "args": a, "result": attempt(lambda op=op, a=a: run(op, SHEET_BASE, a))}
        for op, a in SHEET_EDITS
    ]
    out["yaml_resolve"] = [[t, enc_cell(yamltree.resolve(t))] for t in YAML_PLAIN]
    out["yaml_dump"] = [
        {"value": enc_value(v), "indent": ind, "text": yamltree.dumps(v, ind)}
        for v, ind in YAML_DUMP
    ]
    out["xml"] = []
    for text in XML_TEXTS:
        p = xmltree.parse(text)
        out["xml"].append(
            {
                "text": text,
                "root": enc_value(p.root),
                "declaration": p.declaration,
                "indent": p.indent,
                "lost": p.lost,
                "written": xmltree.dumps(p.root, p.declaration, p.indent),
            }
        )
    out["date_kinds"] = [[code, xlsx.format_kind(code)] for code in FORMAT_CODES]
    out["serials"] = [
        [text, kind, d1904, xlsx.serial_text(text, kind, d1904)] for text, kind, d1904 in SERIALS
    ]
    out["typed_dates"] = [[t, d1904, xlsx.text_serial(t, d1904)] for t, d1904 in TYPED_DATES]
    out["xlsx"] = []
    for name, parts, steps in XLSX_FILES:
        opened = io.open_bytes(make_zip(parts), "xlsx")
        doc = opened.doc
        for i, op, a in steps:
            doc = table.with_sheet(doc, i, getattr(table, op)(doc.sheets[i], **a))
        saved = zipfile.ZipFile(BytesIO(io.to_bytes(opened, doc)))
        out["xlsx"].append(
            {
                "name": name,
                "parts": parts,
                "steps": steps,
                "doc": [enc_sheet(x) for x in opened.doc.sheets],
                "saved": {n: saved.read(n).decode("utf-8") for n in saved.namelist()},
            }
        )
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
