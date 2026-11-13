"""Changing a photo's details: title, comments, author, copyright, tags, date taken, rotation;
removing the location or every hidden detail. JPEG, PNG and WebP only (the picture data is
never decoded or re-saved). Pure Python; ``web/src/properties/photo.ts`` is the port.

Where each detail goes (what Windows Explorer, phones and photo programs read):
  title     → ImageDescription + XPTitle          comments → XPComment
  author    → Artist + XPAuthor                   copyright → Copyright
  tags      → XPKeywords ("a; b")                 taken → DateTimeOriginal + DateTimeDigitized
  rotation  → Orientation
The matching XMP properties are removed when a detail changes, so an old value can't win."""

from __future__ import annotations

import os
import re
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

from umc.properties import containers, exif, xmp
from umc.properties.exif import TAG

WRITABLE_EXTS = {"jpg", "png", "webp"}
TEMP_PREFIX = ".umc-saving-"  # the new file, next to the original, until it replaces it
FIELDS = ("title", "comment", "author", "copyright", "tags", "taken")
ROTATIONS = {1: "Upright", 6: "Turned 90° right", 3: "Upside down", 8: "Turned 90° left"}

XMP_PROPS = {
    "title": ["dc:title", "dc:description"],
    "comment": ["exif:UserComment"],
    "author": ["dc:creator", "tiff:Artist"],
    "copyright": ["dc:rights"],
    "tags": ["dc:subject"],
    "taken": ["exif:DateTimeOriginal", "exif:DateTimeDigitized", "xmp:CreateDate",
              "photoshop:DateCreated"],
    "orientation": ["tiff:Orientation"],
    "location": ["exif:GPS*", "drone-dji:Gps*", "drone-dji:AbsoluteAltitude",
                 "drone-dji:RelativeAltitude", "photoshop:City", "photoshop:State",
                 "photoshop:Country", "Iptc4xmpCore:Location", "Iptc4xmpCore:CountryCode",
                 "Iptc4xmpExt:LocationCreated", "Iptc4xmpExt:LocationShown",
                 "xmpNote:HasExtendedXMP"],
}  # fmt: skip
# an XMP packet with any of these has a location in it
XMP_PLACE = re.compile(
    r"(exif:GPSL(atitude|ongitude)|drone-dji:Gps|photoshop:City|Iptc4xmpCore:Location"
    r"|Iptc4xmpExt:Location)",
    re.ASCII,
)
WS = exif.WS


class PhotoError(ValueError):
    """A plain-language reason the details can't be changed."""


@dataclass
class PhotoDetails:
    title: str = ""
    comment: str = ""
    author: str = ""
    copyright: str = ""
    tags: str = ""
    taken: str = ""  # "YYYY-MM-DD HH:MM" (the camera's clock), or ""
    orientation: int = 1
    has_location: bool = False

    def value(self, name: str) -> str:
        return str(getattr(self, name))


@dataclass
class PhotoEdit:
    """Only what the user changed: ``values`` maps a field to its new text ("" clears it)."""

    values: dict[str, str] = field(default_factory=dict)
    orientation: int | None = None
    remove_location: bool = False
    remove_all: bool = False

    def is_empty(self) -> bool:
        return not (
            self.values or self.orientation is not None or self.remove_location or self.remove_all
        )


def can_change(ext: str) -> bool:
    return ext.lower() in WRITABLE_EXTS


# ---------------------------------------------------------------------------- values
def tags_text(text: str) -> str:
    """ "a, b;c" → "a; b; c" (empty parts dropped)."""
    parts = [p.strip(WS) for p in re.split(r"[;,]", text)]
    return "; ".join(p for p in parts if p)


def exif_date(display: str) -> str:
    """ "2026-11-12 14:03" (or with seconds) → "2026:11:12 14:03:00"; raises PhotoError."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*",
                     display, re.ASCII)  # fmt: skip
    if not m:
        raise PhotoError("Write the date as year-month-day and time, like 2026-11-12 14:03.")
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    h, mi, s = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31,
            30, 31, 30, 31]  # fmt: skip
    if not (1 <= mo <= 12 and 1 <= d <= days[mo - 1] and h < 24 and mi < 60 and s < 60):
        raise PhotoError("That date doesn't exist. Write it like 2026-11-12 14:03.")
    return f"{y:04d}:{mo:02d}:{d:02d} {h:02d}:{mi:02d}:{s:02d}"


def display_date(exif_text: str | None) -> str:
    m = re.match(r"(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d)", (exif_text or "").strip(WS), re.ASCII)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else ""


def read_details(data: bytes) -> PhotoDetails:
    """What the form shows. Missing EXIF values fall back to the XMP packet's."""
    d = PhotoDetails()
    raw = containers.get_exif(data)
    t = None
    if raw is not None:
        try:
            t = exif.Tiff(raw)
        except exif.ExifError:
            t = None
    packet = containers.get_xmp(data) or ""
    if t is not None:
        d.title = t.text(t.ifd0, TAG["xp_title"]) or t.text(t.ifd0, TAG["description"]) or ""
        d.comment = t.text(t.ifd0, TAG["xp_comment"]) or ""
        d.author = t.text(t.ifd0, TAG["xp_author"]) or t.text(t.ifd0, TAG["artist"]) or ""
        d.copyright = t.text(t.ifd0, TAG["copyright"]) or ""
        d.tags = tags_text(t.text(t.ifd0, TAG["xp_keywords"]) or "")
        d.taken = display_date(t.text(t.exif, TAG["taken"]))
        d.orientation = t.short(t.ifd0, TAG["orientation"]) or 1
        d.has_location = t.gps is not None
    if packet:
        d.title = d.title or xmp.get(packet, "dc:title") or xmp.get(packet, "dc:description") or ""
        d.author = d.author or xmp.get(packet, "dc:creator") or ""
        d.copyright = d.copyright or xmp.get(packet, "dc:rights") or ""
        d.tags = d.tags or tags_text(xmp.get(packet, "dc:subject") or "")
        d.has_location = d.has_location or bool(XMP_PLACE.search(packet))
    return d


# ---------------------------------------------------------------------------- changing
def apply(data: bytes, edit: PhotoEdit) -> bytes:
    """The file with ``edit`` applied (raises PhotoError in plain language)."""
    if containers.kind_of(data) is None:
        raise PhotoError("Only JPG, PNG and WebP pictures can be changed here.")
    try:
        return _apply(data, edit)
    except (exif.ExifError, containers.ContainerError, struct.error) as exc:
        raise PhotoError(f"The picture's details couldn't be changed ({exc}).") from exc


def _apply(data: bytes, edit: PhotoEdit) -> bytes:
    raw = containers.get_exif(data)
    if raw is not None and not edit.remove_all:
        try:
            exif.Tiff(raw)
        except exif.ExifError as exc:
            # never throw the camera's details away quietly
            raise PhotoError(
                "This picture's hidden details are damaged, so they can't be changed safely. "
                "“Remove all hidden details” starts them afresh."
            ) from exc
    if edit.remove_all:
        # keep only the rotation, so the picture still shows the right way up
        old = containers.get_exif(data)
        orientation = 1
        if old is not None:
            try:
                ot = exif.Tiff(old)
                orientation = ot.short(ot.ifd0, TAG["orientation"]) or 1
            except exif.ExifError:
                orientation = 1
        if orientation != 1 and edit.orientation is None:
            edit = PhotoEdit(dict(edit.values), orientation, edit.remove_location, True)
    if raw is None or edit.remove_all:
        raw = exif.empty_tiff()
    t = exif.Tiff(raw)
    e = t.e
    ifd0: dict[int, exif.Change] = {}
    sub: dict[int, exif.Change] = {}

    def text_pair(tag_ascii: int | None, tag_xp: int | None, value: str) -> None:
        if tag_ascii is not None:
            ifd0[tag_ascii] = exif.ascii_value(value) if value else None
        if tag_xp is not None:
            ifd0[tag_xp] = exif.xp_value(value) if value else None

    v = {k: exif.well_formed(edit.values[k]).strip(WS) for k in FIELDS if k in edit.values}
    if "title" in v:
        text_pair(TAG["description"], TAG["xp_title"], v["title"])
    if "comment" in v:
        text_pair(None, TAG["xp_comment"], v["comment"])
    if "author" in v:
        text_pair(TAG["artist"], TAG["xp_author"], v["author"])
    if "copyright" in v:
        text_pair(TAG["copyright"], None, v["copyright"])
    if "tags" in v:
        text_pair(None, TAG["xp_keywords"], tags_text(v["tags"]))
    if "taken" in v:
        stamp = exif_date(v["taken"]) if v["taken"] else ""
        sub[TAG["taken"]] = exif.ascii_value(stamp) if stamp else None
        sub[TAG["digitized"]] = exif.ascii_value(stamp) if stamp else None
    if edit.orientation is not None:
        if edit.orientation not in (1, 2, 3, 4, 5, 6, 7, 8):
            raise PhotoError("That rotation isn't possible.")
        ifd0[TAG["orientation"]] = exif.short_value(edit.orientation, e)
    new_exif = exif.rewrite(raw, ifd0, sub, drop_gps=edit.remove_location or edit.remove_all)
    # XMP: drop what changed (or everything)
    packet = containers.get_xmp(data)
    new_xmp: object = containers.KEEP
    if edit.remove_all:
        new_xmp = None
    elif packet is not None:
        props = [p for k in v for p in XMP_PROPS[k]]
        if edit.orientation is not None:
            props += XMP_PROPS["orientation"]
        if edit.remove_location:
            props += XMP_PROPS["location"]
        if props:
            new_xmp = xmp.remove(packet, props)
    return containers.put(
        data,
        _shrink(new_exif),
        new_xmp,
        strip=edit.remove_all,
        drop_location=edit.remove_location or edit.remove_all,
    )


def _shrink(tiff: bytes) -> bytes | None:
    """No EXIF block at all when nothing is left in it."""
    t = exif.Tiff(tiff)
    return None if not t.ifd0.entries and t.ifd0.next == 0 else tiff


# ---------------------------------------------------------------------------- several files
def common(details: list[PhotoDetails]) -> dict[str, str | None]:
    """Each field's value when every picture has the same one, else None ("Several values")."""
    out: dict[str, str | None] = {}
    for name in FIELDS:
        values = {d.value(name) for d in details}
        out[name] = values.pop() if len(values) == 1 else None
    return out


# ---------------------------------------------------------------------------- saving (desktop)
def save_file(path: Path, edit: PhotoEdit, as_copy: bool = False) -> Path:
    """Write ``edit`` into ``path`` (or into a copy next to it, "name (1).jpg"). The original
    is replaced only once the new file is complete, and keeps its modified date."""
    from umc.core.job import unique_path

    path = path.resolve()  # a link: change the file it points to, keep the link
    data = path.read_bytes()
    out = apply(data, edit)
    if as_copy:
        stem = path.name[: -len(path.suffix)] if path.suffix else path.name
        dest = unique_path(path.parent, stem, path.suffix.lstrip("."))
        dest.write_bytes(out)
        return dest
    st = path.stat()
    tmp = path.with_name(f"{TEMP_PREFIX}{path.name}")
    try:
        tmp.write_bytes(out)
        shutil.copymode(path, tmp)  # a private picture stays private
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise PhotoError(
            f"“{path.name}” couldn't be changed — it may be open in another program, or the "
            "folder is read-only. Close it there and try again, or save a copy."
        ) from exc
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path
