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
    MODEL = "model"
    ARCHIVE = "archive"
    SUBTITLE = "subtitle"
    FONT = "font"


# Aliases → canonical extension.
ALIASES = {
    "jpeg": "jpg",
    "jpe": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "htm": "html",
    "xhtml": "html",
    "markdown": "md",
    "yml": "yaml",
    "stp": "step",
    "tgz": "tar.gz",
    "webvtt": "vtt",
    "sami": "smi",
    "txz": "tar.xz",
    "tbz2": "tar.bz2",
    "tbz": "tar.bz2",
    "igs": "iges",
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
    # camera RAW
    "cr2",
    "cr3",
    "crw",
    "nef",
    "nrw",
    "arw",
    "srf",
    "sr2",
    "dng",
    "orf",
    "raf",
    "rw2",
    "pef",
    "srw",
    "x3f",
    "3fr",
    "iiq",
    "mrw",
    "kdc",
    "dcr",
    "erf",
    "mef",
    "mos",
    "jp2",
    "qoi",
}

AUDIO_EXTS = {
    "mp3",
    "wav",
    "flac",
    "m4a",
    "aac",
    "ogg",
    "oga",
    "opus",
    "wma",
    "aiff",
    "aif",
    "ac3",
    "amr",
    "mka",
    "caf",
    "ape",
    "wv",
    "mp2",
    "au",
}

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
DATA_EXTS = {"json", "yaml", "xml", "tsv"}
MODEL_EXTS = {"stl", "obj", "ply", "glb", "3mf", "off", "step", "iges"}
ARCHIVE_EXTS = {"zip", "7z", "rar", "tar", "tar.gz", "tar.xz", "tar.bz2"}
SUBTITLE_EXTS = {"srt", "vtt", "ass", "ssa", "smi", "lrc"}
FONT_EXTS = {"ttf", "otf", "woff", "woff2"}
COMPOUND_EXTS = ("tar.gz", "tar.xz", "tar.bz2")
PRESENTATION_EXTS = {"pptx", "ppt", "odp"}
DOCUMENT_EXTS = TEXT_DOCUMENT_EXTS | SPREADSHEET_EXTS | PRESENTATION_EXTS | DATA_EXTS


@dataclass(frozen=True)
class DetectedFile:
    path: Path
    family: MediaFamily
    ext: str  # canonical, lowercase, no dot

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        """File name without its extension (``a.tar.gz`` → ``a``)."""
        name = self.path.name
        return (
            name[: -len(self.ext) - 1] if name.lower().endswith("." + self.ext) else self.path.stem
        )


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
    if ext in MODEL_EXTS:
        return MediaFamily.MODEL
    if ext in ARCHIVE_EXTS:
        return MediaFamily.ARCHIVE
    if ext in SUBTITLE_EXTS:
        return MediaFamily.SUBTITLE
    if ext in FONT_EXTS:
        return MediaFamily.FONT
    if ext in DOCUMENT_EXTS:
        return MediaFamily.DOCUMENT
    return None


def _sniff(path: Path) -> str | None:
    """Guess a canonical extension from the first bytes of the file."""
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
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
    if head[:4] in (b"wOFF", b"wOF2"):
        return "woff" if head[:4] == b"wOFF" else "woff2"
    if head[:4] == b"OTTO":
        return "otf"
    if head[:4] in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head[257:262] == b"ustar":
        return "tar"
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
    return "zip"


def detect(path: str | Path) -> DetectedFile | None:
    """Return the detected file, or ``None`` if it isn't a supported format."""
    path = Path(path).resolve()
    lower = path.name.lower()
    compound = next((c for c in COMPOUND_EXTS if lower.endswith("." + c)), None)
    ext = compound or canonical_ext(path.suffix)
    family = family_of_ext(ext)
    if family is None:
        sniffed = _sniff(path)
        if sniffed is None:
            return None
        ext, family = sniffed, family_of_ext(sniffed)
        if family is None:
            return None
    return DetectedFile(path=path, family=family, ext=ext)
