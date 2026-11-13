"""Writes tests/fixtures/photo_edit_cases.json: small pictures + edits → the exact bytes the
desktop's properties/photo.py produces. The web port must produce the same bytes
(web/tests/photo.test.ts). Run on purpose only, after changing photo.py / exif.py /
containers.py / xmp.py:  python scripts/make_photo_cases.py"""

from __future__ import annotations

import io
import json
import struct
import sys
import zlib
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from umc.core.samples import photo_exif, write_test_photo  # noqa: E402
from umc.properties import containers, photo  # noqa: E402

XMP = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    ' <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
    ' tiff:Orientation="6" exif:GPSLatitude="55,40.566N" exif:GPSLongitude="12,34.198E">\n'
    '  <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Old title</rdf:li></rdf:Alt></dc:title>\n'
    "  <dc:creator><rdf:Seq><rdf:li>Old Author</rdf:li></rdf:Seq></dc:creator>\n"
    "  <dc:subject><rdf:Bag><rdf:li>one</rdf:li><rdf:li>two</rdf:li></rdf:Bag></dc:subject>\n"
    " </rdf:Description>\n</rdf:RDF></x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)


XMP2 = (
    "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
    "<rdf:Description rdf:about='' exif:GPSLatitude='55,40.566N' exif:GPSLongitude='12,34.198E'"
    " photoshop:City='Copenhagen' xmpNote:HasExtendedXMP='ABC'><dc:rights>"
    "<rdf:Alt><rdf:li xml:lang='x-default'>Mine</rdf:li></rdf:Alt></dc:rights>"
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
)


def _ifd(entries: list, start: int, e: str, next_ifd: int = 0) -> bytes:
    """A TIFF directory (any byte order) at ``start``; values that don't fit go after it."""
    head = struct.pack(e + "H", len(entries))
    data = b""
    at = start + 2 + 12 * len(entries) + 4
    for tag, typ, count, raw in sorted(entries):
        if len(raw) <= 4:
            field = raw.ljust(4, b"\x00")
        else:
            field = struct.pack(e + "I", at + len(data))
            data += raw + (b"\x00" if len(raw) % 2 else b"")
        head += struct.pack(e + "HHI", tag, typ, count) + field
    return head + struct.pack(e + "I", next_ifd) + data


def big_endian_exif() -> bytes:
    """Motorola byte order, sub-IFD pointers of type 13, an odd-length XPTitle, an unknown
    field type — the shapes Pillow never writes."""
    e = ">"
    title = "Hi".encode("utf-16-le") + b"\x00"  # odd length
    sizes = {}
    for _ in range(2):  # first pass learns the block sizes, second uses them
        ifd0_at = 8
        ifd0 = [
            (0x010E, 2, 8, b"Big end\x00"),
            (0x0112, 3, 1, struct.pack(e + "H", 3)),
            (0x9C9B, 1, len(title), title),
            (0xC000, 99, 1, b"\xde\xad\xbe\xef"),  # unknown type: kept as it is
            (0x8769, 13, 1, struct.pack(e + "I", sizes.get("exif", 0))),
            (0x8825, 13, 1, struct.pack(e + "I", sizes.get("gps", 0))),
        ]
        b0 = _ifd(ifd0, ifd0_at, e)
        exif_at = ifd0_at + len(b0)
        exif = [
            (0x9003, 2, 20, b"2020:05:06 07:08:09\x00"),
            (0xA005, 13, 1, struct.pack(e + "I", sizes.get("interop", 0))),
        ]
        b1 = _ifd(exif, exif_at, e)
        interop_at = exif_at + len(b1)
        b2 = _ifd([(0x0001, 2, 4, b"R98\x00")], interop_at, e)
        gps_at = interop_at + len(b2)
        gps = [(1, 2, 2, b"N\x00"), (2, 5, 3, struct.pack(e + "6I", 55, 1, 40, 1, 3396, 100))]
        b3 = _ifd(gps, gps_at, e)
        sizes = {"exif": exif_at, "interop": interop_at, "gps": gps_at}
    return b"MM\x00*" + struct.pack(e + "I", 8) + b0 + b1 + b2 + b3


def _app(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def iptc_block() -> bytes:
    def rec(ds: int, text: bytes) -> bytes:
        return b"\x1c\x02" + bytes([ds]) + struct.pack(">H", len(text)) + text

    iim = rec(90, b"Copenhagen") + rec(120, b"Caption kept") + rec(101, b"Denmark")

    def res(rid: int, data: bytes) -> bytes:
        body = b"8BIM" + struct.pack(">H", rid) + b"\x00\x00" + struct.pack(">I", len(data)) + data
        return body + (b"\x00" if len(data) % 2 else b"")

    return b"Photoshop 3.0\x00" + res(0x0404, iim) + res(0x0425, b"\x11" * 16)


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def pictures() -> dict[str, bytes]:
    tmp = ROOT / "build" / "photo-cases"
    tmp.mkdir(parents=True, exist_ok=True)
    out = {"phone.jpg": write_test_photo(tmp / "phone.jpg").read_bytes()}
    b = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 40, 40)).save(b, "JPEG", quality=50)
    out["bare.jpg"] = b.getvalue()
    out["xmp.jpg"] = containers.put(out["phone.jpg"], containers.get_exif(out["phone.jpg"]), XMP)
    b = io.BytesIO()
    Image.new("RGBA", (6, 4), (10, 120, 200, 128)).save(b, "PNG", exif=photo_exif())
    out["exif.png"] = b.getvalue()
    for name, kw in (("lossy.webp", {"quality": 50}), ("lossless.webp", {"lossless": True})):
        b = io.BytesIO()
        Image.new("RGBA", (6, 4), (40, 200, 90, 200)).save(b, "WEBP", **kw)
        out[name] = b.getvalue()
    out["bigendian.jpg"] = containers.put(out["bare.jpg"], big_endian_exif())
    # IPTC location records, a C2PA-style APP11 block, an MPF index and a picture appended after
    # the main one (phones' previews), and extended XMP — the hiding places for a location
    phone = out["phone.jpg"]
    jfif_end = 4 + struct.unpack(">H", phone[4:6])[0]
    extra = _app(0xED, iptc_block()) + _app(0xEB, b"JP\x00\x11c2pa-ish")
    extra += _app(0xE2, b"MPF\x00II*\x00") + _app(
        0xE1, b"http://ns.adobe.com/xmp/extension/\x00ABC"
    )
    out["hiding.jpg"] = phone[:jfif_end] + extra + phone[jfif_end:] + out["bare.jpg"]
    out["xmp2.jpg"] = containers.put(out["bare.jpg"], None, XMP2)
    # a PNG whose eXIf starts with "Exif\0\0", plus ImageMagick's raw-profile copy
    png = out["exif.png"]
    iend = png.rindex(b"IEND") - 4
    raw = _png_chunk(b"tEXt", b"Raw profile type exif\x00\nexif\n 12\n4d4d002a\n")
    no_exif = containers.put(png, None)
    first_idat = no_exif.index(b"IDAT") - 4
    prefixed = _png_chunk(b"eXIf", b"Exif\x00\x00" + photo_exif())
    # an animated WebP (VP8X already there, ANIM / ANMF frames)
    frames = [Image.new("RGBA", (6, 4), c) for c in ((255, 0, 0, 255), (0, 0, 255, 255))]
    b = io.BytesIO()
    frames[0].save(b, "WEBP", save_all=True, append_images=frames[1:], duration=100, lossless=True)
    out["animated.webp"] = b.getvalue()
    # extra 0xFF fill bytes before a marker, and two EXIF blocks (only the first counts)
    bare = out["bare.jpg"]
    second = _app(0xE1, b"Exif\x00\x00" + big_endian_exif())
    out["oddities.jpg"] = bare[:2] + b"\xff\xff" + containers.put(bare, photo_exif())[2:]
    soi_app = containers.put(bare, photo_exif())
    jfif_end2 = 4 + struct.unpack(">H", soi_app[4:6])[0]
    exif_len = 2 + struct.unpack(">H", soi_app[jfif_end2 + 2 : jfif_end2 + 4])[0]
    cut = jfif_end2 + exif_len
    out["two-exif.jpg"] = soi_app[:cut] + second + soi_app[cut:]
    out["rawprofile.png"] = (
        no_exif[:first_idat] + prefixed + no_exif[first_idat:iend] + raw + png[iend:]
    )
    return out


EDITS = [
    ("set every field", {"values": {"title": "Harbour, æøå", "comment": "Shot from the pier",
     "author": "Grace Hopper", "copyright": "CC0", "tags": "sea, boats;dusk",
     "taken": "2025-01-02 03:04"}, "orientation": 1}),
    ("clear some, remove location", {"values": {"author": "", "title": ""},
     "remove_location": True}),
    ("only rotate", {"orientation": 8}),
    ("emoji and tidy tags", {"values": {"title": "Sunset 🌅", "tags": " a,,b ;c "}}),
    ("remove location only", {"remove_location": True}),
    ("remove everything", {"remove_all": True}),
    ("remove everything but set a title", {"values": {"title": "Kept"}, "remove_all": True}),
]  # fmt: skip


def main() -> None:
    cases = []
    for name, data in pictures().items():
        cases.append(
            {
                "name": f"{name}: read",
                "input": data.hex(),
                "details": asdict(photo.read_details(data)),
            }
        )
        for label, edit in EDITS:
            pe = photo.PhotoEdit(**edit)
            out = photo.apply(data, pe)
            cases.append(
                {
                    "name": f"{name}: {label}",
                    "input": data.hex(),
                    "edit": edit,
                    "output": out.hex(),
                    "details": asdict(photo.read_details(out)),
                }
            )
    path = ROOT / "tests" / "fixtures" / "photo_edit_cases.json"
    path.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases → {path}")


if __name__ == "__main__":
    main()
