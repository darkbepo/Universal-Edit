"""Text editing helpers of the code editor: indenting, comments, auto-indent, brackets.

Pure functions on strings and lists of lines (the website ports them line by line)."""

from __future__ import annotations

import re

from umc.codeedit.languages import LANGUAGES, Language

DEFAULT_INDENT = "    "
BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {v: k for k, v in BRACKETS.items()}
_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "source", "wbr"}
_OPEN_TAG = re.compile(r"<([A-Za-z][\w-]*)\b[^<>]*(?<!/)>$")


def _language(language_id: str) -> Language | None:
    return next((lang for lang in LANGUAGES if lang.id == language_id), None)


def _lead(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def detect_indent(text: str) -> str:
    """The file's indent unit: "\\t", two spaces or four spaces (four when unclear)."""
    tabs = spaces = 0
    smallest = 0
    for line in text.splitlines():
        lead = _lead(line)
        if not lead or not line.strip():
            continue
        if lead.startswith("\t"):
            tabs += 1
        else:
            spaces += 1
            n = len(lead) - len(lead.lstrip(" "))
            smallest = n if not smallest else min(smallest, n)
    if tabs > spaces:
        return "\t"
    return "  " if smallest == 2 else DEFAULT_INDENT


def indent_lines(lines: list[str], unit: str) -> list[str]:
    return [unit + line if line.strip() else line for line in lines]


def outdent_lines(lines: list[str], unit: str) -> list[str]:
    """One indent level less: a tab, or up to ``len(unit)`` spaces (at least one space)."""
    width = max(len(unit.expandtabs(4)), 1)
    out = []
    for line in lines:
        if line.startswith("\t"):
            out.append(line[1:])
            continue
        n = len(line) - len(line.lstrip(" "))
        out.append(line[min(n, width) :])
    return out


def toggle_comment(lines: list[str], language_id: str) -> list[str]:
    """Comment the lines out, or back in when they already are (line comments where the
    language has them, else one block comment around the lines)."""
    lang = _language(language_id)
    body = [line for line in lines if line.strip()]
    if lang is None or not body:
        return list(lines)
    if lang.line_comment:
        return _toggle_line_comments(lines, lang.line_comment)
    if lang.block_comment:
        return _toggle_block_comment(lines, *lang.block_comment)
    return list(lines)


def _toggle_line_comments(lines: list[str], marker: str) -> list[str]:
    body = [line for line in lines if line.strip()]
    if all(line.lstrip(" \t").startswith(marker) for line in body):
        out = []
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            lead = _lead(line)
            rest = line[len(lead) + len(marker) :]
            out.append(lead + (rest[1:] if rest.startswith(" ") else rest))
        return out
    indent = min(len(_lead(line)) for line in body)
    mark = marker + " "
    return [line[:indent] + mark + line[indent:] if line.strip() else line for line in lines]


def _toggle_block_comment(lines: list[str], opener: str, closer: str) -> list[str]:
    out = list(lines)
    rows = [i for i, line in enumerate(lines) if line.strip()]
    first, last = rows[0], rows[-1]
    head = out[first].lstrip(" \t")
    if head.startswith(opener) and out[last].rstrip().endswith(closer):
        lead = _lead(out[first])
        rest = head[len(opener) :]
        out[first] = lead + (rest[1:] if rest.startswith(" ") else rest)
        tail = out[last].rstrip()[: -len(closer)]
        out[last] = tail[:-1] if tail.endswith(" ") else tail
        return out
    lead = _lead(out[first])
    out[first] = lead + opener + " " + out[first][len(lead) :]
    out[last] = out[last].rstrip() + " " + closer
    return out


def next_line_indent(line_before_cursor: str, unit: str) -> str:
    """The indent a new line gets: the same as this one, one more after an opening bracket, a
    colon or an opening HTML tag."""
    lead = _lead(line_before_cursor)
    end = line_before_cursor.rstrip()
    if end.endswith(("{", "[", "(", ":")):
        return lead + unit
    tag = _OPEN_TAG.search(end)
    if tag and tag.group(1).lower() not in _VOID_TAGS:
        return lead + unit
    return lead


def matching_bracket(text: str, pos: int) -> int | None:
    """Where the bracket at ``pos`` (or just before it) is closed / opened; None if it isn't."""
    for p in (pos, pos - 1):
        if not 0 <= p < len(text):
            continue
        ch = text[p]
        if ch in BRACKETS:
            return _scan(text, p, ch, BRACKETS[ch], 1)
        if ch in _CLOSERS:
            return _scan(text, p, ch, _CLOSERS[ch], -1)
    return None


def _scan(text: str, start: int, same: str, other: str, step: int) -> int | None:
    depth = 0
    i = start
    while 0 <= i < len(text):
        ch = text[i]
        if ch == same:
            depth += 1
        elif ch == other:
            depth -= 1
            if depth == 0:
                return i
        i += step
    return None
