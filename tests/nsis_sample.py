"""Builds small NSIS 3 Unicode installers (solid LZMA) for the tests — no binaries in git.

An entry is ``("dir", path)`` (SetOutPath), ``("set", var, text)`` (StrCpy) or
``("file", name, data)``. Text may hold ``$INSTDIR``, ``$OUTDIR``, ``$PLUGINSDIR`` and ``$31``.
"""

from __future__ import annotations

import lzma
import re
import struct
from pathlib import Path

VARS = {"$INSTDIR": 21, "$OUTDIR": 22, "$PLUGINSDIR": 26, "$31": 31}
FILETIME_2020 = 132223104000000000  # 2020-01-01 00:00:00 UTC


def _encode(text: str) -> bytes:
    out = bytearray()
    for part in re.split(r"(\$INSTDIR|\$OUTDIR|\$PLUGINSDIR|\$31)", text):
        if part in VARS:
            n = VARS[part]
            out += struct.pack("<HH", 3, 0x8080 | ((n & 0x3F80) << 1) | (n & 0x7F))
        else:
            out += part.encode("utf-16-le")
    return bytes(out) + b"\0\0"


def build(entries: list[tuple], path: Path, *, unicode: bool = True) -> Path:
    strings = bytearray(b"\0\0" if unicode else b"\0")
    data = bytearray()
    offsets: dict[bytes, int] = {}

    def string(text: str) -> int:
        at = len(strings) // 2
        strings.extend(_encode(text))
        return at

    raw = []
    for entry in entries:
        if entry[0] == "dir":
            raw.append((11, string(entry[1]), 1, 0, 0, 0, 0))
        elif entry[0] == "set":
            raw.append((25, entry[1], string(entry[2]), 0, 0, 0, 0))
        else:
            _kind, name, blob = entry
            if blob not in offsets:  # NSIS stores identical files once
                offsets[blob] = len(data)
                data += struct.pack("<I", len(blob)) + blob
            low, high = FILETIME_2020 & 0xFFFFFFFF, FILETIME_2020 >> 32
            raw.append((20, 0, string(name), offsets[blob], low, high, 0))
    ent_off = 4 + 64
    str_off = ent_off + 28 * len(raw)
    lang_off = str_off + len(strings)
    blocks = [(0, 0), (0, 0), (ent_off, len(raw)), (str_off, 0), (lang_off, 0)] + [(0, 0)] * 3
    header = struct.pack("<I", 0) + b"".join(struct.pack("<II", *b) for b in blocks)
    header += b"".join(struct.pack("<7I", *e) for e in raw) + strings + b"\0" * 4
    stream = struct.pack("<I", len(header)) + header + bytes(data)
    lc, lp, pb, dict_size = 3, 0, 2, 1 << 16
    packed = lzma.compress(
        stream,
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}],
    )
    props = bytes([(pb * 5 + lp) * 9 + lc]) + struct.pack("<I", dict_size)
    first = struct.pack("<II", 0, 0xDEADBEEF) + b"NullsoftInst"
    first += struct.pack("<II", len(header), 5 + len(packed))
    stub = b"MZ" + b"\0" * 1022  # the installer program comes first; NSIS data is 512-aligned
    path.write_bytes(stub + first + props + packed)
    return path
