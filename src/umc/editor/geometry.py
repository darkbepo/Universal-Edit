"""Integer rectangles (x1 / y1 exclusive)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x0: int
    y0: int
    x1: int
    y1: int

    @classmethod
    def from_points(cls, x0: float, y0: float, x1: float, y1: float) -> Rect:
        return cls(
            math.floor(min(x0, x1)),
            math.floor(min(y0, y1)),
            math.ceil(max(x0, x1)),
            math.ceil(max(y0, y1)),
        )

    @classmethod
    def around(cls, x: float, y: float, radius: float) -> Rect:
        """The pixels a circle of ``radius`` around (x, y) can touch."""
        return cls(
            math.floor(x - radius) - 1,
            math.floor(y - radius) - 1,
            math.ceil(x + radius) + 1,
            math.ceil(y + radius) + 1,
        )

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def empty(self) -> bool:
        return self.width == 0 or self.height == 0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def union(self, other: Rect | None) -> Rect:
        if other is None or other.empty:
            return self
        if self.empty:
            return other
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersect(self, other: Rect) -> Rect:
        r = Rect(
            max(self.x0, other.x0),
            max(self.y0, other.y0),
            min(self.x1, other.x1),
            min(self.y1, other.y1),
        )
        return r if not r.empty else EMPTY

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


EMPTY = Rect(0, 0, 0, 0)


def union_all(rects) -> Rect | None:
    out = None
    for r in rects:
        if r is not None and not r.empty:
            out = r if out is None else out.union(r)
    return out
