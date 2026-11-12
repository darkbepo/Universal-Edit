"""Tiny, valid sample files written from scratch (used by tests and ``--self-test``).

Camera RAW and PSD files can't be produced by Pillow, and real camera samples come with
restrictive licences, so we write minimal but genuine files ourselves.
"""

from __future__ import annotations

import struct
from pathlib import Path


def write_test_dng(path: Path, width: int = 64, height: int = 48) -> Path:
    """A linear (demosaiced) DNG, 16-bit RGB: left half red, right half blue."""
    pixels = bytearray()
    for _y in range(height):
        for x in range(width):
            r, b = (60000, 0) if x < width // 2 else (0, 60000)
            pixels += struct.pack("<3H", r, 0, b)

    # (tag, type, values); types: 1 BYTE, 2 ASCII, 3 SHORT, 4 LONG, 10 SRATIONAL
    identity = [1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1]
    entries = [
        (254, 4, [0]),  # NewSubfileType: main image
        (256, 4, [width]),
        (257, 4, [height]),
        (258, 3, [16, 16, 16]),  # BitsPerSample
        (259, 3, [1]),  # no compression
        (262, 3, [34892]),  # PhotometricInterpretation: LinearRaw
        (273, 4, [0]),  # StripOffsets (patched below)
        (277, 3, [3]),  # SamplesPerPixel
        (278, 4, [height]),  # RowsPerStrip
        (279, 4, [len(pixels)]),  # StripByteCounts
        (284, 3, [1]),  # PlanarConfiguration: chunky
        (50706, 1, [1, 4, 0, 0]),  # DNGVersion
        (50707, 1, [1, 1, 0, 0]),  # DNGBackwardVersion
        (50708, 2, b"Test Camera\0"),  # UniqueCameraModel
        (50717, 3, [65535, 65535, 65535]),  # WhiteLevel
        (50721, 10, identity),  # ColorMatrix1
        (50778, 3, [21]),  # CalibrationIlluminant1: D65
    ]
    fmt = {1: "B", 2: "s", 3: "H", 4: "I", 10: "i"}
    size = {1: 1, 2: 1, 3: 2, 4: 4, 10: 8}
    ifd_offset = 8
    data_offset = ifd_offset + 2 + len(entries) * 12 + 4
    blobs = b""
    ifd = struct.pack("<H", len(entries))
    strip_entry_index = next(i for i, e in enumerate(entries) if e[0] == 273)
    packed_values = []
    for tag, typ, values in entries:
        if typ == 2:
            raw, count = values, len(values)
        else:
            count = len(values) // 2 if typ == 10 else len(values)
            raw = struct.pack(f"<{len(values)}{fmt[typ]}", *values)
        packed_values.append((tag, typ, count, raw))
    # Lay out out-of-line values, then pixels.
    offsets = []
    for _tag, typ, count, raw in packed_values:
        if count * size[typ] <= 4:
            offsets.append(None)
        else:
            offsets.append(data_offset + len(blobs))
            blobs += raw + (b"\0" if len(raw) % 2 else b"")
    pixel_offset = data_offset + len(blobs)
    for i, (tag, typ, count, raw) in enumerate(packed_values):
        if i == strip_entry_index:
            raw = struct.pack("<I", pixel_offset)
        value = raw.ljust(4, b"\0") if offsets[i] is None else struct.pack("<I", offsets[i])
        ifd += struct.pack("<HHI", tag, typ, count) + value
    ifd += struct.pack("<I", 0)  # no next IFD
    path.write_bytes(b"II*\0" + struct.pack("<I", ifd_offset) + ifd + blobs + bytes(pixels))
    return path


def write_test_psd(path: Path, width: int = 40, height: int = 30) -> Path:
    """A flat 8-bit RGB Photoshop file (no layers): solid green."""
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, height, width, 8, 3)
    sections = struct.pack(">III", 0, 0, 0)  # colour mode data, image resources, layers
    plane = width * height
    image = struct.pack(">H", 0) + bytes(plane) + bytes([200]) * plane + bytes(plane)
    path.write_bytes(header + sections + image)
    return path


SCAN_TEXT = ("INVOICE 2026", "Total due 42.50 EUR")


def write_test_scan(path: Path, lines: tuple[str, ...] = SCAN_TEXT) -> Path:
    """A white "scanned page" PNG with large black text, for OCR tests."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 220 + 120 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=72)
    for i, line in enumerate(lines):
        draw.text((80, 100 + 120 * i), line, fill="black", font=font)
    img.save(path, dpi=(300, 300))
    return path


# Tiny RAR4 / RAR5 archives from libarchive's test suite (BSD-2-Clause; see tests/fixtures/README).
# Kept here so the built app's --self-test can check that RAR reading works on that computer.
RAR4_B64 = (
    "UmFyIRoHAM+QcwAADQAAAAAAAACEUnQgkDIAFAAAABQAAAADQqLIvrd22j4UMAgApIEAAHRlc3QudHh0gAi3dto+"
    "t3baPnRlc3QgdGV4dCBkb2N1bWVudA0KnS90IJAyAAgAAAAIAAAAA3tEybbRTNg+FDAIAP+hAAB0ZXN0bGlua8AI"
    "0UzYPlBf2j50ZXN0LnR4dM3gdCCQOgAUAAAAFAAAAANCosi+Y3faPhQwEACkgQAAdGVzdGRpclx0ZXN0LnR4dMDM"
    "Y3faPmN32j50ZXN0IHRleHQgZG9jdW1lbnQNCqHIdOCQMQAAAAAAAAAAAAMAAAAAY3faPhQwBwDtQQAAdGVzdGRp"
    "csDMY3faPmR32j7m53TgkDYAAAAAAAAAAAADAAAAAJ2r1T4UMAwA7UEAAHRlc3RlbXB0eWRpcoDMnavVPsVd2j7E"
    "PXsAQAcA"
)
RAR5_B64 = (
    "UmFyIRoHAQDz4YLrCwEFBwAGAQGAgIAAicZf2iYCAwvpAgSwCaSDAs1wynyABQEIdGVzdC5iaW4KAxOLV6xb+Bit"
    "Hsr0ZQEnZWBUH1V2Xb+UknHJz2WWWQxw2WWTGMWSE4ZCSSThkJCSSSQk45JJIQhJJCEJJJCSSEISEs21b3t9Mw6A"
    "fR0Drj+eup/3Qf732vfdea86X+gAQAEAyCCfn49vX08vHv7uzr6uno5+Xk4+Lh4N/d3NrZ19bV1NPS0M/NzMrJyM"
    "fGxcTDwb+9vLu5uLe2tbSzsrGvrq2rqqmnpaSjoqGgn52cm5mXlpSTSyUjIR6FAfjoyKiYc6cNmoaEgoB+fHt6eH"
    "Z0c3Jxb21raWdlY2FfXVtZV1VTITf1/I/QJT6JTn+ESXRYowZ5YXajgppimUF+ZC0XadRjLDOMCoR/+glMZpCl9l"
    "UKxWVguLAZFoNy4HdeEGwA8xBIyCeZhWNAumoZbYN5uHg4Bgcg+uYROgpnYYDwNt6Hu9h1fQjfxYQI0YMecKHk0J"
    "g2L44OY6HCIEqeGFFjtjQ/nxUIBsIQ0IhNIxnJAvJQAdd1ZRAwUEAA=="
)


def write_test_rar(path: Path, version: int = 5) -> Path:
    import base64

    path.write_bytes(base64.b64decode(RAR5_B64 if version == 5 else RAR4_B64))
    return path


def write_test_font(path: Path) -> Path:
    """A tiny TrueType font: .notdef, space, A, B, f, i and an "fi" ligature (GSUB liga)."""
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def box(x0: int, y0: int, x1: int, y1: int, curve: bool = False):
        pen = TTGlyphPen(None)
        pen.moveTo((x0, y0))
        if curve:  # a quadratic bump, so outline conversion has curves to deal with
            pen.qCurveTo(((x0 + x1) // 2, y1 + 200), (x1, y0))
        else:
            pen.lineTo((x0, y1))
            pen.lineTo((x1, y1))
            pen.lineTo((x1, y0))
        pen.closePath()
        return pen.glyph()

    order = [".notdef", "space", "A", "B", "f", "i", "f_i"]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x20: "space", 0x41: "A", 0x42: "B", 0x66: "f", 0x69: "i"})
    glyphs = {
        ".notdef": box(50, 0, 450, 700),
        "space": TTGlyphPen(None).glyph(),
        "A": box(50, 0, 550, 700, curve=True),
        "B": box(60, 0, 540, 700),
        "f": box(50, 0, 300, 750),
        "i": box(50, 0, 200, 500),
        "f_i": box(50, 0, 500, 750, curve=True),
    }
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (600, getattr(glyphs[n], "xMin", 0)) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "UMC Test", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    addOpenTypeFeaturesFromString(fb.font, "feature liga { sub f i by f_i; } liga;")
    fb.save(path)
    return path


# A 64 × 48 blue JPEG at 300 dpi (JFIF), made once with Pillow; EXIF is added by hand below.
_PHOTO_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010101012c012c0000ffdb0043000d090a0b0a080d0b0a0b0e0e0d0f1320151312"
    "1213271c1e17202e2931302e292d2c333a4a3e333646372c2d405741464c4e525352323e5a615a50604a5152"
    "4fffdb0043010e0e0e131113261515264f352d354f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f"
    "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4fffc000110800300040030122000211010311"
    "01ffc4001500010100000000000000000000000000000004ffc4001410010000000000000000000000000000"
    "0000ffc4001501010100000000000000000000000000000005ffc40014110100000000000000000000000000"
    "000000ffda000c03010002110311003f008405e440000000000000000000000000000000007fffd9"
)

# EXIF field types: 1 BYTE, 2 ASCII, 3 SHORT, 4 LONG, 5 RATIONAL
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}


def _ifd(entries: list[tuple[int, int, object]], start: int, next_ifd: int = 0) -> bytes:
    """One little-endian TIFF IFD at byte ``start`` (values that don't fit go after it)."""
    entries = sorted(entries, key=lambda e: e[0])
    head = struct.pack("<H", len(entries))
    data = b""
    data_at = start + 2 + 12 * len(entries) + 4
    for tag, kind, value in entries:
        if kind == 2:
            raw = str(value).encode("ascii") + b"\0"
            count = len(raw)
        elif kind == 5:
            pairs = value if isinstance(value, list) else [value]
            raw = b"".join(struct.pack("<II", n, d) for n, d in pairs)
            count = len(pairs)
        else:
            fmt = {1: "<B", 3: "<H", 4: "<I"}[kind]
            raw = struct.pack(fmt, value)
            count = 1
        if len(raw) <= 4:
            field = raw.ljust(4, b"\0")
        else:
            field = struct.pack("<I", data_at + len(data))
            data += raw + (b"\0" if len(raw) % 2 else b"")
        head += struct.pack("<HHI", tag, kind, count) + field
    return head + struct.pack("<I", next_ifd) + data


def photo_exif() -> bytes:
    """The EXIF a phone writes (camera, settings, date taken, location) as TIFF bytes."""
    gps = [
        (1, 2, "N"),
        (2, 5, [(55, 1), (40, 1), (3396, 100)]),
        (3, 2, "E"),
        (4, 5, [(12, 1), (34, 1), (1188, 100)]),
        (5, 1, 0),
        (6, 5, (12, 1)),
    ]
    exif = [
        (0x829A, 5, (1, 120)),
        (0x829D, 5, (18, 10)),
        (0x8827, 3, 200),
        (0x9003, 2, "2026:11:12 14:03:27"),
        (0x9209, 3, 16),
        (0x920A, 5, (50, 1)),
        (0xA434, 2, "RF 50mm F1.8 STM"),
    ]
    base = [
        (0x010E, 2, "Harbour at dusk"),
        (0x010F, 2, "Canon"),
        (0x0110, 2, "Canon EOS R6"),
        (0x0112, 3, 6),
        (0x013B, 2, "Ada Lovelace"),
        (0x8298, 2, "CC BY 4.0"),
    ]
    # sizes first (pointer values don't change a block's length), then the real offsets
    ifd0_len = len(_ifd(base + [(0x8769, 4, 0), (0x8825, 4, 0)], 8))
    exif_at = 8 + ifd0_len
    exif_block = _ifd(exif, exif_at)
    gps_at = exif_at + len(exif_block)
    ifd0 = _ifd(base + [(0x8769, 4, exif_at), (0x8825, 4, gps_at)], 8)
    return b"II*\0" + struct.pack("<I", 8) + ifd0 + exif_block + _ifd(gps, gps_at)


def write_test_photo(path: Path) -> Path:
    """A small JPEG with a phone's EXIF (camera, settings, date taken, location) — written
    without Pillow, so the web e2e setup can call it from any Python (Properties tests)."""
    tiff = b"Exif\0\0" + photo_exif()
    app1 = b"\xff\xe1" + struct.pack(">H", len(tiff) + 2) + tiff
    jfif_end = 2 + 2 + struct.unpack(">H", _PHOTO_JPEG[4:6])[0]  # SOI + APP0
    path.write_bytes(_PHOTO_JPEG[:jfif_end] + app1 + _PHOTO_JPEG[jfif_end:])
    return path
