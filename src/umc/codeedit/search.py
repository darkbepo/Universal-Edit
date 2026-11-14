"""Find and replace for the code editor (match case, whole word, regular expressions)."""

from __future__ import annotations

import re


class SearchError(ValueError):
    """A pattern or replacement that can't be used; the message is shown as it is."""


def _pattern(query: str, *, case: bool, whole_word: bool, regex: bool) -> re.Pattern[str]:
    body = query if regex else re.escape(query)
    if whole_word:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    try:
        return re.compile(body, 0 if case else re.IGNORECASE)
    except re.error as exc:
        raise SearchError(f"That pattern isn't valid: {exc.msg}") from exc


def find_all(
    text: str, query: str, *, case: bool, whole_word: bool, regex: bool
) -> list[tuple[int, int]]:
    """(start, end) of every match; empty matches are skipped."""
    if not query:
        return []
    pattern = _pattern(query, case=case, whole_word=whole_word, regex=regex)
    return [m.span() for m in pattern.finditer(text) if m.end() > m.start()]


def replace_all(
    text: str, query: str, replacement: str, *, case: bool, whole_word: bool, regex: bool
) -> tuple[str, int]:
    """The text with every match replaced, and how many were. With ``regex`` the replacement
    may use \\1 / \\g<name>; otherwise it is inserted as it is."""
    if not query:
        return text, 0
    pattern = _pattern(query, case=case, whole_word=whole_word, regex=regex)
    count = 0

    def swap(m: re.Match[str]) -> str:
        nonlocal count
        if m.end() == m.start():
            return m.group(0)
        count += 1
        if not regex:
            return replacement
        try:
            return m.expand(replacement)
        except (re.error, IndexError) as exc:
            raise SearchError(f"That replacement isn't valid: {exc}") from exc

    return pattern.sub(swap, text), count
