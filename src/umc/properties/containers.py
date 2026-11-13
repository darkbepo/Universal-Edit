"""Finding and replacing the EXIF block and the XMP packet inside JPEG, PNG and WebP files,
leaving the picture data byte-for-byte as it was. Pure Python, line-by-line ported to
``web/src/properties/containers.ts`` (shared cases in ``tests/fixtures/photo_edit_cases.json``).

``put`` takes the new EXIF block (TIFF bytes, or None = remove) and the new XMP text (None =
remove, KEEP = leave as it is). With ``strip`` it also drops every other place hidden details
live: in a JPEG every APPn block but JFIF, the colour profile and Adobe's colour flag, comments,
and the extra pictures phones append after the main one (previews, depth maps, each with its own
EXIF); in a PNG the text chunks and time stamp. With ``drop_location`` (Remove the location) it
removes the other copies of a location: extended XMP, the IPTC city / country records, the
appended pictures, and PNG "Raw profile" text chunks (ImageMagick's copies of EXIF / XMP)."""

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
PHOTOSHOP_ID = b"Photoshop 3.0\x00"
ICC_ID = b"ICC_PROFILE\x00"
ADOBE_ID = b"Adobe"
MPF_ID = b"MPF\x00"
# IPTC records that say where: sublocation, city, province, country code / name, location code
IPTC_PLACES = {(2, 26), (2, 27), (2, 90), (2, 92), (2, 95), (2, 100), (2, 101)}


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


def _jpeg_end(rest: bytes) -> int | None:
    """Where the main picture ends (after its EOI marker) in ``rest`` (from the first SOS on),
    or None when that can't be found safely (then nothing is cut)."""
    p, n = 0, len(rest)
    while p + 1 < n:
        if rest[p] != 0xFF:
            return None
        m = rest[p + 1]
        if m == 0xFF:  # fill byte
            p += 1
            continue
        if m == 0xD9:
            return p + 2
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            p += 2
        else:
            if p + 4 > n:
                return None
            p += 2 + struct.unpack(">H", rest[p + 2 : p + 4])[0]
        if m == 0xDA or 0xD0 <= m <= 0xD7:  # entropy-coded data follows
            while p < n:
                if rest[p] == 0xFF and p + 1 < n:
                    nxt = rest[p + 1]
                    if nxt != 0 and not 0xD0 <= nxt <= 0xD7:
                        break
                    p += 2
                else:
                    p += 1
    return None


def _iptc_without_places(iim: bytes) -> bytes | None:
    """IPTC IIM data without the location records (None if it can't be read)."""
    out = bytearray()
    p = 0
    while p < len(iim):
        if iim[p] != 0x1C or p + 5 > len(iim):
            return None
        rec, ds = iim[p + 1], iim[p + 2]
        size = struct.unpack(">H", iim[p + 3 : p + 5])[0]
        head = 5
        if size & 0x8000:  # extended length
            k = size & 0x7FFF
            if k > 4 or p + 5 + k > len(iim):
                return None
            size = int.from_bytes(iim[p + 5 : p + 5 + k], "big")
            head += k
        end = p + head + size
        if end > len(iim):
            return None
        if (rec, ds) not in IPTC_PLACES:
            out += iim[p:end]
        p = end
    return bytes(out)


def _photoshop_without_places(payload: bytes) -> bytes | None:
    """An APP13 Photoshop block without the IPTC location records (and the now wrong IPTC
    checksum resource 0x0425). None if it can't be read (the caller then drops it)."""
    out = bytearray(PHOTOSHOP_ID)
    p = len(PHOTOSHOP_ID)
    while p < len(payload):
        if payload[p : p + 4] != b"8BIM" or p + 7 > len(payload):
            return None
        rid = struct.unpack(">H", payload[p + 4 : p + 6])[0]
        name_len = payload[p + 6]
        name_end = p + 7 + name_len + ((name_len + 1) % 2)  # padded to even
        if name_end + 4 > len(payload):
            return None
        size = struct.unpack(">I", payload[name_end : name_end + 4])[0]
        data_end = name_end + 4 + size
        if data_end > len(payload):
            return None
        data = payload[name_end + 4 : data_end]
        nxt = data_end + (size % 2)
        if rid == 0x0425:
            p = nxt
            continue
        if rid == 0x0404:
            data = _iptc_without_places(data)
            if data is None:
                return None
        out += payload[p:name_end] + struct.pack(">I", len(data)) + data
        if len(data) % 2:
            out += b"\x00"
        p = nxt
    return bytes(out)


def _jpeg_put(
    data: bytes, exif: bytes | None, xmp: object, strip: bool, drop_location: bool
) -> bytes:
    segs, rest = _jpeg_segments(data)
    if strip or drop_location:  # the appended pictures (previews, depth maps) go too
        end = _jpeg_end(rest)
        if end is not None:
            rest = rest[:end]
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
        if strip:
            keep = marker == 0xE0 or (marker == 0xE2 and payload.startswith(ICC_ID))
            keep = keep or (marker == 0xEE and payload.startswith(ADOBE_ID))
            if (0xE1 <= marker <= 0xEF or marker == 0xFE) and not keep:
                continue  # every other APPn block and comments
        if drop_location:
            if is_xmp_ext or (marker == 0xE2 and payload.startswith(MPF_ID)):
                continue  # extended XMP; the index of the appended pictures (now gone)
            if marker == 0xED and payload.startswith(PHOTOSHOP_ID):
                payload = _photoshop_without_places(payload)
                if payload is None:
                    continue  # unreadable: drop it rather than keep a location
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


def _png_put(
    data: bytes, exif: bytes | None, xmp: object, strip: bool, drop_location: bool
) -> bytes:
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
        if (
            drop_location
            and kind in (b"tEXt", b"zTXt", b"iTXt")
            and body.startswith(b"Raw profile type")
        ):
            continue  # ImageMagick's copies of EXIF / XMP / IPTC
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


def _webp_put(
    data: bytes, exif: bytes | None, xmp: object, strip: bool, drop_location: bool
) -> bytes:
    del drop_location  # a WebP keeps its details only in the EXIF and XMP chunks
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
        raw = next((b for k, b in _png_chunks(data) if k == b"eXIf"), None)
        return raw[6:] if raw is not None and raw.startswith(EXIF_ID) else raw
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


def put(
    data: bytes,
    exif: bytes | None,
    xmp: object = KEEP,
    strip: bool = False,
    drop_location: bool = False,
) -> bytes:
    kind = kind_of(data)
    if kind == "jpeg":
        return _jpeg_put(data, exif, xmp, strip, drop_location)
    if kind == "png":
        return _png_put(data, exif, xmp, strip, drop_location)
    if kind == "webp":
        return _webp_put(data, exif, xmp, strip, drop_location)
    raise ContainerError("only JPEG, PNG and WebP pictures can be changed")
