"""Changing a song's tags: title, artist, album, album artist, track, disc, year, genre and the
cover picture. MP3, M4A, FLAC, OGG and Opus; the sound itself is never decoded or re-encoded.
Desktop only (mutagen, GPL-2.0-or-later); the website writes the same tags with FFmpeg
(``web/src/properties/music.ts``). ``clean_values`` and ``common`` are ported line by line and
both suites check ``tests/fixtures/music_cases.json``.

Where each detail goes:
  MP3        ID3v2.3 (what Windows Explorer and every player read; a v2.4 tag becomes v2.3):
             TIT2 TPE1 TALB TPE2 TRCK TPOS TDRC (→ TYER) TCON, cover = APIC "front cover"
  M4A        ©nam ©ART ©alb aART trkn disk ©day ©gen, cover = covr
  FLAC / OGG / Opus  Vorbis comments TITLE ARTIST ALBUM ALBUMARTIST TRACKNUMBER + TRACKTOTAL
             DISCNUMBER + DISCTOTAL DATE GENRE; cover = a FLAC picture (OGG: base64 in
             METADATA_BLOCK_PICTURE)"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from umc.properties import saving
from umc.properties.exif import WS, well_formed

WRITABLE_EXTS = {"mp3", "m4a", "flac", "ogg", "opus"}
FIELDS = ("title", "artist", "album", "album_artist", "track", "disc", "year", "genre")
TEXT_FIELDS = ("title", "artist", "album", "album_artist", "genre")
MAX_COVER_BYTES = 20 * 1024 * 1024
TEMP_PREFIX = saving.TEMP_PREFIX

ID3_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "track": "TRCK",
    "disc": "TPOS",
    "year": "TDRC",
    "genre": "TCON",
}
MP4_KEYS = {
    "title": "©nam",
    "artist": "©ART",
    "album": "©alb",
    "album_artist": "aART",
    "year": "©day",
    "genre": "©gen",
}
VORBIS_KEYS = {
    "title": ("TITLE",),
    "artist": ("ARTIST",),
    "album": ("ALBUM",),
    "album_artist": ("ALBUMARTIST", "ALBUM ARTIST"),
    "year": ("DATE", "YEAR"),
    "genre": ("GENRE",),
}
VORBIS_NUMBERS = {
    "track": ("TRACKNUMBER", ("TRACKTOTAL", "TOTALTRACKS")),
    "disc": ("DISCNUMBER", ("DISCTOTAL", "TOTALDISCS")),
}


class MusicError(ValueError):
    """A plain-language reason the tags can't be changed."""


@dataclass
class MusicDetails:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: str = ""  # "3" or "3/12"
    disc: str = ""
    year: str = ""
    genre: str = ""
    has_cover: bool = False

    def value(self, name: str) -> str:
        return str(getattr(self, name))


@dataclass
class MusicEdit:
    """Only what the user changed: ``values`` maps a field to its new text ("" clears it).
    ``cover``: new picture bytes (JPG / PNG); ``remove_cover`` drops the picture."""

    values: dict[str, str] = field(default_factory=dict)
    cover: bytes | None = None
    remove_cover: bool = False

    def is_empty(self) -> bool:
        return not (self.values or self.cover is not None or self.remove_cover)


def can_change(ext: str) -> bool:
    return ext.lower() in WRITABLE_EXTS


# ---------------------------------------------------------------------------- values
NUMBER = re.compile(r"(\d{1,4})(?:\s*/\s*(\d{1,4}))?", re.ASCII)
NUMBER_TEXT = {
    "track": (
        "Write the track number like 3, or 3/12 with the number of tracks.",
        "The track number can't be 0.",
        "The track number can't be bigger than the number of tracks.",
    ),
    "disc": (
        "Write the disc number like 1, or 1/2 with the number of discs.",
        "The disc number can't be 0.",
        "The disc number can't be bigger than the number of discs.",
    ),
}
YEAR_TEXT = "Write the year with four digits, like 2026."


def number_pair(text: str) -> tuple[int, int | None] | None:
    """ "3", "3/12", "03 / 12" → (3, None) / (3, 12); anything else → None."""
    m = NUMBER.fullmatch(text.strip(WS))
    if not m:
        return None
    return int(m[1]), int(m[2]) if m[2] is not None else None


def clean_values(values: dict[str, str]) -> dict[str, str]:
    """The user's text → what's written ("" = remove the tag); raises MusicError."""
    out: dict[str, str] = {}
    for name in FIELDS:
        if name not in values:
            continue
        text = well_formed(values[name]).strip(WS)
        if name in NUMBER_TEXT and text:
            pair = number_pair(text)
            shape, zero, bigger = NUMBER_TEXT[name]
            if pair is None:
                raise MusicError(shape)
            n, total = pair
            if n == 0 or total == 0:
                raise MusicError(zero)
            if total is not None and n > total:
                raise MusicError(bigger)
            text = f"{n}/{total}" if total is not None else str(n)
        elif name == "year" and text and not re.fullmatch(r"\d{4}", text, re.ASCII):
            raise MusicError(YEAR_TEXT)
        out[name] = text
    return out


def cover_mime(data: bytes) -> str:
    """ "image/jpeg" / "image/png" from the bytes; raises MusicError for anything else."""
    if len(data) > MAX_COVER_BYTES:
        raise MusicError("That picture is too big for a cover (over 20 MB).")
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    raise MusicError("The cover has to be a JPG or PNG picture.")


def common(details: list[MusicDetails]) -> dict[str, str | None]:
    """Each field's value when every song has the same one, else None ("Several values")."""
    out: dict[str, str | None] = {}
    for name in FIELDS:
        values = {d.value(name) for d in details}
        out[name] = values.pop() if len(values) == 1 else None
    return out


def _number_text(n: object, total: object = None) -> str:
    try:
        n = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    try:
        total = int(total) if total not in (None, "") else 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        total = 0
    if n <= 0:
        return ""
    return f"{n}/{total}" if total else str(n)


def _shown_number(text: str, total: str = "") -> str:
    """A stored "3/12" (or "3" + a separate total) → the form's text; odd text is shown as is."""
    pair = number_pair(text)
    if pair is None:
        return text.strip(WS)
    return _number_text(pair[0], pair[1] if pair[1] is not None else total)


def _year(text: str) -> str:
    m = re.search(r"\d{4}", text or "", re.ASCII)
    return m[0] if m else ""


# ---------------------------------------------------------------------------- reading
def _open(source: object):
    import mutagen

    try:
        f = mutagen.File(source)
    except Exception as exc:  # mutagen raises its own error types for damaged files
        raise MusicError("This file's tags couldn't be read.") from exc
    if f is None:
        raise MusicError("This file's tags couldn't be read.")
    return f


def _kind(f) -> str:
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.ogg import OggFileType

    if isinstance(f, MP3):
        return "id3"
    if isinstance(f, MP4):
        return "mp4"
    if isinstance(f, FLAC):
        return "flac"
    if isinstance(f, OggFileType):
        return "ogg"
    raise MusicError("Only MP3, M4A, FLAC, OGG and Opus files can be changed here.")


def read_details(source: Path | bytes) -> MusicDetails:
    """What the form shows."""
    f = _open(io.BytesIO(source) if isinstance(source, bytes) else source)
    kind = _kind(f)
    d = MusicDetails()
    tags = f.tags
    if tags is None:
        return d
    if kind == "id3":
        for name, frame in ID3_FRAMES.items():
            got = tags.get(frame)
            text = "; ".join(str(t) for t in got.text) if got is not None else ""
            setattr(d, name, text)
        d.track, d.disc = _shown_number(d.track), _shown_number(d.disc)
        d.year = _year(d.year)
        d.has_cover = bool(tags.getall("APIC"))
    elif kind == "mp4":
        for name, key in MP4_KEYS.items():
            setattr(d, name, "; ".join(str(v) for v in tags.get(key, [])))
        for name, key in (("track", "trkn"), ("disc", "disk")):
            pair = (tags.get(key) or [None])[0]
            if pair:
                setattr(d, name, _number_text(*pair))
        d.year = _year(d.year)
        d.has_cover = bool(tags.get("covr"))
    else:
        for name, keys in VORBIS_KEYS.items():
            values = next((tags[k] for k in keys if k in tags and tags[k]), [])
            setattr(d, name, "; ".join(values))
        for name, (key, totals) in VORBIS_NUMBERS.items():
            total = next((tags[t][0] for t in totals if t in tags and tags[t]), "")
            setattr(d, name, _shown_number(tags[key][0], total) if key in tags else "")
        d.year = _year(d.year)
        if kind == "flac":
            d.has_cover = bool(f.pictures)
        else:
            d.has_cover = "METADATA_BLOCK_PICTURE" in tags or "COVERART" in tags
    return d


def read_cover(source: Path | bytes) -> bytes | None:
    """The cover picture's bytes (the front cover when there are several), or None."""
    f = _open(io.BytesIO(source) if isinstance(source, bytes) else source)
    kind = _kind(f)
    tags = f.tags
    if kind == "flac":
        pictures = list(f.pictures)
    elif tags is None:
        return None
    elif kind == "id3":
        pictures = tags.getall("APIC")
    elif kind == "mp4":
        return bytes(tags["covr"][0]) if tags.get("covr") else None
    else:
        from mutagen.flac import Picture

        pictures = []
        for text in tags.get("METADATA_BLOCK_PICTURE", []):
            try:
                pictures.append(Picture(base64.b64decode(text)))
            except Exception:  # a damaged picture block: skip it
                continue
    if not pictures:
        return None
    front = next((p for p in pictures if p.type == 3), pictures[0])
    return bytes(front.data)


# ---------------------------------------------------------------------------- changing
def apply(data: bytes, edit: MusicEdit) -> bytes:
    """The file with ``edit`` applied (raises MusicError in plain language)."""
    values = clean_values(edit.values)
    mime = cover_mime(edit.cover) if edit.cover is not None else None
    buf = io.BytesIO(data)
    f = _open(buf)
    kind = _kind(f)
    if f.tags is None:
        f.add_tags()
    try:
        if kind == "id3":
            _write_id3(f.tags, values, edit, mime)
            buf.seek(0)
            f.save(buf, v2_version=3)
        else:
            if kind == "mp4":
                _write_mp4(f.tags, values, edit, mime)
            else:
                _write_vorbis(f, values, edit, mime)
            buf.seek(0)
            f.save(buf)
    except MusicError:
        raise
    except Exception as exc:
        raise MusicError(f"The tags couldn't be changed ({exc}).") from exc
    return buf.getvalue()


def _write_id3(tags, values: dict[str, str], edit: MusicEdit, mime: str | None) -> None:
    from mutagen import id3

    for name, text in values.items():
        frame = ID3_FRAMES[name]
        tags.delall(frame)
        if frame == "TDRC":  # a v2.3 date is spread over these
            for old in ("TYER", "TDAT", "TIME", "TRDA"):
                tags.delall(old)
        if text:
            tags.add(getattr(id3, frame)(encoding=id3.Encoding.UTF16, text=[text]))
    if mime is not None or edit.remove_cover:
        tags.delall("APIC")
    if mime is not None:
        tags.add(
            id3.APIC(
                encoding=id3.Encoding.LATIN1,
                mime=mime,
                type=id3.PictureType.COVER_FRONT,
                desc="Cover",
                data=edit.cover,
            )
        )


def _write_mp4(tags, values: dict[str, str], edit: MusicEdit, mime: str | None) -> None:
    from mutagen.mp4 import MP4Cover

    for name, text in values.items():
        if name in ("track", "disc"):
            key = "trkn" if name == "track" else "disk"
            pair = number_pair(text) if text else None
            if pair is None:
                tags.pop(key, None)
            else:
                tags[key] = [(pair[0], pair[1] or 0)]
            continue
        key = MP4_KEYS[name]
        if name == "genre":
            tags.pop("gnre", None)  # an old numbered genre would win in some players
        if text:
            tags[key] = [text]
        else:
            tags.pop(key, None)
    if mime is not None or edit.remove_cover:
        tags.pop("covr", None)
    if mime is not None:
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(edit.cover, imageformat=fmt)]


def _drop(tags, key: str) -> None:
    """Remove a Vorbis comment (any letter case)."""
    if key in tags:
        del tags[key]


def _write_vorbis(f, values: dict[str, str], edit: MusicEdit, mime: str | None) -> None:
    from mutagen.flac import FLAC, Picture

    tags = f.tags
    for name, text in values.items():
        if name in VORBIS_NUMBERS:
            key, totals = VORBIS_NUMBERS[name]
            for k in (key, *totals):
                _drop(tags, k)
            pair = number_pair(text) if text else None
            if pair is not None:
                tags[key] = [str(pair[0])]
                if pair[1] is not None:
                    tags[totals[0]] = [str(pair[1])]
            continue
        keys = VORBIS_KEYS[name]
        for k in keys:
            _drop(tags, k)
        if text:
            tags[keys[0]] = [text]
    if mime is None and not edit.remove_cover:
        return
    picture = None
    if mime is not None:
        picture = Picture()
        picture.type = 3  # front cover
        picture.mime = mime
        picture.desc = "Cover"
        picture.data = edit.cover
    if isinstance(f, FLAC):
        f.clear_pictures()
        if picture is not None:
            f.add_picture(picture)
    else:
        for k in ("METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"):
            _drop(tags, k)
        if picture is not None:
            tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(picture.write()).decode("ascii")]


# ---------------------------------------------------------------------------- saving (desktop)
def save_file(path: Path, edit: MusicEdit, as_copy: bool = False) -> Path:
    """Write ``edit`` into ``path`` (or a copy, "name (1).mp3"); see ``saving.save_bytes``."""
    return saving.save_bytes(path, lambda data: apply(data, edit), as_copy, MusicError)
