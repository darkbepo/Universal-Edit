"""Open any editable document as the editor's HTML subset."""

from __future__ import annotations

import html as html_lib
import re
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
MD_EXTENSIONS = ["tables", "fenced_code", "sane_lists"]  # md_writer writes what these read
# A table with only a header row gets one empty body row from Python-Markdown.
_LONE_EMPTY_ROW = re.compile(r"</thead>\s*<tbody>\s*<tr>\s*(?:<td></td>\s*)+</tr>\s*</tbody>")
NBSP = "\xa0"


def _keep_spaces(line: str) -> str:
    """A space at the start of the line or after another space becomes a no-break space."""
    return "".join(
        NBSP if c == " " and (i == 0 or line[i - 1] == " ") else c for i, c in enumerate(line)
    )


def split_front_matter(text: str) -> tuple[str, str]:
    """(front matter, rest): a first line "---" up to a later "---" / "..." line, verbatim."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            front = "".join(lines[: i + 1]).replace("\r\n", "\n")  # saved with "\n" like the rest
            return front, "".join(lines[i + 1 :])
    return "", text


def _txt_to_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return EMPTY
    return "".join(f"<p>{html_lib.escape(_keep_spaces(line))}</p>" for line in lines)


def open_document(path: Path, cancel_event: threading.Event | None = None) -> OpenedDocument:
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ConversionError(TOO_LARGE)
    ext = detect(path).ext
    if ext == "docx":
        doc = read(path)
    elif ext == "md":
        front, text = split_front_matter(read_text(path))
        body = markdown.markdown(text, extensions=MD_EXTENSIONS)
        body = _LONE_EMPTY_ROW.sub("</thead>", body)  # added by Python-Markdown, not the file
        out, images = normalise_html(body, base_dir=path.parent)
        doc = OpenedDocument(out, images, "md", front_matter=front)
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
