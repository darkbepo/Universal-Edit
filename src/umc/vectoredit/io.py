"""SVG files ↔ vector documents (port: web/src/vectoredit/io.ts).

Text goes through ``codeedit.textfile`` (encoding, BOM and line endings kept); ``.svgz`` is
gunzipped and saved gzipped again. An untouched document saves the bytes as read.
"""

from __future__ import annotations

import gzip
import zlib
from dataclasses import dataclass

from umc.codeedit import textfile
from umc.vectoredit import doc as vdoc
from umc.vectoredit.doc import VectorDoc, VectorError
from umc.vectoredit.geom import num

MAX_OPEN_BYTES = 20 * 1024 * 1024
MAX_ELEMENTS = 100_000


@dataclass(frozen=True)
class Opened:
    doc: VectorDoc  # as read (the history's first snapshot)
    file: textfile.TextFile
    gz: bool = False
    new: bool = False  # made by new_document: always written
    data: bytes = b""  # the file as read (gzipped for .svgz)


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(data, MAX_OPEN_BYTES + 1)
    except zlib.error as e:
        raise VectorError("This compressed SVG (.svgz) is damaged.") from e
    if len(out) > MAX_OPEN_BYTES:
        raise VectorError("This drawing is too big for the editor (over 20 MB).")
    return out


def _count(el) -> int:
    return 1 + sum(_count(c) for c in el.elements)


def open_bytes(data: bytes) -> Opened:
    if len(data) > MAX_OPEN_BYTES:
        raise VectorError("This drawing is too big for the editor (over 20 MB).")
    gz = data[:2] == b"\x1f\x8b"
    raw = _gunzip(data) if gz else data
    try:
        f = textfile.decode(raw)
    except ValueError as e:
        raise VectorError("This file isn't text, so it can't be an SVG drawing.") from e
    doc = vdoc.parse(f.text)
    if _count(doc.root) > MAX_ELEMENTS:
        raise VectorError(f"This drawing has more than {MAX_ELEMENTS:,} parts: too many to edit.")
    return Opened(doc, f, gz, False, data)


def to_bytes(opened: Opened, doc: VectorDoc, *, utf8: bool = False) -> bytes:
    """The file to save. Raises ValueError("encoding") if a character doesn't fit the file's
    encoding (then ask, and pass ``utf8=True``)."""
    if doc is opened.doc and not opened.new and not utf8:
        return opened.data
    text = vdoc.write(doc)
    body = textfile.encode(opened.file, text, encoding="utf-8" if utf8 else None)
    if not opened.gz:
        return body
    return gzip.compress(body, mtime=0)


def new_text(width: float, height: float, unit: str = "px") -> str:
    w, h = num(width), num(height)
    size = f'width="{w}{unit}" height="{h}{unit}"' if unit != "px" else f'width="{w}" height="{h}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" {size} viewBox="0 0 {w} {h}">\n</svg>\n'
    )


def new_document(width: float, height: float, unit: str = "px") -> Opened:
    text = new_text(width, height, unit)
    data = text.encode("utf-8")
    return Opened(vdoc.parse(text), textfile.decode(data), False, True, data)
