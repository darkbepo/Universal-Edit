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
from urllib.parse import quote

from umc.converters import document as docroute
from umc.core.errors import ConversionError
from umc.doceditor.docx_writer import write
from umc.doceditor.md_writer import to_markdown
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
_HTML_SRC = re.compile(r'(\bsrc=")(img/[\w.-]+)(")')
_MD_LINK = re.compile(r'((?<!\\)\]\()(img/[\w.-]+)((?:\s+"[^"]*")?\))')
_OURS = re.compile(r"^\d+\.(png|jpg|gif|bmp|webp)$")
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
        data = images.get(m.group(2))
        if data is None:
            return m.group(0)
        mime = _MIME.get(m.group(2).rsplit(".", 1)[-1].lower(), "png")
        uri = f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"
        return m.group(1) + uri + m.group(3)

    return _HTML_SRC.sub(sub, html_text)


def _files_folder(dest: Path) -> str:
    """Name of the picture folder next to ``dest``: ours is reused, anything else is kept."""
    base = f"{dest.stem}_files"
    n = 0
    while True:
        name = base if n == 0 else f"{base} ({n})"
        folder = dest.parent / name
        if not folder.exists():
            return name
        if (
            n == 0
            and folder.is_dir()
            and all(f.is_file() and _OURS.match(f.name) for f in folder.iterdir())
        ):
            return name
        n += 1


def _swap_in(new: Path, folder: Path) -> None:
    if folder.exists():  # only ever an earlier save's pictures (see _files_folder)
        shutil.rmtree(folder)
    shutil.move(str(new), str(folder))


def _write_images(images: DocImages, folder: Path, prefix: str) -> None:
    for name, data in images.items():
        target = folder / name.removeprefix(prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _build(
    content: SaveContent,
    fmt: str,
    tmp: Path,
    stem: str,
    cancel_event: threading.Event | None,
    files_name: str = "",
) -> Path:
    """Write the document into ``tmp``; returns the file (md also makes ``tmp/{stem}_files``)."""
    out = tmp / f"{stem}.{fmt}"
    if fmt == "docx":
        return write(content.html, content.images, out)
    if fmt == "html":
        body = _data_uris(content.html, content.images)
        out.write_text(htmlpdf._page(content.title or stem, body), encoding="utf-8", newline="\n")
        return out
    if fmt == "txt":
        text = content.plain_text if content.plain_text is not None else ""
        out.write_text(text, encoding="utf-8", newline="\n")
        return out
    if fmt == "md":
        link = quote(files_name, safe="()")
        text = _MD_LINK.sub(
            lambda m: (
                f"{m.group(1)}{link}/{m.group(2).removeprefix('img/')}{m.group(3)}"
                if m.group(2) in content.images
                else m.group(0)
            ),
            to_markdown(content.html),
        )
        if content.front_matter:  # kept exactly as it was read
            front = content.front_matter
            text = front + ("" if front.endswith("\n") else "\n") + ("\n" + text if text else "")
        if content.images:
            _write_images(content.images, tmp / "_pics", "img/")
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
    files_name = _files_folder(dest) if fmt == "md" else ""
    try:
        with tempfile.TemporaryDirectory(prefix="umc-docedit-") as t:
            tmp = Path(t)
            built = _build(content, fmt, tmp, dest.stem, cancel_event, files_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(built, saving)
            os.replace(saving, dest)
            if files_name and (tmp / "_pics").is_dir():
                _swap_in(tmp / "_pics", dest.parent / files_name)
    except OSError as exc:
        log.warning("Could not write %s: %s", dest, exc)
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (32, 33):
            msg = f"Couldn't save “{dest.name}” — it may be open in another program."
        else:
            msg = f"Couldn't save “{dest.name}”: {exc.strerror or exc}."
        raise ConversionError(msg) from exc
    finally:
        saving.unlink(missing_ok=True)
    return dest
