"""Finding and replacing the EXIF block and the XMP packet inside JPEG, PNG and WebP files,
leaving the picture data byte-for-byte as it was. Pure Python, line-by-line ported to
``web/src/properties/containers.ts`` (shared cases in ``tests/fixtures/photo_edit_cases.json``).

``put`` takes the new EXIF block (TIFF bytes, or None = remove) and the new XMP text (None =
remove, KEEP = leave as it is). With ``strip`` it also drops every other place hidden details
live (JPEG comments and Photoshop/IPTC blocks, PNG text chunks and time stamp), but never the
colour profile."""

from __future__ import annotations

import struct
import zlib

EXIF_ID = b"Exif\x00\x00"
XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_ID = b"http://ns.adobe.com/xmp/extension/\x00"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_XMP_KEY = b"XML:com.adobe.xmp"
KEEP = object()  # put(): leave the XMP packet alone

MAX_JPEG_SEGMENT = 65533  # payload bytes after the 2-byte length


class ContainerError(ValueError):
    """The file isn't a JPEG / PNG / WebP this code can change safely."""


def kind_of(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == PNG_SIG:
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# ---------------------------------------------------------------------------- JPEG
def _jpeg_segments(data: bytes) -> tuple[list[tuple[int, bytes | None]], bytes]:
    """[(marker, payload)] up to the start of the picture data (payload None for a marker
    without one), and the rest (from SOS on)."""
    if data[:2] != b"\xff\xd8":
        raise ContainerError("not a JPEG")
    segs: list[tuple[int, bytes | None]] = []
    p = 2
    while p < len(data):
        if data[p] != 0xFF:
            raise ContainerError("damaged JPEG")
        while p < len(data) and data[p] == 0xFF:  # fill bytes
            p += 1
        if p >= len(data):
            break
        marker = data[p]
        p += 1
        if marker == 0xDA or marker == 0xD9:  # start of scan / end: the rest stays as it is
            return segs, data[p - 2 :]
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            segs.append((marker, None))
            continue
        if p + 2 > len(data):
            raise ContainerError("damaged JPEG")
        length = struct.unpack(">H", data[p : p + 2])[0]
        if length < 2 or p + length > len(data):
            raise ContainerError("damaged JPEG")
        segs.append((marker, data[p + 2 : p + length]))
        p += length
    raise ContainerError("damaged JPEG")


def _jpeg_segment(marker: int, payload: bytes) -> bytes:
    if len(payload) > MAX_JPEG_SEGMENT:
        raise ContainerError("too many details to fit in a JPEG")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg_get(data: bytes, ident: bytes) -> bytes | None:
    segs, _ = _jpeg_segments(data)
    for marker, payload in segs:
        if marker == 0xE1 and payload is not None and payload.startswith(ident):
            return payload[len(ident) :]
    return None


def _jpeg_put(data: bytes, exif: bytes | None, xmp: object, strip: bool) -> bytes:
    segs, rest = _jpeg_segments(data)
    out = bytearray(b"\xff\xd8")
    placed_exif = placed_xmp = False
    new_exif = _jpeg_segment(0xE1, EXIF_ID + exif) if exif is not None else b""
    new_xmp = b""
    if xmp is not KEEP and xmp is not None:
        new_xmp = _jpeg_segment(0xE1, XMP_ID + str(xmp).encode("utf-8"))
    # where new blocks go when the file had none: after JFIF / JFXX (APP0), else first
    lead = 0
    while lead < len(segs) and segs[lead][0] == 0xE0:
        lead += 1
    for i, (marker, payload) in enumerate(segs):
        if i == lead and not placed_exif:
            out += new_exif
            placed_exif = True
        if payload is None:
            out += bytes([0xFF, marker])
            continue
        is_exif = marker == 0xE1 and payload.startswith(EXIF_ID)
        is_xmp = marker == 0xE1 and payload.startswith(XMP_ID)
        is_xmp_ext = marker == 0xE1 and payload.startswith(XMP_EXT_ID)
        if is_exif:
            continue  # replaced (placed at the first APPn position after APP0)
        if is_xmp:
            if xmp is KEEP and not strip:
                out += _jpeg_segment(marker, payload)
            elif not placed_xmp:
                out += new_xmp
            placed_xmp = True
            continue
        if strip and (is_xmp_ext or marker in (0xED, 0xFE, 0xEC)):  # Photoshop/IPTC, COM, Ducky
            continue
        out += _jpeg_segment(marker, payload)
    if not placed_exif:
        out += new_exif
    if not placed_xmp:
        out += new_xmp
    return bytes(out) + rest


# ---------------------------------------------------------------------------- PNG
def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:8] != PNG_SIG:
        raise ContainerError("not a PNG")
    chunks = []
    p = 8
    while p + 12 <= len(data):
        length = struct.unpack(">I", data[p : p + 4])[0]
        kind = data[p + 4 : p + 8]
        if p + 12 + length > len(data):
            raise ContainerError("damaged PNG")
        chunks.append((kind, data[p + 8 : p + 8 + length]))
        p += 12 + length
        if kind == b"IEND":
            return chunks
    raise ContainerError("damaged PNG")


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def _png_xmp_text(body: bytes) -> str | None:
    """The XMP of an iTXt chunk with Adobe's keyword (uncompressed or zlib)."""
    if not body.startswith(PNG_XMP_KEY + b"\x00") or len(body) < len(PNG_XMP_KEY) + 3:
        return None
    rest = body[len(PNG_XMP_KEY) + 1 :]
    compressed = rest[0] == 1
    rest = rest[2:]
    lang_end = rest.find(b"\x00")
    trans_end = rest.find(b"\x00", lang_end + 1)
    if lang_end < 0 or trans_end < 0:
        return None
    text = rest[trans_end + 1 :]
    if compressed:
        try:
            text = zlib.decompress(text)
        except zlib.error:
            return None
    return text.decode("utf-8", "replace")


def _png_put(data: bytes, exif: bytes | None, xmp: object, strip: bool) -> bytes:
    chunks = _png_chunks(data)
    out = bytearray(PNG_SIG)
    placed = False
    for kind, body in chunks:
        if kind == b"eXIf":
            continue
        is_xmp = kind == b"iTXt" and body.startswith(PNG_XMP_KEY + b"\x00")
        if is_xmp and (xmp is not KEEP or strip):
            continue
        if strip and kind in (b"tEXt", b"zTXt", b"iTXt", b"tIME"):
            continue
        if kind in (b"IDAT", b"IEND") and not placed:  # before the picture data
            if exif is not None:
                out += _png_chunk(b"eXIf", exif)
            if xmp is not KEEP and xmp is not None:
                body_xmp = PNG_XMP_KEY + b"\x00\x00\x00\x00\x00" + str(xmp).encode("utf-8")
                out += _png_chunk(b"iTXt", body_xmp)
            placed = True
        out += _png_chunk(kind, body)
    return bytes(out)


# ---------------------------------------------------------------------------- WebP
def _webp_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ContainerError("not a WebP")
    end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    chunks = []
    p = 12
    while p + 8 <= end:
        kind = data[p : p + 4]
        size = struct.unpack("<I", data[p + 4 : p + 8])[0]
        if p + 8 + size > end:
            raise ContainerError("damaged WebP")
        chunks.append((kind, data[p + 8 : p + 8 + size]))
        p += 8 + size + (size & 1)
    return chunks


def _webp_canvas(chunks: list[tuple[bytes, bytes]]) -> tuple[int, int, bool]:
    """(width, height, has alpha) of a simple WebP, read from its picture data."""
    for kind, body in chunks:
        if kind == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            w = struct.unpack("<H", body[6:8])[0] & 0x3FFF
            h = struct.unpack("<H", body[8:10])[0] & 0x3FFF
            return w, h, any(k == b"ALPH" for k, _ in chunks)
        if kind == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            bits = struct.unpack("<I", body[1:5])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool((bits >> 28) & 1)
    raise ContainerError("damaged WebP")


def _webp_chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def _webp_put(data: bytes, exif: bytes | None, xmp: object, strip: bool) -> bytes:
    chunks = _webp_chunks(data)
    keep_xmp = xmp is KEEP and not strip
    old_xmp = next((b for k, b in chunks if k == b"XMP "), None) if keep_xmp else None
    new_xmp = old_xmp if keep_xmp else (str(xmp).encode("utf-8") if xmp is not None else None)
    body = [(k, b) for k, b in chunks if k not in (b"EXIF", b"XMP ")]
    vp8x = next((b for k, b in body if k == b"VP8X"), None)
    if vp8x is None and (exif is not None or new_xmp is not None):
        w, h, alpha = _webp_canvas(body)
        vp8x = bytes([0x10 if alpha else 0, 0, 0, 0]) + (w - 1).to_bytes(3, "little")
        vp8x += (h - 1).to_bytes(3, "little")
        body.insert(0, (b"VP8X", vp8x))
    out = bytearray()
    for k, b in body:
        if k == b"VP8X":
            flags = b[0] & ~0x0C
            flags |= 0x08 if exif is not None else 0
            flags |= 0x04 if new_xmp is not None else 0
            b = bytes([flags]) + b[1:]
        out += _webp_chunk(k, b)
    if exif is not None:
        out += _webp_chunk(b"EXIF", exif)
    if new_xmp is not None:
        out += _webp_chunk(b"XMP ", new_xmp)
    return b"RIFF" + struct.pack("<I", 4 + len(out)) + b"WEBP" + bytes(out)


# ---------------------------------------------------------------------------- the API
def get_exif(data: bytes) -> bytes | None:
    kind = kind_of(data)
    if kind == "jpeg":
        return _jpeg_get(data, EXIF_ID)
    if kind == "png":
        return next((b for k, b in _png_chunks(data) if k == b"eXIf"), None)
    if kind == "webp":
        raw = next((b for k, b in _webp_chunks(data) if k == b"EXIF"), None)
        return raw[6:] if raw is not None and raw.startswith(EXIF_ID) else raw
    return None


def get_xmp(data: bytes) -> str | None:
    kind = kind_of(data)
    if kind == "jpeg":
        raw = _jpeg_get(data, XMP_ID)
        return raw.decode("utf-8", "replace") if raw is not None else None
    if kind == "png":
        for k, b in _png_chunks(data):
            if k == b"iTXt":
                text = _png_xmp_text(b)
                if text is not None:
                    return text
        return None
    if kind == "webp":
        raw = next((b for k, b in _webp_chunks(data) if k == b"XMP "), None)
        return raw.decode("utf-8", "replace") if raw is not None else None
    return None


def put(data: bytes, exif: bytes | None, xmp: object = KEEP, strip: bool = False) -> bytes:
    kind = kind_of(data)
    if kind == "jpeg":
        return _jpeg_put(data, exif, xmp, strip)
    if kind == "png":
        return _png_put(data, exif, xmp, strip)
    if kind == "webp":
        return _webp_put(data, exif, xmp, strip)
    raise ContainerError("only JPEG, PNG and WebP pictures can be changed")
