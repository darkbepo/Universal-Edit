"""CSV / TSV text ↔ rows: our own RFC 4180 reader / writer, the same in both apps (port:
web/src/dataedit/csvtext.ts). Lenient like spreadsheets: a quote inside an unquoted field is
kept, text after a closing quote is added to the field, an unclosed quote runs to the end."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

SEPARATORS = (",", ";", "\t", "|")
SNIFF_CHARS = 65536
SNIFF_ROWS = 50


@dataclass(frozen=True)
class Parsed:
    rows: list[list[str]]
    quote_all: bool  # every field was in quotes (written back the same way)
    final_newline: bool


_QUOTED = re.compile(r'"((?:[^"]|"")*)"')
_PLAIN: dict[str, re.Pattern[str]] = {}


def _plain(sep: str) -> re.Pattern[str]:
    if sep not in _PLAIN:
        _PLAIN[sep] = re.compile(f"[^{re.escape(sep)}\n]*")
    return _PLAIN[sep]


def parse(text: str, sep: str) -> Parsed:
    """``text`` has "\\n" line endings (codeedit.textfile normalises them)."""
    rows: list[list[str]] = []
    n = len(text)
    final = text.endswith("\n")
    end = n - 1 if final else n
    pos, quoted, fields = 0, 0, 0
    plain = _plain(sep)
    row: list[str] = []
    while pos <= end and end > 0:
        if pos < n and text[pos] == '"':
            m = _QUOTED.match(text, pos)
            if m is None:  # never closed: the rest of the file
                value, pos = text[pos + 1 : end].replace('""', '"'), n
            else:
                value, pos = m.group(1).replace('""', '"'), m.end()
                tail = plain.match(text, pos)
                value += tail.group(0)
                pos = tail.end()
            quoted += 1
        else:
            m = plain.match(text, pos)
            value, pos = m.group(0), m.end()
        fields += 1
        row.append(value)
        if pos < n and text[pos] == sep:
            pos += 1
            continue
        rows.append(row)
        row = []
        pos += 1  # the "\n" (or past the end)
    return Parsed(rows, fields > 0 and quoted == fields, final)


def sniff(text: str) -> str:
    """The separator that gives the same field count (> 1) on most of the first rows; ","
    when none does."""
    sample = text
    if len(text) > SNIFF_CHARS:
        cut = text.rfind("\n", 0, SNIFF_CHARS)
        sample = text[: cut + 1] if cut > 0 else text[:SNIFF_CHARS]
    best, best_score = ",", 0
    for sep in SEPARATORS:
        counts = Counter(len(r) for r in parse(sample, sep).rows[:SNIFF_ROWS])
        if not counts:
            continue
        width, freq = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        score = freq if width > 1 else 0
        if score > best_score:
            best, best_score = sep, score
    return best


def _field(value: str, sep: str, quote_all: bool) -> str:
    if quote_all or sep in value or '"' in value or "\n" in value or "\r" in value:
        return '"' + value.replace('"', '""') + '"'
    return value


def write(rows: list[list[str]], sep: str, quote_all: bool, final_newline: bool) -> str:
    """The rows as text with "\\n" line endings."""
    lines = [sep.join(_field(v, sep, quote_all) for v in r) for r in rows]
    return "\n".join(lines) + ("\n" if final_newline and lines else "")
