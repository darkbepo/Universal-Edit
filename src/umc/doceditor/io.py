"""Open any editable document as the editor's HTML subset."""

from __future__ import annotations

import html as html_lib
import tempfile
import threading
from pathlib import Path

import markdown

from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.doceditor.docx_reader import read
from umc.doceditor.html_subset import normalise_html, text_length
from umc.doceditor.routes import convert_file
from umc.doceditor.types import MAX_CHARS, MAX_FILE_BYTES, TOO_LARGE, OpenedDocument
from umc.tools.htmlpdf import read_text

EMPTY = "<p></p>"


def _txt_to_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return EMPTY
    return "".join(f"<p>{html_lib.escape(line)}</p>" for line in lines)


def open_document(path: Path, cancel_event: threading.Event | None = None) -> OpenedDocument:
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ConversionError(TOO_LARGE)
    ext = detect(path).ext
    if ext == "docx":
        doc = read(path)
    elif ext == "md":
        body = markdown.markdown(
            read_text(path), extensions=["tables", "fenced_code", "sane_lists"]
        )
        out, images = normalise_html(body, base_dir=path.parent)
        doc = OpenedDocument(out, images, "md")
    elif ext == "txt":
        doc = OpenedDocument(_txt_to_html(read_text(path)), {}, "txt")
    elif ext == "html":
        out, images = normalise_html(read_text(path), base_dir=path.parent)
        doc = OpenedDocument(out, images, "html")
    elif ext in ("odt", "rtf", "doc"):
        with tempfile.TemporaryDirectory(prefix="umc-docedit-") as tmp:
            docx_path = convert_file(path, "docx", Path(tmp) / f"{path.stem}.docx", cancel_event)
            doc = read(docx_path)
        doc.source_format = ext
    else:
        raise ConversionError("This file type can't be edited.")
    if not doc.html.strip():
        doc.html = EMPTY
    if text_length(doc.html) > MAX_CHARS:
        raise ConversionError(TOO_LARGE)
    return doc
