"""Subtitle files as a list of cues (``web/src/subedit/formats.ts`` is a line-by-line port).

``parse(text, fmt)`` reads SRT, VTT, ASS, SSA, LRC and SMI text (line endings already "\\n");
``write(doc, fmt)`` writes SRT, VTT, ASS, SSA or LRC. A cue's text keeps the file's own markup
(``<i>`` in SRT / VTT / SMI, ``{\\i1}`` in ASS / SSA, none in LRC) with "\\n" between lines; writing
into another kind converts the basic ones (italic, bold, underline, strike) and drops the rest.
ASS / SSA keep everything around the events as written, and every event's other fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

READ_FORMATS = ("srt", "vtt", "ass", "ssa", "lrc", "smi")
WRITE_FORMATS = ("srt", "vtt", "ass", "ssa", "lrc")
LRC_LAST_MS = 4000  # an LRC line with nothing after it is shown this long
MAX_CUES = 100_000

Extra = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Cue:
    start: int  # ms
    end: int  # ms
    text: str  # "\n" between lines, markup as in the file
    extra: Extra = ()  # format fields kept as written (ASS fields, VTT id / settings)

    def get(self, key: str, default: str = "") -> str:
        return next((v for k, v in self.extra if k == key), default)


@dataclass(frozen=True)
class SubDoc:
    format: str
    cues: tuple[Cue, ...]
    # ASS / SSA up to the events' Format line (plus every event line the editor doesn't show:
    # "; notes", SSA Picture / Sound lines, unreadable Dialogue lines), VTT's WEBVTT block, LRC tags
    header: str = ""
    footer: str = ""  # ASS / SSA sections after [Events]
    fields: tuple[str, ...] = ()  # ASS / SSA event field names


class SubtitleError(ValueError):
    """A file that can't be read as subtitles; the message is shown as it is."""


def markup_kind(fmt: str) -> str:
    return {"ass": "ass", "ssa": "ass", "lrc": "plain"}.get(fmt, "html")


# ------------------------------------------------------------------------------------ times
_CLOCK = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.:](\d{1,3}))?")
_ARROW = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)\s*-->\s*"
    r"((?:\d+:)?\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)(.*)$"
)


def _clock_ms(text: str) -> int | None:
    m = _CLOCK.fullmatch(text.strip())
    if not m:
        return None
    h, mi, s, frac = m.groups()
    ms = int((frac or "0").ljust(3, "0"))
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + ms


def _hms(ms: int, sep: str) -> str:
    ms = max(0, ms)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{rest:03d}"


def _ass_time(ms: int) -> str:
    cs = (max(0, ms) + 5) // 10
    h, rest = divmod(cs, 360_000)
    m, rest = divmod(rest, 6000)
    s, rest = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{rest:02d}"


def _lrc_time(ms: int) -> str:
    cs = (max(0, ms) + 5) // 10
    m, rest = divmod(cs, 6000)
    s, rest = divmod(rest, 100)
    return f"[{m:02d}:{s:02d}.{rest:02d}]"


def clock(ms: int) -> str:
    """How the editor shows a time: 0:01:02.500."""
    ms = max(0, ms)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h}:{m:02d}:{s:02d}.{rest:03d}"


# ----------------------------------------------------------------------------------- markup
_ASS_BLOCK = re.compile(r"\{([^}]*)\}")
_ASS_BASIC = re.compile(r"\\([ibus])([01])(?![0-9])")
_HTML_TAG = re.compile(r"<(/?)([a-zA-Z]+)(?:[\s.][^>]*)?>|<[0-9:.]+>")
_BASIC = ("i", "b", "u", "s")


def _ass_to_html(text: str) -> str:
    def block(m: re.Match[str]) -> str:
        return "".join(
            f"<{'' if on == '1' else '/'}{tag}>" for tag, on in _ASS_BASIC.findall(m.group(1))
        )

    return _ASS_BLOCK.sub(block, text).replace("\\h", " ")


def _html_basic(text: str) -> str:
    """Only <i> <b> <u> <s> (and their ends) kept."""

    def tag(m: re.Match[str]) -> str:
        name = (m.group(2) or "").lower()
        return f"<{m.group(1)}{name}>" if name in _BASIC else ""

    return _HTML_TAG.sub(tag, text)


def _html_to_ass(text: str) -> str:
    def tag(m: re.Match[str]) -> str:
        name = (m.group(2) or "").lower()
        if name not in _BASIC:
            return ""
        return "{\\" + name + ("0" if m.group(1) else "1") + "}"

    return _HTML_TAG.sub(tag, text.replace("{", "(").replace("}", ")"))


def plain_text(text: str, fmt: str) -> str:
    """The words a viewer reads (markup removed)."""
    kind = markup_kind(fmt)
    if kind == "ass":
        return _ASS_BLOCK.sub("", text).replace("\\h", " ")
    if kind == "html":
        return _HTML_TAG.sub("", text)
    return text


def convert_markup(text: str, src: str, dst: str) -> str:
    a, b = markup_kind(src), markup_kind(dst)
    if src == dst or (a == b == "ass"):
        return text
    if b == "plain":
        return plain_text(text, src)
    if a == "ass":
        return _ass_to_html(text) if b == "html" else text
    if b == "ass":
        return _html_to_ass(text)
    return _html_basic(text)  # html → html of another format, or plain → html (no tags)


# ---------------------------------------------------------------------------------- parsing
def _blocks(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _parse_srt(text: str) -> SubDoc:
    cues: list[Cue] = []
    for block in _blocks(text):
        at = next((i for i, line in enumerate(block[:2]) if _ARROW.match(line)), None)
        if at is None:
            if cues:  # text with a blank line inside: belongs to the cue before
                last = cues[-1]
                cues[-1] = replace(last, text=last.text + "\n" + "\n".join(block))
            continue
        m = _ARROW.match(block[at])
        assert m
        start, end = _clock_ms(m.group(1)), _clock_ms(m.group(2))
        if start is None or end is None:
            continue
        extra: Extra = (("pos", m.group(3).rstrip()),) if m.group(3).strip() else ()
        cues.append(Cue(start, end, "\n".join(block[at + 1 :]), extra))
    return SubDoc("srt", tuple(cues))


def _parse_vtt(text: str) -> SubDoc:
    blocks = _blocks(text.lstrip("﻿"))
    if not blocks or not blocks[0][0].startswith("WEBVTT"):
        raise SubtitleError("This isn't a WebVTT file: it doesn't start with WEBVTT.")
    header = ["\n".join(blocks[0])]
    cues: list[Cue] = []
    for block in blocks[1:]:
        at = next((i for i, line in enumerate(block[:2]) if "-->" in line), None)
        if at is None:
            if not cues:  # NOTE / STYLE / REGION blocks before the first cue
                header.append("\n".join(block))
            else:  # a NOTE between cues stays after the cue before it
                last = cues[-1]
                notes = "\n\n".join(filter(None, (last.get("after"), "\n".join(block))))
                kept = tuple(kv for kv in last.extra if kv[0] != "after") + (("after", notes),)
                cues[-1] = replace(last, extra=kept)
            continue
        m = _ARROW.match(block[at])
        if not m:
            continue
        start, end = _clock_ms(m.group(1)), _clock_ms(m.group(2))
        if start is None or end is None:
            continue
        extra: list[tuple[str, str]] = []
        if at == 1:
            extra.append(("id", block[0]))
        if m.group(3).strip():
            extra.append(("settings", m.group(3).strip()))
        cues.append(Cue(start, end, "\n".join(block[at + 1 :]), tuple(extra)))
    return SubDoc("vtt", tuple(cues), header="\n\n".join(header))


def _parse_ass(text: str, fmt: str) -> SubDoc:
    lines = text.lstrip("﻿").split("\n")
    events = next((i for i, line in enumerate(lines) if line.strip().lower() == "[events]"), None)
    if events is None:
        raise SubtitleError("This file has no [Events] section, so it holds no subtitles.")
    fields: tuple[str, ...] = ()
    head_end = events + 1
    i = events + 1
    cues: list[Cue] = []
    other: list[str] = []  # event lines the editor doesn't show: written back as they were
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        key, sep, rest = line.partition(":")
        name = key.strip()
        cue = None
        if sep and name.lower() == "format" and not fields:
            fields = tuple(f.strip() for f in rest.split(","))
            head_end = i + 1
            i += 1
            continue
        if sep and name in ("Dialogue", "Comment") and fields:
            values = rest.lstrip(" ").split(",", len(fields) - 1)
            if len(values) == len(fields):
                d = dict(zip(fields, values, strict=True))
                start = _clock_ms(d.get("Start", ""))
                end = _clock_ms(d.get("End", ""))
                if start is not None and end is not None:
                    extra = (("kind", name),) + tuple(
                        (f, v)
                        for f, v in zip(fields, values, strict=True)
                        if f not in ("Start", "End", "Text")
                    )
                    cue = Cue(start, end, d.get("Text", "").replace("\\N", "\n"), extra)
        if cue is not None:
            cues.append(cue)
        elif stripped and fields:
            other.append(line)
        i += 1
    if not fields:
        raise SubtitleError("The [Events] section has no Format line.")
    header = "\n".join(lines[:head_end] + other)
    footer = "\n".join(lines[i:]).strip("\n")
    return SubDoc(fmt, tuple(cues), header=header, footer=footer, fields=fields)


_LRC_STAMP = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_TAG = re.compile(r"^\[[a-zA-Z#][^\]]*\]\s*$")


def _parse_lrc(text: str) -> SubDoc:
    header: list[str] = []
    stamps: list[tuple[int, str]] = []  # (ms, text); "" text = the line before ends there
    for raw in text.lstrip("﻿").split("\n"):
        line = raw.strip()
        if not line:
            continue
        times: list[int] = []
        pos = 0
        while m := _LRC_STAMP.match(line, pos):
            mi, s, frac = m.groups()
            times.append((int(mi) * 60 + int(s)) * 1000 + int((frac or "0").ljust(3, "0")[:3]))
            pos = m.end()
        if times:
            words = line[pos:].strip()
            stamps.extend((t, words) for t in times)
        elif _LRC_TAG.match(line) and not stamps:
            header.append(line)
    stamps.sort(key=lambda st: st[0])  # stable: same-time lines keep their order
    cues: list[Cue] = []
    for n, (t, words) in enumerate(stamps):
        if not words:
            continue
        nxt = stamps[n + 1][0] if n + 1 < len(stamps) else t + LRC_LAST_MS
        cues.append(Cue(t, max(t, nxt), words))
    return SubDoc("lrc", tuple(cues), header="\n".join(header))


_SYNC = re.compile(r"<sync\b[^>]*?\bstart\s*=\s*[\"']?(\d+)[^>]*>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_ENTITY = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"'}


def _parse_smi(text: str) -> SubDoc:
    body = re.split(r"<body\b[^>]*>", text, maxsplit=1, flags=re.I)[-1]
    parts = _SYNC.split(body)
    syncs: list[tuple[int, str]] = []
    for n in range(1, len(parts) - 1, 2):
        chunk = re.split(r"</body>", parts[n + 1], maxsplit=1, flags=re.I)[0]
        chunk = _BR.sub("\n", chunk.replace("\n", " "))
        chunk = re.sub(r"<(?!/?[ibu]>)[^>]*>", "", chunk, flags=re.I)
        for ent, ch in _ENTITY.items():
            chunk = re.sub(re.escape(ent), ch, chunk, flags=re.I)
        lines = [" ".join(line.split()) for line in chunk.split("\n")]
        syncs.append((int(parts[n]), "\n".join(line for line in lines if line)))
    syncs.sort(key=lambda st: st[0])
    cues: list[Cue] = []
    for n, (t, words) in enumerate(syncs):
        if not words.strip():
            continue
        nxt = syncs[n + 1][0] if n + 1 < len(syncs) else t + LRC_LAST_MS
        cues.append(Cue(t, max(t, nxt), words))
    return SubDoc("smi", tuple(cues))


def parse(text: str, fmt: str) -> SubDoc:
    """``text`` with "\\n" line endings. Raises SubtitleError for a file that isn't ``fmt``."""
    fmt = fmt.lower()
    if fmt == "srt":
        doc = _parse_srt(text)
    elif fmt == "vtt":
        doc = _parse_vtt(text)
    elif fmt in ("ass", "ssa"):
        doc = _parse_ass(text, fmt)
    elif fmt == "lrc":
        doc = _parse_lrc(text)
    elif fmt == "smi":
        doc = _parse_smi(text)
    else:
        raise SubtitleError(f"The subtitle editor can't open .{fmt} files.")
    if len(doc.cues) > MAX_CUES:
        raise SubtitleError("This file has more than 100,000 subtitles.")
    return doc


# ---------------------------------------------------------------------------------- writing
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,\
2,40,40,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""
ASS_FIELDS = ("Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV",
              "Effect", "Text")  # fmt: skip
_ASS_DEFAULTS = {"Layer": "0", "Marked": "Marked=0", "Style": "Default", "MarginL": "0",
                 "MarginR": "0", "MarginV": "0"}  # fmt: skip


def _no_blank_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if line.strip())


def _shown(doc: SubDoc) -> list[Cue]:
    """The cues other formats get: ASS comment lines left out."""
    return [c for c in doc.cues if c.get("kind") != "Comment"]


def _write_srt(doc: SubDoc) -> str:
    out = []
    for n, c in enumerate(_shown(doc), 1):
        text = _no_blank_lines(convert_markup(c.text, doc.format, "srt"))
        pos = c.get("pos") if doc.format == "srt" else ""
        out.append(f"{n}\n{_hms(c.start, ',')} --> {_hms(c.end, ',')}{pos}\n{text}\n")
    return "\n".join(out)


def _write_vtt(doc: SubDoc) -> str:
    header = doc.header if doc.format == "vtt" and doc.header else "WEBVTT"
    out = [header + "\n"]
    for c in _shown(doc):
        text = _no_blank_lines(convert_markup(c.text, doc.format, "vtt")).replace("-->", "->")
        ident = c.get("id") if doc.format == "vtt" else ""
        settings = c.get("settings") if doc.format == "vtt" else ""
        line = f"{_hms(c.start, '.')} --> {_hms(c.end, '.')}" + (f" {settings}" if settings else "")
        out.append((f"{ident}\n" if ident else "") + f"{line}\n{text}\n")
        if doc.format == "vtt" and c.get("after"):
            out.append(c.get("after") + "\n")
    return "\n".join(out)


def _write_ass(doc: SubDoc, fmt: str) -> str:
    same = markup_kind(doc.format) == "ass" and doc.header
    if fmt == "ssa" and not (same and doc.format == "ssa"):
        raise SubtitleError("Only SSA files can be saved as SSA; choose ASS instead.")
    header = doc.header if same else ASS_HEADER
    fields = doc.fields if same else ASS_FIELDS
    lines = [header]
    cues = doc.cues if same else _shown(doc)
    for c in cues:
        text = convert_markup(c.text, doc.format, fmt).replace("\n", "\\N")
        kept = dict(c.extra) if same else {}
        values = []
        for f in fields:
            if f == "Start":
                values.append(_ass_time(c.start))
            elif f == "End":
                values.append(_ass_time(c.end))
            elif f == "Text":
                values.append(text)
            else:
                values.append(kept.get(f, _ASS_DEFAULTS.get(f, "")))
        lines.append(f"{kept.get('kind', 'Dialogue')}: " + ",".join(values))
    body = "\n".join(lines) + "\n"
    if same and doc.footer:
        body += "\n" + doc.footer + "\n"
    return body


def _write_lrc(doc: SubDoc) -> str:
    lines = [doc.header] if doc.format == "lrc" and doc.header else []
    cues = sorted(_shown(doc), key=lambda c: c.start)
    for n, c in enumerate(cues):
        words = " ".join(convert_markup(c.text, doc.format, "lrc").split())
        lines.append(_lrc_time(c.start) + words)
        last = n + 1 == len(cues)
        nxt = c.start + LRC_LAST_MS if last else cues[n + 1].start
        if c.end < nxt or (last and c.end != nxt):  # LRC can't show an overlap
            lines.append(_lrc_time(c.end))
    return "\n".join(lines) + "\n"


def write(doc: SubDoc, fmt: str) -> str:
    """The file's text with "\\n" line endings."""
    fmt = fmt.lower()
    if fmt == "srt":
        return _write_srt(doc)
    if fmt == "vtt":
        return _write_vtt(doc)
    if fmt in ("ass", "ssa"):
        return _write_ass(doc, fmt)
    if fmt == "lrc":
        return _write_lrc(doc)
    raise SubtitleError(f"The subtitle editor can't save .{fmt} files.")


def save_formats(doc: SubDoc) -> tuple[str, ...]:
    """Formats Save as offers for this document, its own first when it can be written."""
    out = ["srt", "vtt", "ass", "lrc"]
    if doc.format == "ssa":
        out.insert(0, "ssa")
    if doc.format in out:
        out.remove(doc.format)
        out.insert(0, doc.format)
    return tuple(out)


def _hidden_events(doc: SubDoc) -> bool:
    """An ASS / SSA header holding event lines after its Format line ("; notes" …)."""
    lines = [x.strip().lower() for x in doc.header.split("\n")]
    if "[events]" not in lines:
        return False
    after = lines[lines.index("[events]") + 1 :]
    at = next((i for i, x in enumerate(after) if x.startswith("format:")), None)
    return at is not None and any(after[at + 1 :])


def lost_parts(doc: SubDoc, fmt: str) -> list[str]:
    """What saving ``doc`` as ``fmt`` leaves out, in plain words (empty = nothing)."""
    if fmt == doc.format:
        return []
    out: list[str] = []
    src, dst = markup_kind(doc.format), markup_kind(fmt)
    if src == "ass" and dst != "ass":
        out.append("styles, positions and effects")
        if any(c.get("kind") == "Comment" for c in doc.cues) or _hidden_events(doc):
            out.append("comment lines")
    if doc.format == "vtt" and (any(c.extra for c in doc.cues) or "\n" in doc.header):
        out.append("cue positions, names and notes")
    if dst == "plain" and doc.format != "lrc":
        if any(_ASS_BLOCK.search(c.text) or _HTML_TAG.search(c.text) for c in doc.cues):
            out.append("italic / bold text")
        if any("\n" in c.text for c in doc.cues):
            out.append("line breaks (LRC has one line per subtitle)")
    if doc.format == "lrc" and doc.header:
        out.append("song details (title, artist …)")
    return out
