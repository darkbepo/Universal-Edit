"""Subtitle files ↔ documents (``web/src/subedit/io.ts`` is a port).

The text goes through ``codeedit.textfile`` so a save keeps the file's encoding, BOM and line
endings, and an untouched document saves the bytes as read. "Reopen with encoding" decodes
with a chosen charset; such a file is then saved as UTF-8 (both apps do this — the browser has
no encoder for legacy charsets)."""

from __future__ import annotations

from dataclasses import dataclass

from umc.codeedit import textfile
from umc.subedit.formats import SubDoc, SubtitleError, parse, write

MAX_OPEN_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Opened:
    doc: SubDoc  # as read (the history's first snapshot)
    file: textfile.TextFile
    charset: str | None = None  # chosen by "Reopen with encoding", else None


def open_bytes(data: bytes, ext: str, charset: str | None = None) -> Opened:
    """Raises SubtitleError with a message for the user."""
    if len(data) > MAX_OPEN_BYTES:
        raise SubtitleError("This file is too big for the subtitle editor (over 20 MB).")
    if charset:
        try:
            if charset.lower() in ("windows-1252", "iso-8859-1"):  # as browsers read both
                text = textfile._decode_text(data, "windows-1252")
            else:
                text = data.decode(charset, errors="replace")
        except LookupError as exc:
            raise SubtitleError(f"Unknown encoding {charset}.") from exc
        text = text.removeprefix("﻿")
        newline, mixed = textfile._newlines(text)
        normal = text.replace("\r\n", "\n").replace("\r", "\n")
        f = textfile.TextFile(normal, "utf-8", False, newline, mixed, data)
    else:
        try:
            f = textfile.decode(data)
        except ValueError as exc:
            raise SubtitleError("This doesn't look like a subtitle file (it isn't text).") from exc
    return Opened(parse(f.text, ext), f, charset)


@dataclass(frozen=True)
class Saved:
    data: bytes
    utf8: bool  # written as UTF-8 although the file was something else


def to_bytes(opened: Opened, doc: SubDoc, fmt: str) -> Saved:
    """The bytes for ``doc`` saved as ``fmt``: the original bytes when nothing changed, else
    the written text in the file's encoding / BOM / line endings (VTT is always UTF-8; text the
    encoding can't hold, or a file reopened with another charset, becomes UTF-8)."""
    f = opened.file
    same = fmt == opened.doc.format
    if same and doc == opened.doc and opened.charset is None:
        return Saved(f.raw, False)
    text = write(doc, fmt)
    if opened.charset is None and (fmt != "vtt" or f.encoding == "utf-8"):
        try:
            return Saved(textfile.encode(f, text), False)
        except ValueError:
            pass
    body = text.replace("\n", f.newline) if f.newline != "\n" else text
    bom = b"\xef\xbb\xbf" if f.bom and f.encoding == "utf-8" else b""
    return Saved(bom + body.encode("utf-8"), f.encoding != "utf-8" or opened.charset is not None)


def new_document(fmt: str = "srt") -> Opened:
    """An empty document (the hub tile); Save asks for a name first."""
    doc = parse("WEBVTT\n" if fmt == "vtt" else "", fmt) if fmt in ("srt", "vtt", "lrc") else None
    if doc is None:
        raise SubtitleError(f"Can't start an empty .{fmt} file.")
    return Opened(doc, textfile.TextFile("", "utf-8", False, "\n", False, b""), None)
