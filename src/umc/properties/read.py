"""Desktop readers: a file → the plain facts ``info.build`` turns into sections.

``describe`` never raises: what can't be read is left out (and logged), the "File" section is
always there. PyMuPDF is used through ``tools/pdf.py`` (the GUI runs ``describe`` for PDFs on its
single PyMuPDF thread)."""

from __future__ import annotations

import logging
import math
import re
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from umc.core.detect import DetectedFile, MediaFamily
from umc.properties import format as fmt
from umc.properties.info import Section, build

log = logging.getLogger(__name__)

NOUN = {
    MediaFamily.IMAGE: "image",
    MediaFamily.VIDEO: "video",
    MediaFamily.AUDIO: "audio",
    MediaFamily.DOCUMENT: "document",
    MediaFamily.MODEL: "3D model",
    MediaFamily.ARCHIVE: "archive",
    MediaFamily.SUBTITLE: "subtitles",
    MediaFamily.FONT: "font",
    MediaFamily.VECTOR: "vector graphic",
    MediaFamily.CAD: "CAD drawing",
    MediaFamily.CODE: "code file",
}
OFFICE_EXTS = {"docx", "docm", "dotx", "xlsx", "xlsm", "pptx", "pptm", "ppsx"}
ODF_EXTS = {"odt", "ods", "odp", "odg"}
NOTE = "Some details of this file couldn't be read."


def describe(f: DetectedFile) -> list[Section]:
    facts = read_facts(f)
    try:
        return build(facts)
    except Exception:  # an odd value in a file: still show what the file is
        log.warning("properties: couldn't show the details of %s", f.path, exc_info=True)
        return build({"file": facts.get("file") or {}, "note": NOTE})


def read_facts(f: DetectedFile) -> dict:
    facts: dict = {"file": file_facts(f)}
    reader = _READERS.get(f.family)
    if f.family == MediaFamily.DOCUMENT and f.ext not in ({"pdf"} | OFFICE_EXTS | ODF_EXTS):
        reader = None
    if reader is None:
        return facts
    try:
        key, value = reader(f)
        facts[key] = value
    except Exception:
        log.warning("properties: couldn't read %s", f.path, exc_info=True)
        facts["note"] = NOTE
    return facts


def file_facts(f: DetectedFile) -> dict:
    out = {"name": f.name, "type": f"{f.ext.upper()} {NOUN.get(f.family, 'file')}"}
    try:
        st = f.path.stat()
        out["size"] = st.st_size
        out["modified"] = fmt.date(datetime.fromtimestamp(st.st_mtime))
    except OSError:
        pass
    out["folder"] = str(f.path.parent)
    return out


# ----------------------------------------------------------------------------- dates
def local_text(d: datetime) -> str:
    """A moment → local display text (naive times are taken as UTC, like MP4 / Office do)."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return fmt.date(d.astimezone().replace(tzinfo=None))


def iso_local(text: str) -> str | None:
    try:
        return local_text(datetime.fromisoformat(text.strip().replace("Z", "+00:00")))
    except (ValueError, AttributeError):
        return None


def exif_date(text: str) -> str | None:
    """EXIF "2026:11:12 14:03:00" (camera's own clock, no time zone) → "2026-11-12 14:03"."""
    m = re.match(r"(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d)", (text or "").strip())
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else None


PDF_DATE = r"D?:?(\d{4})(\d\d)?(\d\d)?(\d\d)?(\d\d)?(\d\d)?([Zz+-])?(\d\d)?'?(\d\d)?"


def pdf_date(text: str) -> str | None:
    """PDF "D:20261112140300+01'00'" → local display text."""
    m = re.match(PDF_DATE, text or "")
    if not m:
        return None
    parts = [int(x) if x else d for x, d in zip(m.groups()[:6], (0, 1, 1, 0, 0, 0), strict=True)]
    try:
        d = datetime(*parts)  # type: ignore[arg-type]
    except ValueError:
        return None
    sign = m[7]
    if sign in ("+", "-"):
        from datetime import timedelta, timezone

        off = timedelta(hours=int(m[8] or 0), minutes=int(m[9] or 0))
        return local_text(d.replace(tzinfo=timezone(off if sign == "+" else -off)))
    if sign in ("Z", "z"):
        return local_text(d.replace(tzinfo=UTC))
    return fmt.date(d)  # no zone: shown as written


# ----------------------------------------------------------------------------- pictures
def _text(v: object) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        v = v.replace("\x00", "").strip()
        return v or None
    return None


def _num(v: object) -> float | None:
    if isinstance(v, tuple | list):
        v = v[0] if v else None
    try:
        x = float(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return x if x is not None and math.isfinite(x) else None  # EXIF 0/0 reads as nan


def _dms(v: object) -> float | None:
    try:
        d, m, s = (float(x) for x in v)  # type: ignore[union-attr]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = d + m / 60 + s / 3600
    return value if math.isfinite(value) else None


def _picture(f: DetectedFile) -> tuple[str, dict]:
    from PIL import Image

    from umc.converters.imaging import RAW_EXTS, _register_plugins

    if f.path.suffix.lower() in RAW_EXTS or f".{f.ext}" in RAW_EXTS:
        import rawpy

        with rawpy.imread(str(f.path)) as raw:
            return "picture", {
                "width": raw.sizes.width,
                "height": raw.sizes.height,
                "kind": "colour",
            }
    _register_plugins()
    with Image.open(f.path) as img:
        mode = img.mode
        p: dict = {"width": img.width, "height": img.height}
        if mode == "1":
            p["kind"] = "bw"
        elif mode == "CMYK":
            p["kind"] = "cmyk"
        elif mode.startswith(("L", "I", "F")):
            p["kind"] = "greyscale"
        else:
            p["kind"] = "colour"
        p["alpha"] = "A" in mode or (mode in ("P", "L", "RGB") and "transparency" in img.info)
        p["frames"] = int(getattr(img, "n_frames", 1) or 1)
        dpi = img.info.get("dpi")
        if dpi:
            p["dpi"] = [float(dpi[0]), float(dpi[1])]
        exif = img.getexif()
        p["orientation"] = int(exif.get(0x0112) or 1)
        p["make"] = _text(exif.get(0x010F))
        p["model"] = _text(exif.get(0x0110))
        p["description"] = _text(exif.get(0x010E))
        p["artist"] = _text(exif.get(0x013B))
        p["copyright"] = _text(exif.get(0x8298))
        sub = exif.get_ifd(0x8769)
        p["taken"] = exif_date(_text(sub.get(0x9003)) or _text(exif.get(0x0132)) or "")
        p["lens"] = _text(sub.get(0xA434))
        p["exposure"] = _num(sub.get(0x829A))
        p["fnumber"] = _num(sub.get(0x829D))
        p["iso"] = _num(sub.get(0x8827))
        p["focal"] = _num(sub.get(0x920A))
        flash = sub.get(0x9209)
        p["flash"] = int(flash) if isinstance(flash, int) else None
        gps = exif.get_ifd(0x8825)
        lat, lon = _dms(gps.get(2)), _dms(gps.get(4))
        if lat is not None and lon is not None:
            p["lat"] = -lat if _text(gps.get(1)) == "S" else lat
            p["lon"] = -lon if _text(gps.get(3)) == "W" else lon
            alt = _num(gps.get(6))
            if alt is not None:
                p["altitude"] = -alt if gps.get(5) in (1, b"\x01") else alt
    return "picture", {k: v for k, v in p.items() if v is not None}


# ----------------------------------------------------------------------------- video / audio
TAG_KEYS = {
    "title": ("title",),
    "artist": ("artist",),
    "album": ("album",),
    "album_artist": ("album_artist", "albumartist", "album artist"),
    "track": ("track", "tracknumber"),
    "disc": ("disc", "discnumber"),
    "date": ("date", "year"),
    "genre": ("genre",),
    "composer": ("composer",),
    "comment": ("comment", "description"),
}


def iso6709(text: str) -> tuple[float, float] | None:
    m = re.match(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", text or "")
    return (float(m[1]), float(m[2])) if m else None


def clockwise(stream: dict) -> int:
    """How far a player turns the picture, clockwise (what the browser reports too). ffprobe's
    display-matrix "rotation" is counter-clockwise; an old "rotate" tag is clockwise."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(-float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                return 0
    try:
        return int(round(float((stream.get("tags") or {}).get("rotate") or 0))) % 360
    except (TypeError, ValueError):
        return 0


def media_facts(data: dict, size: int) -> dict:
    """ffprobe JSON → media facts (also used by the tests with hand-made JSON)."""
    streams = data.get("streams") or []
    fmt_info = data.get("format") or {}
    from umc.tools.ffmpeg import _parse_rate

    m: dict = {"duration": float(fmt_info.get("duration") or 0), "size": size}
    pictures = [s for s in streams if (s.get("disposition") or {}).get("attached_pic")]
    video = next((s for s in streams if s.get("codec_type") == "video" and s not in pictures), None)
    if video:
        m["video"] = {
            "codec": video.get("codec_name", ""),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": _parse_rate(video.get("avg_frame_rate"))
            or _parse_rate(video.get("r_frame_rate")),
            "rotation": clockwise(video),
        }
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    m["audio"] = [
        {
            "codec": a.get("codec_name", ""),
            "channels": int(a.get("channels") or 0),
            "sample_rate": int(a.get("sample_rate") or 0),
            "language": (a.get("tags") or {}).get("language", ""),
        }
        for a in audio
    ]
    m["subtitles"] = [
        {"language": (s.get("tags") or {}).get("language", "")}
        for s in streams
        if s.get("codec_type") == "subtitle"
    ]
    raw: dict[str, str] = {}
    for source in [fmt_info.get("tags") or {}] + [a.get("tags") or {} for a in audio[:1]]:
        for k, v in source.items():
            raw.setdefault(str(k).lower(), str(v))
    m["tags"] = {
        key: next((raw[n] for n in names if raw.get(n)), None) for key, names in TAG_KEYS.items()
    }
    m["cover"] = bool(pictures)
    where = raw.get("location") or raw.get("com.apple.quicktime.location.iso6709")
    if where and iso6709(where):
        m["lat"], m["lon"] = iso6709(where)
    if video and raw.get("creation_time"):
        m["recorded"] = iso_local(raw["creation_time"])
    return m


def _media(f: DetectedFile) -> tuple[str, dict]:
    from umc.tools.ffmpeg import probe_json

    return "media", media_facts(probe_json(f.path), f.path.stat().st_size)


# ----------------------------------------------------------------------------- documents
NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}


def _find(root: ET.Element | None, path: str) -> str | None:
    if root is None:
        return None
    el = root.find(path, NS)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(name))
    except (KeyError, ET.ParseError):
        return None


def office_facts(zf: zipfile.ZipFile) -> dict:
    core, app = _xml(zf, "docProps/core.xml"), _xml(zf, "docProps/app.xml")
    num = lambda p: int(_find(app, p) or 0) or None  # noqa: E731
    created, modified = _find(core, "dcterms:created"), _find(core, "dcterms:modified")
    return {
        "title": _find(core, "dc:title"),
        "subject": _find(core, "dc:subject"),
        "author": _find(core, "dc:creator"),
        "last_saved_by": _find(core, "cp:lastModifiedBy"),
        "keywords": _find(core, "cp:keywords"),
        "created": iso_local(created) if created else None,
        "modified": iso_local(modified) if modified else None,
        "company": _find(app, "ep:Company"),
        "application": _find(app, "ep:Application"),
        "pages": num("ep:Pages"),
        "words": num("ep:Words"),
        "slides": num("ep:Slides"),
    }


def odf_facts(zf: zipfile.ZipFile) -> dict:
    root = _xml(zf, "meta.xml")
    meta = root.find("office:meta", NS) if root is not None else None
    stats = meta.find("meta:document-statistic", NS) if meta is not None else None
    stat = lambda k: int(stats.get(f"{{{NS['meta']}}}{k}") or 0) if stats is not None else 0  # noqa: E731
    created, modified = _find(meta, "meta:creation-date"), _find(meta, "dc:date")
    app = _find(meta, "meta:generator")
    return {
        "title": _find(meta, "dc:title"),
        "subject": _find(meta, "dc:subject"),
        "author": _find(meta, "meta:initial-creator"),
        "last_saved_by": _find(meta, "dc:creator"),
        "keywords": _find(meta, "meta:keyword"),
        "created": iso_local(created) if created else None,
        "modified": iso_local(modified) if modified else None,
        "application": app.split("/")[0] if app else None,
        "pages": stat("page-count") or None,
        "words": stat("word-count") or None,
    }


def _document(f: DetectedFile) -> tuple[str, dict]:
    if f.ext == "pdf":
        from umc.tools import pdf

        info = pdf.document_info(f.path)
        meta = info["meta"]
        d = {
            "pages": info["pages"],
            "page_size": info["page_size"],
            "encrypted": info["encrypted"],
            "pdf_version": meta.get("format"),
            "title": meta.get("title"),
            "subject": meta.get("subject"),
            "author": meta.get("author"),
            "keywords": meta.get("keywords"),
            "application": meta.get("creator") or meta.get("producer"),
            "created": pdf_date(meta.get("creationDate") or ""),
            "modified": pdf_date(meta.get("modDate") or ""),
        }
    else:
        with zipfile.ZipFile(f.path) as zf:
            d = office_facts(zf) if f.ext in OFFICE_EXTS else odf_facts(zf)
    return "document", {k: v for k, v in d.items() if v not in (None, "")}


# ----------------------------------------------------------------------------- fonts / 3D
def best_name(records: list[tuple[int, int, int, int, str]], name_id: int) -> str | None:
    """(platform, encoding, language, id, text) → English Windows first, then any Windows,
    then Mac — the same order as the web's font reader."""
    order = (
        lambda r: r[0] == 3 and r[2] == 0x409,
        lambda r: r[0] == 3,
        lambda r: r[0] == 1,
        lambda r: True,
    )
    candidates = [r for r in records if r[3] == name_id and r[4].strip()]
    for test in order:
        for r in candidates:
            if test(r):
                return r[4].strip()
    return None


def _font(f: DetectedFile) -> tuple[str, dict]:
    from fontTools.ttLib import TTFont

    with TTFont(str(f.path), lazy=True, fontNumber=0) as font:
        records = []
        for n in font["name"].names:
            try:
                records.append((n.platformID, n.platEncID, n.langID, n.nameID, n.toUnicode()))
            except UnicodeDecodeError:
                continue
        pick = lambda i: best_name(records, i)  # noqa: E731
        return "font", {
            "family": pick(16) or pick(1),
            "style": pick(17) or pick(2),
            "version": pick(5),
            "designer": pick(9),
            "manufacturer": pick(8),
            "copyright": pick(0),
            "glyphs": font["maxp"].numGlyphs,
            "outlines": "cff" if ("CFF " in font or "CFF2" in font) else "ttf",
            "axes": [a.axisTag for a in font["fvar"].axes] if "fvar" in font else [],
        }


def _model(f: DetectedFile) -> tuple[str, list]:
    from umc.editor3d.mesh import describe as describe_model
    from umc.editor3d.mesh import load_view_model

    return "model", [list(r) for r in describe_model(load_view_model(f.path, f.ext))]


_READERS = {
    MediaFamily.IMAGE: _picture,
    MediaFamily.VIDEO: _media,
    MediaFamily.AUDIO: _media,
    MediaFamily.DOCUMENT: _document,
    MediaFamily.FONT: _font,
    MediaFamily.MODEL: _model,
}


def uses_pdf_engine(f: DetectedFile) -> bool:
    """Reading this touches PyMuPDF (run it on the PDF thread)."""
    return f.family == MediaFamily.DOCUMENT and f.ext == "pdf"
