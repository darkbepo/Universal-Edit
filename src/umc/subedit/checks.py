"""Problems worth a look in a subtitle list (``web/src/subedit/checks.ts`` is a port)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from umc.subedit.formats import Cue, plain_text

MIN_MS = 700  # shorter is hard to read
MAX_CPS = 21  # characters a second (spaces count, line breaks don't)
MAX_LINE = 42  # characters on one line
MAX_LINES = 2


@dataclass(frozen=True)
class Problem:
    index: int
    kind: str  # empty, backwards, overlap, short, fast, long, lines
    message: str


def _seconds(ms: int) -> str:
    tenths = (ms + 50) // 100  # whole numbers: Python and JS round the same way
    return f"{tenths // 10}.{tenths % 10} s"


def check(cues: tuple[Cue, ...], fmt: str) -> list[Problem]:
    """Every problem, in cue order (a cue can have several). ASS comment lines are skipped."""
    out: list[Problem] = []
    prev: tuple[int, Cue] | None = None
    for i, c in enumerate(cues):
        if c.get("kind") == "Comment":
            continue
        words = plain_text(c.text, fmt)
        lines = [line.strip() for line in words.split("\n") if line.strip()]
        span = c.end - c.start
        if not lines:
            out.append(Problem(i, "empty", "No text"))
        if span <= 0:
            out.append(Problem(i, "backwards", "Ends before it starts"))
        elif span < MIN_MS:
            out.append(Problem(i, "short", f"Shown for only {_seconds(span)}"))
        if prev is not None and c.start < prev[1].end:
            out.append(Problem(i, "overlap", f"Starts before line {prev[0] + 1} ends"))
        chars = sum(len(line) for line in lines)
        if span > 0 and lines:
            cps = chars * 1000 / span
            if cps > MAX_CPS:
                rate = math.floor(cps + 0.5)
                out.append(Problem(i, "fast", f"Too fast to read ({rate} characters a second)"))
        longest = max((len(line) for line in lines), default=0)
        if longest > MAX_LINE:
            out.append(Problem(i, "long", f"A line is {longest} characters long"))
        if len(lines) > MAX_LINES:
            out.append(Problem(i, "lines", f"{len(lines)} lines (two fit best)"))
        if prev is None or c.end >= prev[1].end:
            prev = (i, c)
    return out
