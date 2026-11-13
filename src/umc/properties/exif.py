"""Reading and changing a photo's EXIF block (a small TIFF structure) without touching anything
else — pure Python, line-by-line ported to ``web/src/properties/exif.ts`` (both suites check
``tests/fixtures/photo_edit_cases.json``).

Changing is done by *appending*: the original bytes stay where they are (camera maker notes
point into them by absolute offset, so moving them would break them), changed values and the
rewritten directories go at the end, and the pointers are updated. Whatever a change leaves
unreferenced — the old directories, old values, a removed GPS block — is overwritten with zeros,
so an old location or name is really gone from the file, not just hidden.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# field types → bytes per item
SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
BYTE, ASCII, SHORT, LONG, RATIONAL = 1, 2, 3, 4, 5

EXIF_IFD, GPS_IFD = 0x8769, 0x8825
TAG = {
    "description": 0x010E,
    "make": 0x010F,
    "model": 0x0110,
    "orientation": 0x0112,
    "artist": 0x013B,
    "copyright": 0x8298,
    "xp_title": 0x9C9B,
    "xp_comment": 0x9C9C,
    "xp_author": 0x9C9D,
    "xp_keywords": 0x9C9E,
    "taken": 0x9003,  # DateTimeOriginal (Exif IFD)
    "digitized": 0x9004,  # DateTimeDigitized (Exif IFD)
}
MAX_IFD_ENTRIES = 1000


class ExifError(ValueError):
    """The EXIF block is damaged or too big to change safely."""


@dataclass
class Entry:
    tag: int
    type: int
    count: int
    raw: bytes  # the value's bytes (count × size)
    at: int  # absolute offset of the value when stored outside the entry, else -1


@dataclass
class Ifd:
    offset: int
    entries: list[Entry] = field(default_factory=list)
    next: int = 0

    def get(self, tag: int) -> Entry | None:
        return next((e for e in self.entries if e.tag == tag), None)

    def size(self) -> int:
        return 2 + 12 * len(self.entries) + 4


class Tiff:
    """A parsed EXIF block: IFD0, the Exif sub-IFD and the GPS sub-IFD."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 8 or data[:2] not in (b"II", b"MM"):
            raise ExifError("not an EXIF block")
        self.data = bytes(data)
        self.e = "<" if data[:2] == b"II" else ">"
        if self.u16(2) != 42:
            raise ExifError("not an EXIF block")
        self.ifd0 = self.read_ifd(self.u32(4))
        self.exif = self.sub(self.ifd0, EXIF_IFD)
        self.gps = self.sub(self.ifd0, GPS_IFD)

    def u16(self, at: int) -> int:
        return struct.unpack_from(self.e + "H", self.data, at)[0]

    def u32(self, at: int) -> int:
        return struct.unpack_from(self.e + "I", self.data, at)[0]

    def read_ifd(self, at: int) -> Ifd:
        if at < 8 or at + 2 > len(self.data):
            raise ExifError("damaged EXIF block")
        n = self.u16(at)
        if n > MAX_IFD_ENTRIES or at + 2 + 12 * n + 4 > len(self.data):
            raise ExifError("damaged EXIF block")
        ifd = Ifd(at)
        for i in range(n):
            p = at + 2 + 12 * i
            tag, typ, count = self.u16(p), self.u16(p + 2), self.u32(p + 4)
            size = SIZES.get(typ, 0) * count
            if size <= 4:
                raw, where = self.data[p + 8 : p + 8 + size], -1
            else:
                where = self.u32(p + 8)
                if where + size > len(self.data):
                    continue  # a broken entry: leave it out, keep the rest
                raw = self.data[where : where + size]
            ifd.entries.append(Entry(tag, typ, count, raw, where))
        ifd.next = self.u32(at + 2 + 12 * n)
        return ifd

    def sub(self, ifd: Ifd, tag: int) -> Ifd | None:
        e = ifd.get(tag)
        if e is None or e.type not in (LONG, 13) or e.count != 1:
            return None
        try:
            return self.read_ifd(struct.unpack(self.e + "I", e.raw)[0])
        except ExifError:
            return None

    # ------------------------------------------------------------------ values
    def text(self, ifd: Ifd | None, tag: int) -> str | None:
        e = ifd.get(tag) if ifd else None
        if e is None:
            return None
        if tag in (TAG["xp_title"], TAG["xp_comment"], TAG["xp_author"], TAG["xp_keywords"]):
            text = e.raw.decode("utf-16-le", "replace")
        else:
            text = e.raw.decode("utf-8", "replace")
        text = text.split("\x00", 1)[0].strip()
        return text or None

    def short(self, ifd: Ifd | None, tag: int) -> int | None:
        e = ifd.get(tag) if ifd else None
        if e is None or e.type != SHORT or len(e.raw) < 2:
            return None
        return struct.unpack(self.e + "H", e.raw[:2])[0]


# ---------------------------------------------------------------------------- encoding values
def ascii_value(text: str) -> tuple[int, bytes]:
    return ASCII, text.encode("utf-8") + b"\x00"


def xp_value(text: str) -> tuple[int, bytes]:
    """Windows' own fields (Title, Comments, Authors, Tags): UTF-16 in BYTE values."""
    return BYTE, text.encode("utf-16-le") + b"\x00\x00"


def short_value(n: int, e: str) -> tuple[int, bytes]:
    return SHORT, struct.pack(e + "H", n)


def empty_tiff() -> bytes:
    """A valid EXIF block with an empty IFD0 (little-endian)."""
    return b"II*\x00" + struct.pack("<I", 8) + struct.pack("<HI", 0, 0)


# ---------------------------------------------------------------------------- changing
Change = tuple[int, bytes] | None  # (type, raw bytes) to set, None to delete


def _align(buf: bytearray) -> None:
    if len(buf) % 2:
        buf.append(0)


def rewrite(
    data: bytes,
    ifd0: dict[int, Change] | None = None,
    exif: dict[int, Change] | None = None,
    drop_gps: bool = False,
) -> bytes:
    """``data`` with the given tags set or deleted (see the module notes for how)."""
    t = Tiff(data)
    e = t.e
    out = bytearray(t.data)
    wipe: list[tuple[int, int]] = []
    keep: set[int] = set()  # value offsets still referenced after the change

    def put_dir(entries: list[Entry], next_ifd: int) -> int:
        _align(out)
        at = len(out)
        out.extend(struct.pack(e + "H", len(entries)))
        for en in sorted(entries, key=lambda x: x.tag):
            out.extend(struct.pack(e + "HHI", en.tag, en.type, en.count))
            if len(en.raw) <= 4:
                out.extend(en.raw.ljust(4, b"\x00"))
            else:
                out.extend(struct.pack(e + "I", en.at))
        out.extend(struct.pack(e + "I", next_ifd))
        return at

    def apply(ifd: Ifd | None, changes: dict[int, Change]) -> list[Entry]:
        entries = list(ifd.entries) if ifd else []
        for tag, change in changes.items():
            old = next((x for x in entries if x.tag == tag), None)
            if old is not None:
                entries.remove(old)
                if old.at >= 0:
                    wipe.append((old.at, len(old.raw)))
            if change is None:
                continue
            typ, raw = change
            count = len(raw) // SIZES[typ]
            where = -1
            if len(raw) > 4:
                _align(out)
                where = len(out)
                out.extend(raw)
            entries.append(Entry(tag, typ, count, raw, where))
        for en in entries:
            if en.at >= 0:
                keep.add(en.at)
        return entries

    ifd0_changes = dict(ifd0 or {})
    # the Exif sub-IFD
    if exif:
        if t.exif is not None:
            wipe.append((t.exif.offset, t.exif.size()))
        entries = apply(t.exif, exif)
        if entries:
            at = put_dir(entries, t.exif.next if t.exif else 0)
            ifd0_changes[EXIF_IFD] = (LONG, struct.pack(e + "I", at))
        else:
            ifd0_changes[EXIF_IFD] = None
    # the GPS sub-IFD: gone, directory and values
    if drop_gps and t.gps is not None:
        wipe.append((t.gps.offset, t.gps.size()))
        for en in t.gps.entries:
            if en.at >= 0:
                wipe.append((en.at, len(en.raw)))
        ifd0_changes[GPS_IFD] = None
    elif drop_gps and t.ifd0.get(GPS_IFD) is not None:
        ifd0_changes[GPS_IFD] = None  # pointed at garbage: just drop the pointer
    if ifd0_changes:
        wipe.append((t.ifd0.offset, t.ifd0.size()))
        entries = apply(t.ifd0, ifd0_changes)
        at = put_dir(entries, t.ifd0.next)
        out[4:8] = struct.pack(e + "I", at)
    # zero what nothing points at any more (never the header or a kept value)
    for start, length in wipe:
        if start < 8 or start in keep:
            continue
        end = min(start + length, len(t.data))
        out[start:end] = bytes(end - start)
    return bytes(out)
