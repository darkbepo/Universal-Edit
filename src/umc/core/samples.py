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
