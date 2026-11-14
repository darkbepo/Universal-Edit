"""Detect which media family a file belongs to (by extension, falling back to magic bytes)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from umc.codeedit.languages import LANGUAGES
from umc.codeedit.textfile import SNIFF_BYTES, looks_like_text


class MediaFamily(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    MODEL = "model"
    ARCHIVE = "archive"
    SUBTITLE = "subtitle"
    FONT = "font"
    VECTOR = "vector"
    CAD = "cad"
    PROJECT = "project"  # a video editor project (.umcvideo)
    CODE = "code"  # code and text-config files (the code editor; see codeedit/languages.py)


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
    "epsf": "eps",
    "epsi": "eps",
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
    "ora",  # OpenRaster (layered; written by the editor, GIMP, Krita)
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
    "azw3",
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
VECTOR_EXTS = {"svg", "eps", "ps", "ai"}
CAD_EXTS = {"dxf", "dwg"}
PROJECT_EXTS = {"umcvideo"}
COMPOUND_EXTS = ("tar.gz", "tar.xz", "tar.bz2")
PRESENTATION_EXTS = {"pptx", "ppt", "odp"}
DOCUMENT_EXTS = TEXT_DOCUMENT_EXTS | SPREADSHEET_EXTS | PRESENTATION_EXTS | DATA_EXTS
_OTHER_EXTS = (IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS | DOCUMENT_EXTS | MODEL_EXTS | ARCHIVE_EXTS) | (
    SUBTITLE_EXTS | FONT_EXTS | VECTOR_EXTS | CAD_EXTS | PROJECT_EXTS
)
# Code files: the language table's extensions that no other family owns. "ts" is in both
# (MPEG transport stream video, TypeScript): detect() looks inside the file.
CODE_EXTS = {e for lang in LANGUAGES for e in lang.exts} - (_OTHER_EXTS - {"ts"})
CODE_FILENAMES = {n for lang in LANGUAGES for n in lang.filenames}


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
        if self.ext and name.lower().endswith("." + self.ext):
            return name[: -len(self.ext) - 1] or name  # ".gitignore" stays ".gitignore"
        return self.path.stem


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
    if ext in VECTOR_EXTS:
        return MediaFamily.VECTOR
    if ext in CAD_EXTS:
        return MediaFamily.CAD
    if ext in PROJECT_EXTS:
        return MediaFamily.PROJECT
    if ext in DOCUMENT_EXTS:
        return MediaFamily.DOCUMENT
    if ext in CODE_EXTS:
        return MediaFamily.CODE
    return None


def _head(path: Path, size: int) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(size)
    except OSError:
        return b""


def is_mpeg_ts(head: bytes) -> bool:
    """MPEG transport stream: a 0x47 sync byte every 188 bytes (192 with M2TS's 4-byte header)."""
    if len(head) < 392:
        return False
    return all(head[i] == 0x47 for i in (0, 188, 376)) or all(
        head[i] == 0x47 for i in (4, 196, 388)
    )


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
    if head.startswith(b"%!PS-Adobe") and b"EPSF" in head[:40]:
        return "eps"
    if head.startswith(b"%!"):
        return "ps"
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
    if lower in CODE_FILENAMES:  # Dockerfile, .gitignore, …
        return DetectedFile(path=path, family=MediaFamily.CODE, ext=lower.lstrip("."))
    compound = next((c for c in COMPOUND_EXTS if lower.endswith("." + c)), None)
    ext = compound or canonical_ext(path.suffix)
    if ext == "ts":
        family = MediaFamily.VIDEO if is_mpeg_ts(_head(path, 392)) else MediaFamily.CODE
        return DetectedFile(path=path, family=family, ext=ext)
    family = family_of_ext(ext)
    if family is None:
        sniffed = _sniff(path)
        if sniffed is not None:
            ext, family = sniffed, family_of_ext(sniffed)
        elif looks_like_text(_head(path, SNIFF_BYTES)):
            family = MediaFamily.CODE  # text of a kind we don't know: plain text
        if family is None:
            return None
    return DetectedFile(path=path, family=family, ext=ext)
