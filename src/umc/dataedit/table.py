"""Sheets, documents and the edits on them (port: web/src/dataedit/table.ts).

Everything is frozen: an edit returns a new sheet (unchanged rows are shared), so undo keeps
plain snapshots."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from umc.codeedit import search
from umc.dataedit.cells import MISSING, NUMBER, WS, Cell, Num, display, edit_text, from_text

Row = tuple[Cell, ...]
MAX_CELLS = 1_000_000


class EditError(ValueError):
    """An edit that can't be done; the message is shown as it is."""


@dataclass(frozen=True)
class Sheet:
    name: str
    columns: tuple[str, ...]
    rows: tuple[Row, ...]
    typed: bool  # cells keep their kind (JSON …); False = every cell is text (CSV / TSV)
    header: bool = True  # CSV / TSV: the first line holds the column names

    @property
    def blank(self) -> Cell:
        """What a new cell holds."""
        return MISSING if self.typed else ""


@dataclass(frozen=True)
class DataDoc:
    format: str  # "csv", "tsv", "json"
    sheets: tuple[Sheet, ...]


def with_sheet(doc: DataDoc, i: int, sheet: Sheet) -> DataDoc:
    return replace(doc, sheets=(*doc.sheets[:i], sheet, *doc.sheets[i + 1 :]))


def letter(i: int) -> str:
    """Spreadsheet column letters: 0 → A, 25 → Z, 26 → AA."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _check_size(columns: int, rows: int) -> None:
    if columns * rows > MAX_CELLS:
        raise EditError(f"That would make more than {MAX_CELLS:,} cells.")


# ---------------------------------------------------------------------------- cells
def set_cell(sheet: Sheet, r: int, c: int, text: str) -> Sheet:
    """The cell typed as ``text`` (unchanged when that's what it shows already)."""
    old = sheet.rows[r][c]
    if text == edit_text(old, sheet.typed):
        return sheet
    row = sheet.rows[r]
    new = (*row[:c], from_text(text, sheet.typed), *row[c + 1 :])
    return replace(sheet, rows=(*sheet.rows[:r], new, *sheet.rows[r + 1 :]))


# ---------------------------------------------------------------------------- rows
def insert_rows(sheet: Sheet, at: int, count: int = 1) -> Sheet:
    at = max(0, min(at, len(sheet.rows)))
    _check_size(len(sheet.columns), len(sheet.rows) + count)
    empty = (sheet.blank,) * len(sheet.columns)
    return replace(sheet, rows=(*sheet.rows[:at], *([empty] * count), *sheet.rows[at:]))


def delete_rows(sheet: Sheet, indices: Iterable[int]) -> Sheet:
    gone = set(indices)
    return replace(sheet, rows=tuple(r for i, r in enumerate(sheet.rows) if i not in gone))


# ---------------------------------------------------------------------------- columns
def new_column_name(sheet: Sheet) -> str:
    n = len(sheet.columns) + 1
    while f"column{n}" in sheet.columns:
        n += 1
    return f"column{n}"


def _check_name(sheet: Sheet, name: str, skip: int | None = None) -> None:
    if sheet.typed and not name:
        raise EditError("A column needs a name.")
    if sheet.header and any(n == name and i != skip for i, n in enumerate(sheet.columns)):
        raise EditError(f"There is already a column called “{name}”.")


def insert_column(sheet: Sheet, at: int, name: str | None = None) -> Sheet:
    at = max(0, min(at, len(sheet.columns)))
    if not sheet.header:
        name = ""
    elif name is None:
        name = new_column_name(sheet)
    else:
        _check_name(sheet, name)
    _check_size(len(sheet.columns) + 1, len(sheet.rows))
    columns = (*sheet.columns[:at], name, *sheet.columns[at:])
    rows = tuple((*r[:at], sheet.blank, *r[at:]) for r in sheet.rows)
    return replace(sheet, columns=columns, rows=rows)


def delete_columns(sheet: Sheet, indices: Iterable[int]) -> Sheet:
    gone = set(indices)
    keep = [i for i in range(len(sheet.columns)) if i not in gone]
    return replace(
        sheet,
        columns=tuple(sheet.columns[i] for i in keep),
        rows=tuple(tuple(r[i] for i in keep) for r in sheet.rows),
    )


def rename_column(sheet: Sheet, c: int, name: str) -> Sheet:
    if not sheet.header:
        raise EditError("Turn on “First row is the header” to name the columns.")
    if name == sheet.columns[c]:
        return sheet
    _check_name(sheet, name, skip=c)
    return replace(sheet, columns=(*sheet.columns[:c], name, *sheet.columns[c + 1 :]))


def set_header(sheet: Sheet, on: bool) -> Sheet:
    """CSV / TSV: the first row becomes the column names, or the names become the first row."""
    if sheet.typed or on == sheet.header:
        return sheet
    if on:
        first = sheet.rows[0] if sheet.rows else ("",) * len(sheet.columns)
        return replace(sheet, header=True, columns=tuple(display(c) for c in first),
                       rows=sheet.rows[1:])  # fmt: skip
    return replace(sheet, header=False, columns=("",) * len(sheet.columns),
                   rows=(sheet.columns, *sheet.rows) if sheet.columns else sheet.rows)  # fmt: skip


# ---------------------------------------------------------------------------- sorting
_RUNS = re.compile(r"[0-9]+|[^0-9]+")


def _natural(text: str) -> tuple[tuple[int, float, str], ...]:
    """ "item2" before "item10": digit runs compare as numbers, the rest ignoring case."""
    return tuple(
        (0, float(part), "") if part[0] in "0123456789" else (1, 0.0, part.lower())
        for part in _RUNS.findall(text)
    )


def sort_key(cell: Cell, typed: bool) -> tuple:
    """Numbers by value, then text in natural order; blanks are kept apart (always last)."""
    if isinstance(cell, Num):
        return (0, float(cell.text), (), "")
    text = display(cell)
    if not typed and NUMBER.fullmatch(text.strip(WS)):
        return (0, float(text.strip(WS)), (), "")
    return (1, 0.0, _natural(text), text)


def is_blank(cell: Cell) -> bool:
    return display(cell) == ""


def sort_rows(sheet: Sheet, c: int, descending: bool = False) -> Sheet:
    """Stable: equal cells keep their order; blank cells go last either way."""
    full = [r for r in sheet.rows if not is_blank(r[c])]
    empty = [r for r in sheet.rows if is_blank(r[c])]
    full.sort(key=lambda r: sort_key(r[c], sheet.typed), reverse=descending)
    return replace(sheet, rows=(*full, *empty))


# ---------------------------------------------------------------------------- find
def find(
    sheet: Sheet, query: str, *, case: bool, whole_word: bool, regex: bool
) -> list[tuple[int, int]]:
    """(row, column) of every cell whose text matches, row by row."""
    if not query:
        return []
    out = []
    for r, row in enumerate(sheet.rows):
        for c, cell in enumerate(row):
            text = display(cell)
            if text and search.find_all(text, query, case=case, whole_word=whole_word,
                                        regex=regex):  # fmt: skip
                out.append((r, c))
    return out


def replace_all(
    sheet: Sheet, query: str, replacement: str, *, case: bool, whole_word: bool, regex: bool
) -> tuple[Sheet, int]:
    """Every match replaced; text stays text, other cells are typed again from the new text."""
    if not query:
        return sheet, 0
    count = 0
    rows = list(sheet.rows)
    for r, row in enumerate(sheet.rows):
        new_row: list[Cell] | None = None
        for c, cell in enumerate(row):
            text = display(cell)
            if not text:
                continue
            new, n = search.replace_all(text, query, replacement, case=case,
                                        whole_word=whole_word, regex=regex)  # fmt: skip
            if not n:
                continue
            count += n
            if new_row is None:
                new_row = list(row)
            new_row[c] = new if isinstance(cell, str) else from_text(new, sheet.typed)
        if new_row is not None:
            rows[r] = tuple(new_row)
    return (replace(sheet, rows=tuple(rows)) if count else sheet), count
