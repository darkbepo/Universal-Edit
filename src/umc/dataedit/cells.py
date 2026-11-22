"""One table cell (port: web/src/dataedit/cells.ts).

CSV / TSV cells are always text. Cells of typed sheets (JSON, …) keep their kind: text, a bool,
null, a number with its spelling (``Num``), a list / empty object as compact JSON (``Raw``), or
``MISSING`` when the row has no such key (saved without it, not as null)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from umc.codeedit.check import JsonError, scan_json

WS = " \t\r\n"  # what counts as blank around a number / a JSON file (same in the port)
NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class Num:
    text: str  # exactly as written ("1.0" stays "1.0")


@dataclass(frozen=True)
class Raw:
    text: str  # compact JSON of a list or an empty object


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()

Cell = str | bool | None | Num | Raw | _Missing


def display(cell: Cell) -> str:
    """What the grid shows."""
    if cell is None or cell is MISSING:
        return ""
    if isinstance(cell, bool):
        return "true" if cell else "false"
    if isinstance(cell, Num | Raw):
        return cell.text
    return cell  # type: ignore[return-value]


def compact_json(text: str) -> str | None:
    """``text`` as compact JSON if it is a valid list or object, else None."""
    try:
        tokens = scan_json(text)
    except JsonError:
        return None
    if not tokens or tokens[0].kind not in "{[":
        return None
    return "".join(t.text for t in tokens)


def from_text(text: str, typed: bool) -> Cell:
    """A typed cell: a number, true / false / null, a JSON list / object, else text; a leading
    ' forces text. Untyped sheets (CSV) keep the text as it is."""
    if not typed:
        return text
    if text in ("", "null"):
        return None
    if text in ("true", "false"):
        return text == "true"
    if NUMBER.fullmatch(text):
        return Num(text)
    if text.startswith("'"):
        return text[1:]
    if text[:1] in ("[", "{"):
        raw = compact_json(text)
        if raw is not None:
            return Raw(raw)
    return text


def guess(text: str) -> Cell:
    """A value read from a file that only holds text (XML): typed like ``from_text`` when that
    keeps exactly what it shows ("12" → a number, "true" → a bool, "" → null), else text."""
    cell = from_text(text, True)
    return cell if not isinstance(cell, Raw) and display(cell) == text else text


def number_value(cell: Cell, typed: bool) -> float | None:
    """The number a cell sorts by: a ``Num`` in JSON spelling, or (untyped) text that reads as
    one. YAML spellings like 0x1F or .inf are numbers too but sort as text."""
    text = cell.text if isinstance(cell, Num) else display(cell).strip(WS) if not typed else ""
    return float(text) if NUMBER.fullmatch(text) else None


def edit_text(cell: Cell, typed: bool) -> str:
    """What the edit box shows: ``from_text(edit_text(c), typed) == c`` (a ' is added to text
    that would otherwise turn into another kind) — except empty text, shown empty (a lone '
    would puzzle; typing nothing into it changes nothing, as the edit is compared first)."""
    if typed and isinstance(cell, str) and cell and from_text(cell, True) != cell:
        return "'" + cell
    return display(cell)
