"""Pure edits on a tuple of cues (``web/src/subedit/edits.ts`` is a port). Each returns new
cues; the caller pushes them on the history. Times are ms and never go below 0."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace

from umc.codeedit import search
from umc.subedit.formats import Cue

Cues = tuple[Cue, ...]
NEW_CUE_MS = 2000  # length of an added cue when there is room
MIN_SPLIT_MS = 1  # each half of a split keeps at least this


def _round(x: float) -> int:
    return math.floor(x + 0.5)


def set_text(cues: Cues, i: int, text: str) -> Cues:
    return cues[:i] + (replace(cues[i], text=text),) + cues[i + 1 :]


def set_times(cues: Cues, i: int, start: int | None = None, end: int | None = None) -> Cues:
    """A new start keeps the end (pushed along when passed); a new end keeps the start (the
    start pulled back when the end goes before it)."""
    c = cues[i]
    s, e = c.start, c.end
    if start is not None:
        s = max(0, start)
        e = max(e, s)
    if end is not None:
        e = max(0, end)
        s = min(s, e)
    return cues[:i] + (replace(c, start=s, end=e),) + cues[i + 1 :]


def set_length(cues: Cues, i: int, length: int) -> Cues:
    c = cues[i]
    return set_times(cues, i, end=c.start + max(0, length))


def shift(cues: Cues, delta: int, indices: Iterable[int] | None = None) -> Cues:
    """Move cues (all, or ``indices``) by ``delta`` ms. Moving earlier stops when the earliest
    of them reaches 0, so every cue keeps its length and the gaps between them."""
    chosen = set(range(len(cues)) if indices is None else indices)
    if not chosen:
        return cues
    earliest = min(cues[i].start for i in chosen)
    delta = max(delta, -earliest)
    if delta == 0:
        return cues
    return tuple(
        replace(c, start=c.start + delta, end=c.end + delta) if i in chosen else c
        for i, c in enumerate(cues)
    )


def sync(cues: Cues, a: int, a_time: int, b: int, b_time: int) -> Cues:
    """Stretch every cue so cue ``a`` starts at ``a_time`` and cue ``b`` at ``b_time``.
    Raises ValueError("same") when the two cues start together, ("order") when the new times
    would turn the subtitles round."""
    sa, sb = cues[a].start, cues[b].start
    if sa == sb:
        raise ValueError("same")
    factor = (b_time - a_time) / (sb - sa)
    if factor <= 0:
        raise ValueError("order")

    def at(t: int) -> int:
        return max(0, _round(a_time + (t - sa) * factor))

    return tuple(replace(c, start=at(c.start), end=at(c.end)) for c in cues)


def add(cues: Cues, after: int, length: int = NEW_CUE_MS) -> tuple[Cues, int]:
    """An empty cue after cue ``after`` (-1 = at the very start): it starts where that one
    ends and lasts ``length`` ms, shortened to fit before the next cue when that leaves at
    least half of it. It takes the cue before's format fields (ASS style …), not its VTT id."""
    prev = cues[after] if 0 <= after < len(cues) else None
    nxt = cues[after + 1] if after + 1 < len(cues) else None
    start = prev.end if prev else 0
    end = start + length
    if nxt and start < nxt.start < end and nxt.start - start >= length // 2:
        end = nxt.start
    extra = tuple(kv for kv in prev.extra if kv[0] != "id") if prev else ()
    if extra and dict(extra).get("kind") == "Comment":
        extra = tuple(kv if kv[0] != "kind" else ("kind", "Dialogue") for kv in extra)
    cue = Cue(start, end, "", extra)
    at = after + 1
    return cues[:at] + (cue,) + cues[at:], at


def delete(cues: Cues, indices: Iterable[int]) -> Cues:
    gone = set(indices)
    return tuple(c for i, c in enumerate(cues) if i not in gone)


def _split_point(text: str) -> int:
    """Where Split cuts a text with no caret given: the middle line break, else the space
    nearest the middle, else nowhere (-1)."""
    breaks = [i for i, ch in enumerate(text) if ch == "\n"]
    if breaks:
        return breaks[(len(breaks) - 1) // 2]
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return -1
    middle = len(text) / 2
    return min(spaces, key=lambda i: (abs(i - middle), i))


def split(cues: Cues, i: int, at: int | None = None) -> Cues:
    """Cut cue ``i`` in two at text position ``at`` (else ``_split_point``). The time is
    shared out by the number of characters on each side (half each when one side is empty)."""
    c = cues[i]
    pos = at if at is not None and 0 < at < len(c.text) else _split_point(c.text)
    if pos < 0:
        first, second = c.text, ""
    else:
        first, second = c.text[:pos].strip(), c.text[pos:].strip()
    total = len(first) + len(second)
    share = 0.5 if not first or not second else len(first) / total
    span = c.end - c.start
    cut = c.start + _round(span * share)
    if span >= 2 * MIN_SPLIT_MS:
        cut = min(max(cut, c.start + MIN_SPLIT_MS), c.end - MIN_SPLIT_MS)
    extra2 = tuple(kv for kv in c.extra if kv[0] != "id")
    a = replace(c, end=cut, text=first)
    b = Cue(cut, c.end, second, extra2)
    return cues[:i] + (a, b) + cues[i + 1 :]


def join(cues: Cues, indices: Iterable[int]) -> tuple[Cues, int]:
    """One cue from the chosen ones (from the first start to the last end, texts one under
    the other, the first one's fields). Returns the cues and the joined cue's index."""
    chosen = sorted(set(indices))
    if len(chosen) < 2:
        return cues, chosen[0] if chosen else -1
    parts = [cues[i] for i in chosen]
    text = "\n".join(p.text.strip() for p in parts if p.text.strip())
    first = parts[0]
    joined = replace(
        first,
        start=min(p.start for p in parts),
        end=max(p.end for p in parts),
        text=text,
    )
    out = [c for n, c in enumerate(cues) if n not in chosen[1:]]
    at = chosen[0]
    out[at] = joined
    return tuple(out), at


def sort(cues: Cues) -> Cues:
    """By start time (then end); equal ones keep their order."""
    return tuple(sorted(cues, key=lambda c: (c.start, c.end)))


def is_sorted(cues: Cues) -> bool:
    return all((a.start, a.end) <= (b.start, b.end) for a, b in zip(cues, cues[1:], strict=False))


def find(
    cues: Cues, query: str, *, case: bool, whole_word: bool, regex: bool
) -> list[tuple[int, int, int]]:
    """(cue, start, end) of every match in the cues' text. Raises search.SearchError."""
    out: list[tuple[int, int, int]] = []
    for i, c in enumerate(cues):
        for a, b in search.find_all(c.text, query, case=case, whole_word=whole_word, regex=regex):
            out.append((i, a, b))
    return out


def replace_all(
    cues: Cues, query: str, replacement: str, *, case: bool, whole_word: bool, regex: bool
) -> tuple[Cues, int]:
    count = 0
    out = []
    for c in cues:
        text, n = search.replace_all(
            c.text, query, replacement, case=case, whole_word=whole_word, regex=regex
        )
        count += n
        out.append(replace(c, text=text) if n else c)
    return (tuple(out) if count else cues), count


def cue_at(cues: Cues, ms: int) -> list[int]:
    """Indices of the cues shown at ``ms`` (start ≤ ms < end), ASS comment lines left out."""
    return [i for i, c in enumerate(cues) if c.start <= ms < c.end and c.get("kind") != "Comment"]
