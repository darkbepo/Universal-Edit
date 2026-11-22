"""Data files ↔ documents (port: web/src/dataedit/io.ts).

Text formats go through codeedit.textfile, so a save keeps the file's encoding, BOM and line
endings; XLSX through ``xlsx`` (only changed sheets are rewritten). An untouched document saves
the bytes as read."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from umc.codeedit import textfile
from umc.codeedit.check import JsonError
from umc.dataedit import csvtext, jsontree, tree, xlsx, xmltree, yamltree
from umc.dataedit.cells import WS, display
from umc.dataedit.table import MAX_CELLS, DataDoc, Sheet

MAX_OPEN_BYTES = 20 * 1024 * 1024
FORMATS = ("csv", "tsv", "json", "yaml", "xml", "xlsx")
TREES = ("json", "yaml", "xml")
NEW_FORMATS = ("csv", "tsv", "json", "yaml", "xml")  # an empty table (the hub tile)
SHEET = "Sheet1"
NAMES = {"csv": "CSV", "tsv": "TSV", "json": "JSON", "yaml": "YAML", "xml": "XML", "xlsx": "Excel"}


class DataError(ValueError):
    """Why a file can't be opened; the message is shown as it is."""


@dataclass(frozen=True)
class Opened:
    doc: DataDoc  # as read (the history's first snapshot)
    file: textfile.TextFile  # (XLSX: empty; the workbook is ``book``)
    new: bool = False  # made by new_document: never saved as the (empty) bytes read
    sep: str = ","  # CSV / TSV
    quote_all: bool = False
    final_newline: bool = True
    root: Any = None  # JSON / YAML / XML: the whole tree, and where the table sits in it
    located: tree.Located | None = None
    indent: str | None = "  "
    declaration: str = ""  # XML
    start_marker: bool = False  # YAML "---"
    book: xlsx.Book | None = None
    data: bytes = b""  # XLSX: the workbook as read
    lost: tuple[str, ...] = field(default=())  # what saving an edited file can't keep


def _too_big(columns: int, rows: int) -> None:
    if columns * rows > MAX_CELLS:
        raise DataError(f"This file has more than {MAX_CELLS:,} cells: too many for the editor.")


def _line_col(text: str, pos: int) -> tuple[int, int]:
    return text.count("\n", 0, pos) + 1, pos - (text.rfind("\n", 0, pos) + 1) + 1


def _tree_doc(ext: str, root: Any) -> tuple[DataDoc, tree.Located]:
    located = tree.locate(root)
    columns, rows = tree.table_of(root, located)
    _too_big(len(columns), len(rows))
    sheet = Sheet(SHEET, columns, rows, typed=True, tree=True)
    return DataDoc(ext, (sheet,)), located


def open_bytes(data: bytes, ext: str) -> Opened:
    """Raises DataError with a message for the user."""
    ext = ext.lower().lstrip(".")
    ext = "yaml" if ext == "yml" else ext
    if ext not in FORMATS:
        raise DataError(f"The data editor can't open .{ext} files.")
    if len(data) > MAX_OPEN_BYTES:
        raise DataError("This file is too big for the data editor (over 20 MB).")
    if ext == "xlsx":
        try:
            book = xlsx.read(data)
        except xlsx.XlsxError as exc:
            raise DataError(str(exc)) from exc
        for s in book.sheets:
            _too_big(len(s.columns), len(s.rows) + 1)
        return Opened(DataDoc(ext, book.sheets), textfile.decode(b""), book=book, data=data)
    try:
        f = textfile.decode(data)
    except ValueError as exc:
        raise DataError("This isn't a text file.") from exc
    text = f.text
    if ext in ("csv", "tsv"):
        sep = "\t" if ext == "tsv" else csvtext.sniff(text)
        parsed = csvtext.parse(text, sep)
        width = max((len(r) for r in parsed.rows), default=0)
        _too_big(width, len(parsed.rows))
        rows = [tuple(r) + ("",) * (width - len(r)) for r in parsed.rows]
        sheet = Sheet(SHEET, rows[0] if rows else (), tuple(rows[1:]), typed=False)
        return Opened(
            DataDoc(ext, (sheet,)),
            f,
            sep=sep,
            quote_all=parsed.quote_all,
            final_newline=parsed.final_newline or not text,
        )
    final = text.endswith("\n") or not text.strip(WS)
    if ext == "json":
        try:
            root = jsontree.parse(text)
        except JsonError as exc:
            line, col = _line_col(text, exc.pos)
            raise DataError(
                f"This JSON has a mistake on line {line}, column {col}: {exc.message}."
            ) from exc
        if root is None and not text.strip(WS):
            root = []
        doc, located = _tree_doc(ext, root)
        return Opened(
            doc,
            f,
            root=root,
            located=located,
            indent=jsontree.detect_indent(text),
            final_newline=final,
        )
    if ext == "yaml":
        try:
            parsed_yaml = yamltree.parse(text)
        except yamltree.YamlError as exc:
            where = f" on line {exc.line}" if exc.line else ""
            raise DataError(f"This YAML has a mistake{where}: {exc.message}.") from exc
        root = [] if parsed_yaml.root is None and not text.strip(WS) else parsed_yaml.root
        doc, located = _tree_doc(ext, root)
        return Opened(
            doc,
            f,
            root=root,
            located=located,
            indent=parsed_yaml.indent,
            start_marker=parsed_yaml.start_marker,
            lost=tuple(parsed_yaml.lost),
        )
    try:
        parsed_xml = xmltree.parse(text)
    except xmltree.XmlError as exc:
        line, col = _line_col(text, exc.pos)
        raise DataError(
            f"This XML has a mistake on line {line}, column {col}: {exc.message}."
        ) from exc
    doc, located = _tree_doc(ext, parsed_xml.root)
    return Opened(
        doc,
        f,
        root=parsed_xml.root,
        located=located,
        indent=parsed_xml.indent,
        declaration=parsed_xml.declaration,
        final_newline=final,
        lost=tuple(parsed_xml.lost),
    )


def text_of(opened: Opened, doc: DataDoc) -> str:
    """A text format's document as the file's text ("\\n" line endings)."""
    sheet = doc.sheets[0]
    if doc.format in ("csv", "tsv"):
        rows = [[display(c) for c in r] for r in sheet.rows]
        if sheet.header:
            rows.insert(0, list(sheet.columns))
        return csvtext.write(rows, opened.sep, opened.quote_all, opened.final_newline)
    assert opened.located is not None
    root = tree.put(opened.root, opened.located, sheet.columns, sheet.rows)
    end = "\n" if opened.final_newline else ""
    if doc.format == "yaml":
        return yamltree.dumps(root, opened.indent or "  ", opened.start_marker)
    if doc.format == "xml":
        return xmltree.dumps(root, opened.declaration, opened.indent) + end
    return jsontree.dumps(root, opened.indent) + end


def to_bytes(opened: Opened, doc: DataDoc, utf8: bool = False) -> bytes:
    """Raises ValueError("encoding") when the text doesn't fit the file's encoding; the page
    then asks and saves again with ``utf8`` (UTF-8, the file's BOM and line endings kept)."""
    if doc == opened.doc and not opened.new and not utf8:
        return opened.file.raw if opened.book is None else opened.data
    if opened.book is not None:
        return xlsx.write(opened.book, doc.sheets)
    encoding = "utf-8" if utf8 else None
    return textfile.encode(opened.file, text_of(opened, doc), encoding=encoding)


def new_document(fmt: str = "csv", columns: int = 3, rows: int = 20) -> Opened:
    """An empty table to type into (the hub tile)."""
    if fmt not in NEW_FORMATS:
        raise DataError(f"A new table can't be made as .{fmt}.")
    is_tree = fmt in TREES
    names = tuple(f"column{i + 1}" for i in range(columns))
    sheet = Sheet(SHEET, names, (), typed=is_tree, tree=is_tree)
    sheet = Sheet(SHEET, names, ((sheet.blank,) * columns,) * rows, typed=is_tree, tree=is_tree)
    root: Any = {"root": {"row": []}} if fmt == "xml" else [] if is_tree else None
    located = tree.locate(root) if is_tree else None
    return Opened(
        DataDoc(fmt, (sheet,)),
        textfile.decode(b""),
        new=True,
        sep="\t" if fmt == "tsv" else ",",
        root=root,
        located=located,
        declaration='<?xml version="1.0" encoding="UTF-8"?>' if fmt == "xml" else "",
    )
