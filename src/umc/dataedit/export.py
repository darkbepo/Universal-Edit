"""Save as another format: the document as plain values, written by the data converter
(``converters/data.py``), so the editor's "Save as" gives exactly what converting would.

Tree formats are rebuilt with the rest of their file (``tree.put``); tables become a list of
rows (``{column: value}``; one list per sheet when a workbook has several). Numbers become
real numbers, CSV text that reads as a number or true / false too (the converter's rule)."""

from __future__ import annotations

from typing import Any

from umc.converters import data
from umc.dataedit import io, tree
from umc.dataedit.cells import MISSING, NUMBER, Num, Raw
from umc.dataedit.table import DataDoc, Sheet

TABLE_FORMATS = ("csv", "tsv", "xlsx")


def _number(text: str) -> Any:
    if NUMBER.fullmatch(text):
        return float(text) if any(ch in text for ch in ".eE") else int(text)
    plain = text.replace("_", "")
    try:
        return int(plain, 0)  # 0x1F, 0o17, 0b101 (YAML)
    except ValueError:
        pass
    try:
        return float(
            plain.replace(".inf", "inf")
            .replace(".Inf", "inf")
            .replace(".INF", "inf")
            .replace(".nan", "nan")
            .replace(".NaN", "nan")
            .replace(".NAN", "nan")
        )
    except ValueError:
        return text


def plain(value: Any) -> Any:
    """A tree of editor values (``Num`` …) as ordinary Python values."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, Num):
        return _number(value.text)
    return value


def _names(sheet: Sheet) -> list[str]:
    """Column names for rows: "column3" where a name is blank, "name_2" where it repeats."""
    out: list[str] = []
    for i, name in enumerate(sheet.columns):
        base = name if sheet.header and name else f"column{i + 1}"
        name, n = base, 2
        while name in out:
            name, n = f"{base}_{n}", n + 1
        out.append(name)
    return out


def _cell(cell: Any, typed: bool) -> Any:
    if cell is MISSING:
        return None
    if isinstance(cell, Raw):
        return plain(tree.value_of(cell))
    if not typed and isinstance(cell, str):
        return data._scalar(cell) if cell else None
    return plain(cell)


def _rows(sheet: Sheet) -> list[dict[str, Any]]:
    names = _names(sheet)
    return [
        {n: _cell(c, sheet.typed) for n, c in zip(names, row, strict=True) if c is not MISSING}
        for row in sheet.rows
    ]


def to_value(opened: io.Opened, doc: DataDoc) -> Any:
    """The document as plain values (what the data converter reads from a file)."""
    if doc.format in io.TREES:
        sheet = doc.sheets[0]
        assert opened.located is not None
        return plain(tree.put(opened.root, opened.located, sheet.columns, sheet.rows))
    if len(doc.sheets) > 1:
        return {s.name: _rows(s) for s in doc.sheets}
    return _rows(doc.sheets[0])


def export_bytes(opened: io.Opened, doc: DataDoc, ext: str) -> bytes:
    """The document as an ``ext`` file, written the way the converter writes it."""
    value = to_value(opened, doc)
    if doc.format in TABLE_FORMATS and ext in io.TREES:  # columns like a.b become nesting
        value = (
            {k: [data.unflatten(r) for r in v] for k, v in value.items()}
            if isinstance(value, dict)
            else [data.unflatten(r) for r in value]
        )
    return data.write(value, ext, data.DataOptions())
