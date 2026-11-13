"""Which view a file gets in the viewer, and the other files in its folder it can show."""

from __future__ import annotations

import re
from pathlib import Path

from umc.core.detect import COMPOUND_EXTS, DetectedFile, MediaFamily, canonical_ext, family_of_ext
from umc.core.editors import EDITORS, EditorInfo
from umc.doceditor.types import EDITABLE_DOCUMENT_EXTS

# View kinds
IMAGE = "image"  # a picture (Qt or Pillow decodes it)
VIDEO = "video"  # played with sound
AUDIO = "audio"
PAGES = "pages"  # PyMuPDF opens it directly: page by page
CONVERT = "convert"  # turned into a PDF first (in the background), then shown page by page
TEXT = "text"  # plain text shown as it is
MODEL = "model"  # 3D view
OTHER = "other"  # name, kind and size only

PAGES_EXTS = {"pdf", "epub", "xps", "cbz", "fb2", "mobi", "svg"}
TEXT_EXTS = {"txt", "md", "csv", "tsv", "json", "yaml", "xml", "rst", "org", "tex"}
TEXT_EXTS |= {"srt", "vtt", "ass", "ssa", "lrc", "smi"}
# Converter target that turns the file into a PDF for previewing (needs the converter's tools)
PREVIEW_TARGETS = {
    MediaFamily.DOCUMENT: "doc:pdf",
    MediaFamily.VECTOR: "vec:pdf",
    MediaFamily.CAD: "cad:pdf",
}

# Which editor the Edit button opens for each view kind
_EDITOR_OF_KIND = {IMAGE: "image", MODEL: "model", VIDEO: "video", AUDIO: "audio"}
_EDITOR_OF_KIND |= {PAGES: "document", CONVERT: "document", TEXT: "document"}

ICON_OF_KIND = {IMAGE: "photo", VIDEO: "movie", AUDIO: "music", PAGES: "pdf"}
ICON_OF_KIND |= {CONVERT: "document", TEXT: "document", MODEL: "cube", OTHER: "file"}


def _ext_of_name(name: str) -> str:
    low = name.lower()
    for compound in COMPOUND_EXTS:
        if low.endswith("." + compound):
            return compound
    return canonical_ext(Path(name).suffix)


def kind_of(family: MediaFamily | None, ext: str) -> str:
    if family == MediaFamily.IMAGE:
        return IMAGE
    if family == MediaFamily.VIDEO:
        return VIDEO
    if family == MediaFamily.AUDIO:
        return AUDIO
    if family == MediaFamily.MODEL:
        return MODEL
    if ext in PAGES_EXTS:
        return PAGES
    if ext in TEXT_EXTS:
        return TEXT
    if family in PREVIEW_TARGETS:
        return CONVERT
    return OTHER


def view_kind(f: DetectedFile) -> str:
    return kind_of(f.family, f.ext)


def kind_of_path(path: Path) -> str:
    """By the file name only (fast: used for every file in a folder)."""
    ext = _ext_of_name(path.name)
    return kind_of(family_of_ext(ext), ext)


def natural_key(name: str) -> list:
    """ "img2" before "img10", case-insensitive — like File Explorer sorts."""
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", name)]


def folder_items(path: Path) -> list[Path]:
    """Every file next to ``path`` the viewer can show (hidden files skipped), sorted like
    File Explorer, always including ``path`` itself."""
    path = Path(path)
    items: list[Path] = []
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        entries = []
    for p in entries:
        if p.name.startswith(".") or p == path:
            continue
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if kind_of_path(p) != OTHER:
            items.append(p)
    items.append(path)
    items.sort(key=lambda p: natural_key(p.name))
    return items


def editor_for(kind: str) -> EditorInfo | None:
    """The editor Edit opens for this kind, or None. ``available`` False = not ready yet."""
    editor_id = _EDITOR_OF_KIND.get(kind)
    return next((e for e in EDITORS if e.id == editor_id), None)


def can_edit(kind: str, path: Path) -> bool:
    """Whether Edit opens ``path`` (of view ``kind``): its editor is ready and, for documents,
    the document editor can open that format (not PDF / EPUB, which it only converts)."""
    editor = editor_for(kind)
    if editor is None or not editor.available:
        return False
    return editor.id != "document" or _ext_of_name(path.name) in EDITABLE_DOCUMENT_EXTS
