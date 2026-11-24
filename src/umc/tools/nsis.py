"""Unpack the files of an NSIS installer (``*.exe``) without running it.

Ghostscript's installer can't be run silently: its ``.onInit`` starts with ``SetSilent normal``,
so ``/S`` is ignored and it waits on an invisible welcome page for ever. Its files are a plain
NSIS data block, so we read them out ourselves, the way 7-Zip does, but only what the install
section puts under ``$INSTDIR``. Nothing of the installer's script is run.

Supported: NSIS 3 Unicode installers with *solid* LZMA compression (what Ghostscript ships).
Anything else raises :class:`NsisError` with the reason, so a future change of packing shows up
as a clear message instead of a half-unpacked folder.

Layout (NSIS ``fileform.h``): a 28-byte first header (flags, 0xDEADBEEF, "NullsoftInst",
header length, data length) at a 512-byte boundary, then one LZMA stream holding
``u32 len + header`` followed by every file as ``u32 len + bytes``. The header has 8 blocks
(pages, sections, entries, strings, …); an entry is an opcode + 6 parameters. We follow three
opcodes in order: ``EW_ASSIGNVAR`` (the compiler keeps ``$OUTDIR`` in a variable for ``File /r``),
``EW_CREATEDIR`` with "set out path" (``SetOutPath``) and ``EW_EXTRACTFILE``.
"""

from __future__ import annotations

import datetime
import lzma
import os
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from umc.core.errors import ConversionCancelled, ConversionError

SIGNATURE = b"\xef\xbe\xad\xdeNullsoftInst"
FIRST_HEADER = 28

# Opcodes (NSIS 3 fileform.h).
EW_CREATEDIR = 11
EW_EXTRACTFILE = 20
EW_ASSIGNVAR = 25

# Variables: $0-$9, $R0-$R9, then $CMDLINE, $INSTDIR, $OUTDIR, …
VAR_INSTDIR = 21
VAR_OUTDIR = 22

# Special characters in NSIS 3 Unicode strings; each is followed by one parameter character.
CODE_LANG, CODE_SHELL, CODE_VAR, CODE_SKIP = 1, 2, 3, 4

_INST = "\x00INSTDIR"  # stands for $INSTDIR while paths are built; never a real path part
_UNKNOWN = "\x00?"  # a shell folder, language string or unset variable

_CHUNK = 1 << 20


class NsisError(ConversionError):
    pass


@dataclass
class Entry:
    """One file the install section writes: its path below ``$INSTDIR`` and where its bytes are."""

    path: str  # forward slashes, relative
    offset: int  # into the data area
    mtime: float | None = None


@dataclass
class Installer:
    exe: Path
    data_start: int  # file offset of the LZMA stream
    props: bytes
    header_len: int
    entries: list[Entry] = field(default_factory=list)


def _decompressor(props: bytes) -> lzma.LZMADecompressor:
    value = props[0]
    if value >= 9 * 5 * 5:
        raise NsisError("The installer's packing isn't supported.", details=f"props={props!r}")
    lc, value = value % 9, value // 9
    lp, pb = value % 5, value // 5
    (dict_size,) = struct.unpack("<I", props[1:5])
    return lzma.LZMADecompressor(
        lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}],
    )


class _Stream:
    """Reads the solid stream from the start, in order (LZMA can't seek)."""

    def __init__(self, fh, props: bytes, cancel_event: threading.Event | None = None) -> None:
        self.fh, self.dec, self.cancel = fh, _decompressor(props), cancel_event
        self.buf = bytearray()
        self.pos = 0  # stream position of buf[0]

    def _fill(self, need: int) -> None:
        while len(self.buf) < need:
            if self.cancel is not None and self.cancel.is_set():
                raise ConversionCancelled()
            if self.dec.needs_input:
                raw = self.fh.read(_CHUNK)
                if not raw:
                    raise NsisError("The installer file is cut short.")
            else:
                raw = b""
            self.buf += self.dec.decompress(raw, max_length=max(need - len(self.buf), _CHUNK))

    def read(self, n: int) -> bytes:
        self._fill(n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        self.pos += n
        return out

    def skip_to(self, pos: int) -> None:
        if pos < self.pos:
            raise NsisError("The installer's file list is out of order.", details=str(pos))
        while self.pos < pos:
            self.read(min(_CHUNK, pos - self.pos))

    def copy(self, n: int, sinks: list) -> None:
        while n:
            part = self.read(min(_CHUNK, n))
            for sink in sinks:
                sink.write(part)
            n -= len(part)


def _find_first_header(fh) -> int:
    """File offset of the first header: NSIS puts it on a 512-byte boundary after the stub."""
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    pos = 0
    while pos + FIRST_HEADER <= size:
        fh.seek(pos + 4)
        if fh.read(len(SIGNATURE)) == SIGNATURE:
            return pos
        pos += 512
    raise NsisError("This isn't an installer the app can unpack (no NSIS data found).")


def _strings(header: bytes, start: int, end: int):
    """A function reading the Unicode string at a character offset, with variables resolved."""

    def text(offset: int, variables: dict[int, str]) -> str:
        pos = start + offset * 2
        out: list[str] = []
        while pos + 2 <= end:
            (c,) = struct.unpack_from("<H", header, pos)
            pos += 2
            if c == 0:
                return "".join(out)
            if c in (CODE_LANG, CODE_SHELL, CODE_VAR, CODE_SKIP):
                (p,) = struct.unpack_from("<H", header, pos)
                pos += 2
                if c == CODE_SKIP:
                    out.append(chr(p))
                elif c == CODE_VAR:
                    out.append(variables.get((p & 0x7F) | ((p >> 1) & 0x3F80), _UNKNOWN))
                else:
                    out.append(_UNKNOWN)
            else:
                out.append(chr(c))
        raise NsisError("The installer's text table is damaged.")

    return text


def _join(base: str, name: str) -> str:
    """NSIS joins a relative name to the current folder; an absolute one stands alone."""
    if name.startswith((_INST, _UNKNOWN)) or PureWindowsPath(name).anchor:
        return name
    return f"{base}\\{name}" if base else name


def _relative(path: str) -> str | None:
    """``path`` below $INSTDIR as a safe relative path, or None for anything else."""
    if not path.startswith(_INST):
        return None
    rest = path[len(_INST) :]
    if "\x00" in rest:
        return None
    parts = [p for p in rest.replace("/", "\\").split("\\") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return "/".join(parts)


def _filetime(low: int, high: int) -> float | None:
    if low == 0xFFFFFFFF and high == 0xFFFFFFFF:
        return None
    ticks = (high << 32) | low
    stamp = datetime.datetime(1601, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(
        microseconds=ticks // 10
    )
    try:
        return stamp.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def read(exe: Path) -> Installer:
    """Parse ``exe`` and list the files its install section puts under ``$INSTDIR``."""
    with exe.open("rb") as fh:
        start = _find_first_header(fh)
        fh.seek(start)
        first = fh.read(FIRST_HEADER)
        header_len = struct.unpack_from("<I", first, 20)[0]
        props = fh.read(5)
        if len(props) < 5:
            raise NsisError(
                "The installer isn't packed the way the app expects (solid LZMA).",
                details=f"first bytes {props.hex()}",
            )
        stream = _Stream(fh, props)
        (length,) = struct.unpack("<I", stream.read(4))
        if length != header_len:
            raise NsisError(
                "The installer isn't packed the way the app expects (solid LZMA).",
                details=f"header {length} != {header_len}",
            )
        header = stream.read(header_len)
    installer = Installer(exe, start + FIRST_HEADER, props, header_len)
    blocks = [struct.unpack_from("<II", header, 4 + 8 * k) for k in range(8)]
    (ent_off, ent_num), (str_off, _n), (lang_off, _m) = blocks[2], blocks[3], blocks[4]
    if ent_off + 28 * ent_num > len(header) or not str_off < lang_off <= len(header):
        raise NsisError("The installer's header is damaged.")
    if header[str_off : str_off + 2] != b"\x00\x00":
        raise NsisError("Only Unicode NSIS installers are supported.")
    text = _strings(header, str_off, lang_off)

    variables: dict[int, str] = {VAR_INSTDIR: _INST}
    outdir = ""
    found: dict[str, Entry] = {}  # a later File with the same name overwrites (NSIS does too)
    for k in range(ent_num):
        op, *parm = struct.unpack_from("<7I", header, ent_off + 28 * k)
        if op == EW_ASSIGNVAR and parm[2] == 0 and parm[3] == 0:  # plain StrCpy $x "…"
            variables[parm[0]] = text(parm[1], variables)
        elif op == EW_CREATEDIR and parm[1]:  # SetOutPath
            outdir = text(parm[0], variables)
            variables[VAR_OUTDIR] = outdir
        elif op == EW_EXTRACTFILE:
            rel = _relative(_join(outdir, text(parm[1], variables)))
            if rel is not None:
                found.pop(rel.lower(), None)
                found[rel.lower()] = Entry(rel, parm[2], _filetime(parm[3], parm[4]))
    installer.entries = list(found.values())
    return installer


def extract(
    installer: Installer,
    dest: Path,
    *,
    cancel_event: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    only: Callable[[str], bool] | None = None,
) -> list[Path]:
    """Write the installer's files (those ``only`` accepts) into ``dest``; returns them."""
    dest = dest.resolve()
    wanted = [e for e in installer.entries if only is None or only(e.path)]
    by_offset: dict[int, list[Entry]] = {}
    for entry in wanted:
        by_offset.setdefault(entry.offset, []).append(entry)
    for entry in wanted:
        if not (dest / entry.path).resolve().is_relative_to(dest):
            raise NsisError("The installer contains unsafe paths.", details=entry.path)
    written: list[Path] = []
    total = len(by_offset) or 1
    with installer.exe.open("rb") as fh:
        fh.seek(installer.data_start + 5)
        stream = _Stream(fh, installer.props, cancel_event)
        base = 4 + installer.header_len
        for done, offset in enumerate(sorted(by_offset)):
            stream.skip_to(base + offset)
            (size,) = struct.unpack("<I", stream.read(4))
            if size & 0x80000000:
                raise NsisError("The installer's file data is damaged.", details=str(offset))
            entries = by_offset[offset]
            files = []
            try:
                for entry in entries:
                    target = dest / entry.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    files.append(target.open("wb"))
                    written.append(target)
                stream.copy(size, files)
            finally:
                for f in files:
                    f.close()
            for entry in entries:
                if entry.mtime is not None:
                    os.utime(dest / entry.path, (entry.mtime, entry.mtime))
            if progress is not None:
                progress((done + 1) / total)
    return written
