"""Detect which media family a file belongs to (by extension, falling back to magic bytes)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class MediaFamily(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


# Aliases → canonical extension.
ALIASES = {
    "jpeg": "jpg",
    "jpe": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "htm": "html",
    "xhtml": "html",
    "markdown": "md",
    "heif": "heic",
    "mpeg": "mpg",
    "text": "txt",
}

IMAGE_EXTS = {
    "png",
    "jpg",
    "webp",
    "bmp",
    "tiff",
    "gif",
    "ico",
    "heic",
    "avif",
    "tga",
    "ppm",
    "pgm",
    "pbm",
    "pcx",
    "dds",
    "psd",
    "jp2",
    "qoi",
}

AUDIO_EXTS = {
    "mp3", "wav", "flac", "m4a", "aac", "ogg", "oga", "opus", "wma", "aiff", "aif", "ac3",
    "amr", "mka", "caf", "ape", "wv", "mp2", "au",
}  # fmt: skip

VIDEO_EXTS = {
    "mp4",
    "mkv",
    "mov",
    "avi",
    "webm",
    "flv",
    "wmv",
    "m4v",
    "mpg",
    "ts",
    "mts",
    "m2ts",
    "3gp",
    "ogv",
    "vob",
    "mxf",
    "asf",
}

TEXT_DOCUMENT_EXTS = {
    "pdf",
    "docx",
    "doc",
    "odt",
    "rtf",
    "txt",
    "md",
    "html",
    "epub",
    "rst",
    "tex",
    "org",
    "xps",
    "mobi",
    "fb2",
    "cbz",
    "wpd",
}
SPREADSHEET_EXTS = {"xlsx", "xls", "ods", "csv"}
PRESENTATION_EXTS = {"pptx", "ppt", "odp"}
DOCUMENT_EXTS = TEXT_DOCUMENT_EXTS | SPREADSHEET_EXTS | PRESENTATION_EXTS


@dataclass(frozen=True)
class DetectedFile:
    path: Path
    family: MediaFamily
    ext: str  # canonical, lowercase, no dot

    @property
    def name(self) -> str:
        return self.path.name


def canonical_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return ALIASES.get(ext, ext)


def family_of_ext(ext: str) -> MediaFamily | None:
    ext = canonical_ext(ext)
    if ext in IMAGE_EXTS:
        return MediaFamily.IMAGE
    if ext in VIDEO_EXTS:
        return MediaFamily.VIDEO
    if ext in AUDIO_EXTS:
        return MediaFamily.AUDIO
    if ext in DOCUMENT_EXTS:
        return MediaFamily.DOCUMENT
    return None


def _sniff(path: Path) -> str | None:
    """Guess a canonical extension from the first bytes of the file."""
    try:
        with path.open("rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"FORM") and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "opus" if b"OpusHead" in head else "ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "avi"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "webm" if b"webm" in head else "mkv"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
            return "heic"
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand == b"qt  ":
            return "mov"
        if brand in (b"M4A ", b"M4B "):
            return "m4a"
        return "mp4"
    if head.startswith(b"FLV"):
        return "flv"
    if head.startswith(b"\x30\x26\xb2\x75"):
        return "wmv"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc"  # OLE container; could also be xls/ppt
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(path)
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return None


def _sniff_zip(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mime = zf.read("mimetype").decode(errors="ignore").strip()
                return {
                    "application/epub+zip": "epub",
                    "application/vnd.oasis.opendocument.text": "odt",
                    "application/vnd.oasis.opendocument.spreadsheet": "ods",
                    "application/vnd.oasis.opendocument.presentation": "odp",
                }.get(mime)
            if any(n.startswith("word/") for n in names):
                return "docx"
            if any(n.startswith("xl/") for n in names):
                return "xlsx"
            if any(n.startswith("ppt/") for n in names):
                return "pptx"
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def detect(path: str | Path) -> DetectedFile | None:
    """Return the detected file, or ``None`` if it isn't a supported format."""
    path = Path(path).resolve()
    ext = canonical_ext(path.suffix)
    family = family_of_ext(ext)
    if family is None:
        sniffed = _sniff(path)
        if sniffed is None:
            return None
        ext, family = sniffed, family_of_ext(sniffed)
        if family is None:
            return None
    return DetectedFile(path=path, family=family, ext=ext)
