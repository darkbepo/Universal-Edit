"""Data files ↔ documents (port: web/src/dataedit/io.ts). The text goes through
codeedit.textfile, so a save keeps the file's encoding, BOM and line endings; an untouched
document saves the bytes as read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from umc.codeedit import textfile
from umc.codeedit.check import JsonError
from umc.dataedit import csvtext, jsontree, tree
from umc.dataedit.cells import MISSING, WS, display
from umc.dataedit.table import MAX_CELLS, DataDoc, Sheet

MAX_OPEN_BYTES = 20 * 1024 * 1024
FORMATS = ("csv", "tsv", "json")  # what this step opens (YAML / XML / XLSX come next)
SHEET = "Sheet1"


class DataError(ValueError):
    """Why a file can't be opened; the message is shown as it is."""


@dataclass(frozen=True)
class Opened:
    doc: DataDoc  # as read (the history's first snapshot)
    file: textfile.TextFile
    new: bool = False  # made by new_document: never saved as the (empty) bytes read
    sep: str = ","  # CSV / TSV
    quote_all: bool = False
    final_newline: bool = True
    root: Any = None  # JSON: the whole tree, and where the table sits in it
    located: tree.Located | None = None
    indent: str | None = "  "


def _too_big(columns: int, rows: int) -> None:
    if columns * rows > MAX_CELLS:
        raise DataError(f"This file has more than {MAX_CELLS:,} cells: too many for the editor.")


def _line_col(text: str, pos: int) -> tuple[int, int]:
    return text.count("\n", 0, pos) + 1, pos - (text.rfind("\n", 0, pos) + 1) + 1


def open_bytes(data: bytes, ext: str) -> Opened:
    """Raises DataError with a message for the user."""
    ext = ext.lower().lstrip(".")
    if ext not in FORMATS:
        raise DataError(f"The data editor can't open .{ext} files yet.")
    if len(data) > MAX_OPEN_BYTES:
        raise DataError("This file is too big for the data editor (over 20 MB).")
    try:
        f = textfile.decode(data)
    except ValueError as exc:
        raise DataError("This isn't a text file.") from exc
    if ext in ("csv", "tsv"):
        sep = "\t" if ext == "tsv" else csvtext.sniff(f.text)
        parsed = csvtext.parse(f.text, sep)
        width = max((len(r) for r in parsed.rows), default=0)
        _too_big(width, len(parsed.rows))
        rows = [tuple(r) + ("",) * (width - len(r)) for r in parsed.rows]
        sheet = Sheet(SHEET, rows[0] if rows else (), tuple(rows[1:]), typed=False)
        return Opened(DataDoc(ext, (sheet,)), f, sep=sep, quote_all=parsed.quote_all,
                      final_newline=parsed.final_newline or not f.text)  # fmt: skip
    try:
        root = jsontree.parse(f.text)
    except JsonError as exc:
        line, col = _line_col(f.text, exc.pos)
        raise DataError(f"This JSON has a mistake on line {line}, column {col}: "
                        f"{exc.message}.") from exc  # fmt: skip
    if root is None and not f.text.strip(WS):
        root = []
    located = tree.locate(root)
    columns, rows = tree.table_of(root, located)
    _too_big(len(columns), len(rows))
    sheet = Sheet(SHEET, columns, rows, typed=True)
    return Opened(DataDoc(ext, (sheet,)), f, root=root, located=located,
                  indent=jsontree.detect_indent(f.text),
                  final_newline=f.text.endswith("\n") or not f.text.strip(WS))  # fmt: skip


def text_of(opened: Opened, doc: DataDoc) -> str:
    """The document as the file's text ("\\n" line endings)."""
    sheet = doc.sheets[0]
    if doc.format in ("csv", "tsv"):
        rows = [[display(c) for c in r] for r in sheet.rows]
        if sheet.header:
            rows.insert(0, list(sheet.columns))
        return csvtext.write(rows, opened.sep, opened.quote_all, opened.final_newline)
    assert opened.located is not None
    root = tree.put(opened.root, opened.located, sheet.columns, sheet.rows)
    return jsontree.dumps(root, opened.indent) + ("\n" if opened.final_newline else "")


def to_bytes(opened: Opened, doc: DataDoc) -> bytes:
    """Raises ValueError("encoding") when the text doesn't fit the file's encoding (the page
    then offers UTF-8: ``textfile.encode(…, encoding="utf-8")``)."""
    if doc == opened.doc and not opened.new:
        return opened.file.raw
    return textfile.encode(opened.file, text_of(opened, doc))


def new_document(fmt: str = "csv", columns: int = 3, rows: int = 20) -> Opened:
    """An empty table to type into (the hub tile)."""
    typed = fmt == "json"
    names = tuple(f"column{i + 1}" for i in range(columns))

    blank = MISSING if typed else ""
    sheet = Sheet(SHEET, names, tuple((blank,) * columns for _ in range(rows)), typed=typed)
    located = tree.Located((), "list", False) if typed else None
    sep = "\t" if fmt == "tsv" else ","
    return Opened(DataDoc(fmt, (sheet,)), textfile.decode(b""), new=True, sep=sep,
                  root=[] if typed else None, located=located)  # fmt: skip
