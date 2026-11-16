"""Changing a video's details: title, description, date recorded, the rotation flag, and each
sound / subtitle track's language and which one plays by default. MP4, MOV, M4V, MKV, WebM.

Nothing is decoded: FFmpeg copies every track as it is (``-c copy``) into a new file and only the
details change, so it's quick and loses no quality. ``details_from_probe`` (ffprobe-style JSON
→ what the form shows), ``clean_values`` and ``ffmpeg_args`` are ported line by line to
``web/src/properties/video.ts``; both suites check ``tests/fixtures/video_cases.json``. The
website builds the same JSON from FFmpeg's ``-i`` output (``probeFromLog``).

Where each detail goes:
  title        format tag ``title`` (MP4 ©nam, Matroska TITLE) — Windows "Title"
  description  ``comment`` and ``description`` (MP4 ©cmt + desc) — Windows "Comments"
  date         ``creation_time`` (MP4 mvhd, Matroska DateUTC) — Windows "Media created"
  rotation     MP4 / MOV / M4V only (Matroska has no display matrix): FFmpeg 6+
               ``-display_rotation`` on the input, older ones the ``rotate`` stream tag
  tracks       stream ``language`` (ISO 639-2) and the ``default`` disposition

A plain copy would drop ``creation_time`` (FFmpeg leaves it out unless ``-map_metadata`` is
given), so it's always given. Tracks the container can't hold again (a data track like an action
camera's GPS, an odd subtitle format) are listed in ``lost`` and named before saving."""

from __future__ import annotations

import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from umc.properties import saving
from umc.properties.exif import WS, well_formed

WRITABLE_EXTS = {"mp4", "mov", "m4v", "mkv", "webm"}
ROTATING_EXTS = {"mp4", "mov", "m4v"}
MP4_LIKE = ROTATING_EXTS
FIELDS = ("title", "description", "date", "rotation")
ROTATIONS = (0, 90, 180, 270)  # clockwise, how a player turns the picture
TRACK_KINDS = ("audio", "subtitle")
MP4_SUBTITLES = {"mov_text", "dvd_subtitle"}
WEBM_SUBTITLES = {"webvtt"}
TEMP_PREFIX = saving.TEMP_PREFIX

# ISO 639-2/B codes the track menus offer (others found in a file are kept and shown as is)
LANGUAGES = (
    ("ara", "Arabic"), ("baq", "Basque"), ("bul", "Bulgarian"), ("cat", "Catalan"),
    ("chi", "Chinese"), ("hrv", "Croatian"), ("cze", "Czech"), ("dan", "Danish"),
    ("dut", "Dutch"), ("eng", "English"), ("est", "Estonian"), ("fin", "Finnish"),
    ("fre", "French"), ("ger", "German"), ("gre", "Greek"), ("heb", "Hebrew"),
    ("hin", "Hindi"), ("hun", "Hungarian"), ("ice", "Icelandic"), ("ind", "Indonesian"),
    ("ita", "Italian"), ("jpn", "Japanese"), ("kor", "Korean"), ("lav", "Latvian"),
    ("lit", "Lithuanian"), ("nor", "Norwegian"), ("nob", "Norwegian Bokmål"),
    ("nno", "Norwegian Nynorsk"), ("per", "Persian"), ("pol", "Polish"),
    ("por", "Portuguese"), ("rum", "Romanian"), ("rus", "Russian"), ("srp", "Serbian"),
    ("slo", "Slovak"), ("slv", "Slovenian"), ("spa", "Spanish"), ("swe", "Swedish"),
    ("tha", "Thai"), ("tur", "Turkish"), ("ukr", "Ukrainian"), ("vie", "Vietnamese"),
)  # fmt: skip
LANGUAGE_NAMES = dict(LANGUAGES)
# the /T spellings some files use for the same languages
LANGUAGE_ALIASES = {
    "zho": "chi", "ces": "cze", "nld": "dut", "fra": "fre", "deu": "ger", "ell": "gre",
    "isl": "ice", "fas": "per", "ron": "rum", "slk": "slo", "eus": "baq", "sve": "swe",
}  # fmt: skip
# MOV (not MP4) keeps only the old Macintosh language list, which spells a few differently and
# lacks some (FFmpeg writes "und" for those)
MOV_SPELLING = {"fre": "fra", "swe": "sve", "rum": "ron", "cze": "ces", "slo": "slk"}
MOV_MISSING = {"hrv", "srp", "nob", "nno"}
MOV_LANGUAGE_TEXT = "MOV videos can't store that language — choose another or save as MP4."

DATE_RE = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", re.ASCII
)
DATE_TEXT = "Write the date like 2026-11-15, or 2026-11-15 14:30 with the time."
DATE_RANGE_TEXT = "The date has to be between the years 1904 and 2100."
ROTATION_TEXT = "The rotation has to be 0, 90, 180 or 270 degrees."
NOT_ROTATING_TEXT = "MKV and WebM videos can't store a rotation — only MP4, MOV and M4V can."
LANGUAGE_TEXT = "A language has to be a 3-letter code like eng."
FORMAT_TEXT = "Only MP4, MOV, M4V, MKV and WebM videos can be changed here."


class VideoError(ValueError):
    """A plain-language reason the details can't be changed."""


@dataclass
class Track:
    index: int  # the stream's number in the file (-map 0:index)
    kind: str  # "audio" | "subtitle"
    codec: str = ""
    language: str = ""  # ISO 639-2, "" = not set ("und")
    default: bool = False
    title: str = ""


@dataclass
class VideoDetails:
    container: str = ""  # the extension, lower case
    title: str = ""
    description: str = ""
    date: str = ""  # UTC, "2026-11-15T14:30:00Z", "" = none
    rotation: int = 0  # clockwise
    tracks: list[Track] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)  # what can't be kept when saved
    streams: list[dict] = field(default_factory=list)  # {index, type, codec, keep}

    def value(self, name: str) -> str:
        return str(getattr(self, name))


@dataclass
class VideoEdit:
    """Only what the user changed. ``values``: title / description ("" clears), date (the
    user's local text, "" clears), rotation ("0" / "90" / "180" / "270"). ``languages``:
    stream index → code ("" = not set). ``defaults``: kind → the stream index that plays by
    default, or None for none of that kind."""

    values: dict[str, str] = field(default_factory=dict)
    languages: dict[int, str] = field(default_factory=dict)
    defaults: dict[str, int | None] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.values or self.languages or self.defaults)


def can_change(ext: str) -> bool:
    return ext.lower() in WRITABLE_EXTS


def allows_no_default(ext: str) -> bool:
    """MP4 / MOV / M4V always play one track of each kind (FFmpeg turns the first one on when
    none is marked), so "none" is only offered for MKV / WebM."""
    return ext.lower() not in MP4_LIKE


# ---------------------------------------------------------------------------- values
def parse_date(text: str) -> tuple[int, int, int, int, int, int] | None:
    """ "2026-11-15", "2026-11-15 14:30", "2026-11-15T14:30:05" → the parts; else None."""
    m = DATE_RE.fullmatch(text.strip(WS))
    if not m:
        return None
    parts = tuple(int(p) if p is not None else 0 for p in m.groups())
    y, mo, d, h, mi, s = parts
    try:
        date(y, mo, d)
    except ValueError:
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    return y, mo, d, h, mi, s  # type: ignore[return-value]


def utc_text(parts: tuple[int, int, int, int, int, int], offset_minutes: int) -> str:
    """Local date parts at ``offset_minutes`` east of UTC → "YYYY-MM-DDTHH:MM:SSZ"."""
    local = datetime(*parts)
    utc = local - timedelta(minutes=offset_minutes)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_text(iso: str, offset_minutes: int) -> str:
    """A UTC "…Z" date → "YYYY-MM-DD HH:MM" (":SS" when not 0) at ``offset_minutes``."""
    m = DATE_RE.match(iso.strip(WS))
    if not m:
        return ""
    parts = parse_date(m[0])
    if parts is None:
        return ""
    t = datetime(*parts) + timedelta(minutes=offset_minutes)
    return t.strftime("%Y-%m-%d %H:%M" + (":%S" if t.second else ""))


def local_offset(parts: tuple[int, int, int, int, int, int]) -> int:
    """This computer's offset from UTC (minutes) at that local time (daylight saving included)."""
    try:
        off = datetime(*parts).astimezone().utcoffset()
    except (OverflowError, OSError, ValueError):
        off = None
    return int(off.total_seconds() // 60) if off is not None else 0


def offset_at_utc(iso: str) -> int:
    """This computer's offset from UTC (minutes) at that UTC moment."""
    m = DATE_RE.match(iso.strip(WS))
    parts = parse_date(m[0]) if m else None
    if parts is None:
        return 0
    try:
        moment = datetime(*parts, tzinfo=UTC).astimezone()
    except (OverflowError, OSError, ValueError):
        return 0
    off = moment.utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def stored_date(stamp: str) -> str:
    """A file's ``creation_time`` ("2026-11-15T14:30:00.000000Z") → "2026-11-15T14:30:00Z";
    "" when missing or a zero date (1904-01-01 / 1970-01-01 00:00 mean "not set")."""
    m = DATE_RE.match(stamp.strip(WS))
    parts = parse_date(m[0]) if m else None
    if parts is None or parts[0] <= 1904 or parts == (1970, 1, 1, 0, 0, 0):
        return ""
    return utc_text(parts, 0)


def shown_date(iso: str) -> str:
    """What the form shows for a stored UTC date (this computer's local time)."""
    return local_text(iso, offset_at_utc(iso)) if iso else ""


def clean_values(values: dict[str, str], offset: Callable[[tuple], int] = local_offset) -> dict:
    """The user's text → what's written: title / description text, date as UTC "…Z" ("" =
    remove), rotation as an int; raises VideoError. ``offset`` gives the local offset (minutes)
    for a date's parts (tests pass a fixed one)."""
    out: dict = {}
    for name in FIELDS:
        if name not in values:
            continue
        text = well_formed(values[name]).strip(WS)
        if name == "date":
            if not text:
                out[name] = ""
                continue
            parts = parse_date(text)
            if parts is None:
                raise VideoError(DATE_TEXT)
            if not 1904 <= parts[0] <= 2100:
                raise VideoError(DATE_RANGE_TEXT)
            out[name] = utc_text(parts, offset(parts))
        elif name == "rotation":
            try:
                turn = int(text)
            except ValueError:
                raise VideoError(ROTATION_TEXT) from None
            if turn not in ROTATIONS:
                raise VideoError(ROTATION_TEXT)
            out[name] = turn
        else:
            out[name] = text
    return out


def clean_language(code: str) -> str:
    code = code.strip(WS).lower()
    if code in ("", "und"):
        return ""
    if not re.fullmatch(r"[a-z]{3}", code, re.ASCII):
        raise VideoError(LANGUAGE_TEXT)
    return LANGUAGE_ALIASES.get(code, code)


def common(details: list[VideoDetails]) -> dict[str, str | None]:
    """Each field's value (as text) when every video has the same one, else None."""
    out: dict[str, str | None] = {}
    for name in FIELDS:
        values = {d.value(name) for d in details}
        out[name] = values.pop() if len(values) == 1 else None
    return out


# ---------------------------------------------------------------------------- reading
def _tags(d: dict) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (d.get("tags") or {}).items()}


def _keep(ext: str, s: dict) -> tuple[bool, str | None]:
    """Can the container hold this stream again? (keep, what's lost if not; None = say nothing)"""
    kind = s.get("codec_type") or ""
    codec = s.get("codec_name") or ""
    if kind in ("video", "audio"):
        return True, None
    if kind == "subtitle":
        if ext in MP4_LIKE and codec not in MP4_SUBTITLES:
            return False, "a subtitle track this format can't hold"
        if ext == "webm" and codec not in WEBM_SUBTITLES:
            return False, "a subtitle track this format can't hold"
        return True, None
    if kind == "attachment":
        return (True, None) if ext == "mkv" else (False, "an attached file")
    if kind == "data":
        tag = (s.get("codec_tag_string") or "").lower()
        if tag == "tmcd":  # FFmpeg writes the timecode track again from the video's tag
            return False, None
        return False, "a data track (like GPS from an action camera)"
    return False, "a track of an unknown kind"


def details_from_probe(data: dict, ext: str) -> VideoDetails:
    """ffprobe-style JSON (``-show_format -show_streams``) → what the form shows."""
    ext = ext.lower()
    if ext not in WRITABLE_EXTS:
        raise VideoError(FORMAT_TEXT)
    fmt = _tags(data.get("format") or {})
    d = VideoDetails(container=ext)
    d.title = fmt.get("title", "")
    d.description = fmt.get("comment") or fmt.get("description") or ""
    d.date = stored_date(fmt.get("creation_time", ""))
    lost: list[str] = []
    first_video = True
    for s in data.get("streams") or []:
        index = int(s.get("index", 0))
        kind = s.get("codec_type") or ""
        codec = s.get("codec_name") or ""
        disposition = s.get("disposition") or {}
        keep, why = _keep(ext, s)
        cover = bool(disposition.get("attached_pic"))
        d.streams.append(
            {"index": index, "type": kind, "codec": codec, "cover": cover, "keep": keep}
        )
        if why and why not in lost:
            lost.append(why)
        if kind == "video" and not cover and first_video:
            first_video = False
            from umc.properties.read import clockwise

            d.rotation = clockwise(s)
        if kind in TRACK_KINDS and keep:
            tags = _tags(s)
            lang = tags.get("language", "")
            d.tracks.append(
                Track(
                    index=index,
                    kind=kind,
                    codec=codec,
                    language="" if lang in ("", "und") else LANGUAGE_ALIASES.get(lang, lang),
                    default=bool(disposition.get("default")),
                    title=tags.get("title", ""),
                )
            )
    if any(k.startswith("com.apple.quicktime.") for k in fmt):
        lost.append("the camera details (make, model, place)")
    d.lost = lost
    return d


def read_details(path: Path) -> VideoDetails:
    from umc.core.errors import ConversionError
    from umc.tools.ffmpeg import probe_json

    try:
        data = probe_json(path)
    except ConversionError as exc:
        raise VideoError(f"“{path.name}” couldn't be read.") from exc
    return details_from_probe(data, path.suffix.lstrip("."))


def main_video(details: VideoDetails) -> int | None:
    """The stream index of the picture (not a cover)."""
    return next(
        (s["index"] for s in details.streams if s["type"] == "video" and not s["cover"]), None
    )


# ---------------------------------------------------------------------------- changing
def ffmpeg_args(
    ext: str,
    src: str,
    dst: str,
    details: VideoDetails,
    edit: VideoEdit,
    clean: dict,
    major: int | None,
) -> list[str]:
    """FFmpeg arguments that copy ``src`` to ``dst`` with ``edit`` applied. ``clean`` =
    ``clean_values(edit.values)``; ``major`` = FFmpeg's major version (None = new)."""
    ext = ext.lower()
    if ext not in WRITABLE_EXTS:
        raise VideoError(FORMAT_TEXT)
    if "rotation" in clean and ext not in ROTATING_EXTS:
        raise VideoError(NOT_ROTATING_TEXT)
    kept = [s for s in details.streams if s["keep"]]
    if not any(s["type"] == "video" and not s["cover"] for s in kept):
        raise VideoError("This file has no picture to keep.")
    out_of = {s["index"]: n for n, s in enumerate(kept)}
    video = main_video(details)
    new_style = major is None or major >= 6
    args: list[str] = []
    turn = clean.get("rotation")
    if turn is not None and new_style and video is not None:
        # the display matrix is counter-clockwise
        args += [f"-display_rotation:{video}", str((360 - turn) % 360)]
    args += ["-i", src]
    for s in kept:
        args += ["-map", f"0:{s['index']}"]
    args += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    if turn is not None and not new_style and video is not None:
        # FFmpeg 5 reads the old "rotate" tag counter-clockwise too (4 and older: clockwise)
        tag = (360 - turn) % 360 if major == 5 else turn
        args += [f"-metadata:s:{out_of[video]}", f"rotate={tag}"]
    if "title" in clean:
        args += ["-metadata", f"title={clean['title']}"]
    if "description" in clean:
        args += ["-metadata", f"comment={clean['description']}"]
        args += ["-metadata", f"description={clean['description']}"]
    if "date" in clean:
        args += ["-metadata", f"creation_time={clean['date']}"]
    tracks = {t.index: t for t in details.tracks}
    for index in sorted(edit.languages):
        if index not in tracks:
            continue
        code = clean_language(edit.languages[index])
        if ext == "mov":
            if code in MOV_MISSING:
                raise VideoError(MOV_LANGUAGE_TEXT)
            code = MOV_SPELLING.get(code, code)
        args += [f"-metadata:s:{out_of[index]}", f"language={code or 'und'}"]
    for kind in TRACK_KINDS:
        if kind not in edit.defaults:
            continue
        chosen = edit.defaults[kind]
        for t in details.tracks:
            if t.kind == kind:
                flag = "+default" if t.index == chosen else "-default"
                args += [f"-disposition:{out_of[t.index]}", flag]
    args.append(dst)
    return args


def check_edit(details: VideoDetails, edit: VideoEdit) -> None:
    """Refuse track changes that don't fit this file (raises VideoError)."""
    tracks = {t.index: t for t in details.tracks}
    for index, code in edit.languages.items():
        if index not in tracks:
            raise VideoError("That track isn't in this video any more.")
        clean_language(code)
    for kind, chosen in edit.defaults.items():
        if kind not in TRACK_KINDS:
            raise VideoError("Only sound and subtitle tracks have a default.")
        if chosen is None and not allows_no_default(details.container):
            raise VideoError("MP4, MOV and M4V videos always have a default track of each kind.")
        if chosen is not None and (chosen not in tracks or tracks[chosen].kind != kind):
            raise VideoError("That track isn't in this video any more.")


# ---------------------------------------------------------------------------- saving (desktop)
def save_file(
    path: Path,
    edit: VideoEdit,
    as_copy: bool = False,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Write ``edit`` into ``path`` (or a copy, "name (1).mp4"); see ``saving.save_with``.
    FFmpeg writes the new file; ``cancel`` stops it (nothing is changed then)."""
    from umc.core.errors import ConversionCancelled, ConversionError
    from umc.tools import ffmpeg

    clean = clean_values(edit.values)
    details = read_details(path)
    check_edit(details, edit)
    ext = path.suffix.lstrip(".").lower()
    need = path.stat().st_size + 50_000_000
    try:
        free = shutil.disk_usage(path.resolve().parent).free
    except OSError:
        free = need
    if free < need:
        raise VideoError(
            f"There isn't enough free space next to “{path.name}” — saving needs about "
            f"{need // 1_000_000} MB for the new copy."
        )

    def write(tmp: Path) -> None:
        args = ffmpeg_args(ext, str(path), str(tmp), details, edit, clean, ffmpeg.major_version())
        # the temp name doesn't end in .mp4, so name the container
        muxer = {"mp4": "mp4", "m4v": "ipod", "mov": "mov", "mkv": "matroska", "webm": "webm"}
        args = [*args[:-1], "-f", muxer[ext], args[-1]]
        try:
            ffmpeg.run_ffmpeg(
                args,
                total_seconds=_duration(path),
                progress=progress or (lambda _p: None),
                cancel_event=cancel or threading.Event(),
                error_message=f"The details of “{path.name}” couldn't be changed.",
            )
        except ConversionCancelled:
            raise
        except ConversionError as exc:
            raise VideoError(str(exc)) from exc

    return saving.save_with(path, write, as_copy, VideoError)


def _duration(path: Path) -> float:
    from umc.tools.ffmpeg import probe

    try:
        return probe(path).duration
    except Exception:  # only for the progress bar
        return 0.0
