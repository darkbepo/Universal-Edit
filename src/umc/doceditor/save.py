"""Save the editor's content to any document format (no Qt)."""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from umc.converters import document as docroute
from umc.core.errors import ConversionError
from umc.doceditor.docx_writer import write
from umc.doceditor.routes import convert_file
from umc.doceditor.types import DocImages, SaveContent
from umc.tools import htmlpdf

log = logging.getLogger(__name__)

_FIRST = [
    ("docx", "Word (DOCX)"),
    ("odt", "OpenDocument (ODT)"),
    ("rtf", "Rich Text (RTF)"),
    ("doc", "Word 97–2003 (DOC)"),
    ("pdf", "PDF"),
    ("md", "Markdown"),
    ("txt", "Plain text (TXT)"),
    ("html", "Web page (HTML)"),
]
_IMG_SRC = re.compile(r"img/[\w.-]+")
_MIME = {"jpg": "jpeg", "png": "png", "gif": "gif", "bmp": "bmp", "webp": "webp"}


def save_formats() -> list[tuple[str, str]]:
    """``(extension, label)`` for the Save as dialog."""
    first = {e for e, _ in _FIRST}
    reach = docroute.reachable("docx")
    rest = [
        (e, docroute.LABELS.get(e, e.upper()))
        for e in docroute.TEXT_TARGETS
        if e not in first and e in reach
    ]
    return _FIRST + rest


def _libreoffice_pdf() -> bool:
    route = docroute.plan("docx", "pdf")
    return bool(route) and route[0].engine == docroute.SOFFICE


def _data_uris(html_text: str, images: DocImages) -> str:
    def sub(m: re.Match) -> str:
        data = images.get(m.group(0))
        if data is None:
            return m.group(0)
        mime = _MIME.get(m.group(0).rsplit(".", 1)[-1].lower(), "png")
        return f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"

    return _IMG_SRC.sub(sub, html_text)


def _write_images(images: DocImages, folder: Path, prefix: str) -> None:
    for name, data in images.items():
        target = folder / name.removeprefix(prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _build(
    content: SaveContent, fmt: str, tmp: Path, stem: str, cancel_event: threading.Event | None
) -> Path:
    """Write the document into ``tmp``; returns the file (md also makes ``tmp/{stem}_files``)."""
    out = tmp / f"{stem}.{fmt}"
    if fmt == "docx":
        return write(content.html, content.images, out)
    if fmt == "html":
        body = _data_uris(content.html, content.images)
        out.write_text(htmlpdf._page(stem, body), encoding="utf-8", newline="\n")
        return out
    if fmt == "txt":
        text = content.plain_text if content.plain_text is not None else ""
        out.write_text(text, encoding="utf-8", newline="\n")
        return out
    if fmt == "md":
        text = content.markdown if content.markdown is not None else ""
        folder = f"{stem}_files"
        text = _IMG_SRC.sub(
            lambda m: (
                f"{folder}/{m.group(0).removeprefix('img/')}"
                if m.group(0) in content.images
                else m.group(0)
            ),
            text,
        )
        if content.images:
            _write_images(content.images, tmp / folder, "img/")
        out.write_text(text, encoding="utf-8", newline="\n")
        return out
    if fmt == "pdf" and not _libreoffice_pdf():
        from umc.tools import pdf

        _write_images(content.images, tmp, "")
        page = htmlpdf._page(stem, content.html)
        pdf.layout_html(page, htmlpdf.PAGE_CSS, [tmp], out, htmlpdf.A4, htmlpdf.MARGIN, stem)
        return out
    docx_path = write(content.html, content.images, tmp / f"{stem}-src.docx")
    return convert_file(docx_path, fmt, out, cancel_event)


def save_document(
    content: SaveContent,
    dest: Path,
    fmt: str,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Write ``content`` to ``dest`` as ``fmt``; ``dest`` is untouched if anything fails."""
    dest = Path(dest)
    fmt = fmt.lower().lstrip(".")
    saving = dest.with_name(dest.name + ".saving")
    try:
        with tempfile.TemporaryDirectory(prefix="umc-docedit-") as t:
            tmp = Path(t)
            built = _build(content, fmt, tmp, dest.stem, cancel_event)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(built, saving)
            files = tmp / f"{dest.stem}_files"
            if fmt == "md" and files.is_dir():
                shutil.copytree(files, dest.parent / files.name, dirs_exist_ok=True)
            os.replace(saving, dest)
    except OSError as exc:
        log.warning("Could not write %s: %s", dest, exc)
        raise ConversionError(
            f"Couldn't save “{dest.name}” — it may be open in another program."
        ) from exc
    finally:
        saving.unlink(missing_ok=True)
    return dest
