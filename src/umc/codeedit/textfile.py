"""Reading and writing text files byte for byte (``web/src/codeedit/textfile.ts`` is a port).

The text is handed to the editor with "\\n" line endings; ``encode`` puts the file's own
encoding, BOM and line endings back. Saving untouched text returns the bytes as read, so a file
with mixed line endings (or anything else odd) is never changed by opening and saving it."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Literal

Encoding = Literal["utf-8", "utf-16-le", "utf-16-be", "windows-1252"]
Newline = Literal["\r\n", "\n", "\r"]

SNIFF_BYTES = 8192
_BOMS: tuple[tuple[bytes, Encoding], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_TEXT_CONTROLS = {0x09, 0x0A, 0x0C, 0x0D, 0x1B}  # tab, LF, form feed, CR, ESC (ANSI logs)


def _cp1252_tables() -> tuple[dict[int, str], dict[int, int]]:
    """Windows-1252 as browsers read it (WHATWG): the five bytes cp1252 leaves undefined
    (0x81 0x8D 0x8F 0x90 0x9D) stand for U+0081 … so every byte decodes and encodes back."""
    to_text: dict[int, str] = {}
    for b in range(0x80, 0xA0):
        try:
            to_text[b] = bytes([b]).decode("cp1252")
        except UnicodeDecodeError:
            to_text[b] = chr(b)
    return to_text, {ord(ch): b for b, ch in to_text.items()}


_CP1252_DECODE, _CP1252_ENCODE = _cp1252_tables()
# U+0080…U+009F that Windows-1252 has no byte for (their bytes mean €, ‚, … there)
_CP1252_MISSING = frozenset(chr(b) for b, ch in _CP1252_DECODE.items() if ch != chr(b))


@dataclass(frozen=True)
class TextFile:
    text: str  # line endings normalised to "\n"
    encoding: Encoding
    bom: bool
    newline: Newline  # the most common one ("\n" when there is none; ties → "\r\n")
    mixed: bool  # more than one kind in the file
    raw: bytes  # the bytes as read


def _bom_of(data: bytes) -> tuple[bytes, Encoding] | None:
    return next(((bom, enc) for bom, enc in _BOMS if data.startswith(bom)), None)


def is_binary(head: bytes) -> bool:
    """A NUL byte in the first 8 KB (UTF-16 with a BOM is text)."""
    bom = _bom_of(head)
    if bom and bom[1] != "utf-8":
        return False
    return b"\x00" in head[:SNIFF_BYTES]


def looks_like_text(head: bytes) -> bool:
    """Whether the first bytes of a file of unknown kind are text worth opening."""
    head = head[:SNIFF_BYTES]
    if is_binary(head):
        return False
    if _bom_of(head):
        return True
    try:  # final=False: a character cut off at the end of the sample is fine
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        return True
    except UnicodeDecodeError:
        pass
    controls = sum(1 for b in head if (b < 0x20 and b not in _TEXT_CONTROLS) or b == 0x7F)
    return controls <= len(head) * 0.05


def _decode_text(body: bytes, encoding: Encoding) -> str:
    if encoding == "windows-1252":
        return body.decode("latin-1").translate(_CP1252_DECODE)
    return body.decode(encoding)


def _newlines(text: str) -> tuple[Newline, bool]:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    counts: list[tuple[int, Newline]] = [(crlf, "\r\n"), (lf, "\n"), (cr, "\r")]
    kinds = sum(1 for n, _ in counts if n)
    if not kinds:
        return "\n", False
    best = max(counts, key=lambda c: c[0])  # max keeps the first on ties: CRLF, then LF
    return best[1], kinds > 1


def decode(data: bytes) -> TextFile:
    """Raises ValueError("binary") for files that aren't text."""
    if is_binary(data):
        raise ValueError("binary")
    found = _bom_of(data)
    if found:
        bom, encoding = found
        text = _decode_text(data[len(bom) :], encoding)
    else:
        try:
            text, encoding = data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, encoding = _decode_text(data, "windows-1252"), "windows-1252"
    newline, mixed = _newlines(text)
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextFile(normal, encoding, bool(found), newline, mixed, data)


def encode(
    f: TextFile,
    text: str,
    *,
    encoding: Encoding | None = None,
    newline: Newline | None = None,
    bom: bool | None = None,
) -> bytes:
    """The bytes to save. Raises ValueError("encoding") if the text doesn't fit the encoding."""
    enc = encoding or f.encoding
    nl = newline or f.newline
    with_bom = f.bom if bom is None else bom
    if (
        text == f.text
        and (enc, nl, with_bom) == (f.encoding, f.newline, f.bom)
        and not (newline and f.mixed)
    ):
        return f.raw
    body = text.replace("\n", nl) if nl != "\n" else text
    try:
        if enc == "windows-1252":
            if not _CP1252_MISSING.isdisjoint(body):
                raise UnicodeEncodeError("windows-1252", body, 0, 1, "no such character")
            out = body.translate(_CP1252_ENCODE).encode("latin-1")
        else:
            out = body.encode(enc)
    except UnicodeEncodeError as e:
        raise ValueError("encoding") from e
    if with_bom:
        out = next(b for b, e in _BOMS if e == enc) + out if enc != "windows-1252" else out
    return out
