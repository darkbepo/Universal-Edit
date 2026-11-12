"""The file-type badges that orbit the empty drop zone (both apps; no Qt).

Badges sit on three tilted rings around the "Drop files here" text, seen in perspective: the
far side of a ring is smaller and fainter, the near side bigger and brighter. While a file is
dragged over the box they speed up and pull in (``pull`` 0…1); a drop bursts them outward and
fades them (``burst`` 0…1) before the file list appears. ``layout`` is pure maths — the GUI
advances ``tau`` (faster while pulled), draws the badges, and keeps the text readable (badges
fade as they come near it).

``web/src/ui/orbit.json`` is this module's BADGES / RINGS (``python -m umc.core.orbit``) and
``web/src/ui/orbit.ts`` ports ``layout``; both test suites check
``tests/fixtures/orbit_cases.json``."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass

# icon (Tabler, both icon sets), label, colour (softened further when drawn)
BADGES: list[tuple[str, str, str]] = [
    ("photo", "JPG", "#ec6f9c"),
    ("movie", "MP4", "#ef6b4a"),
    ("music", "MP3", "#f0a43a"),
    ("pdf", "PDF", "#e5534b"),
    ("document", "DOCX", "#4f8ef7"),
    ("cube", "STL", "#2bb3a3"),
    ("font", "TTF", "#9b7bf2"),
    ("archive", "ZIP", "#c9a227"),
    ("subtitle", "SRT", "#4cbf73"),
    ("vector", "SVG", "#2fb5d6"),
    ("gif", "GIF", "#d96fd1"),
    ("table", "XLSX", "#3fae5a"),
]


@dataclass(frozen=True)
class Ring:
    radius: float  # share of the box's half-size
    tilt: float  # degrees the ring leans away from the viewer (0 = flat circle facing you)
    roll: float  # degrees the ellipse is turned on screen
    period: float  # seconds per lap at normal speed
    direction: int  # 1 or -1
    phase: float  # start angle, share of a lap
    count: int  # badges on it (BADGES are dealt out in order)


RINGS: list[Ring] = [
    Ring(radius=1.0, tilt=62, roll=-10, period=40, direction=1, phase=0.0, count=5),
    Ring(radius=0.84, tilt=56, roll=14, period=32, direction=-1, phase=0.1, count=4),
    Ring(radius=0.68, tilt=68, roll=3, period=26, direction=1, phase=0.3, count=3),
]

PAD = 34  # px kept free at the box edges (half a badge)
PULL_IN = 0.45  # pull 1 shrinks the rings to 55 %
PULL_SPEED = 2.5  # pull 1 makes them 3.5 × as fast (the GUI advances tau with ``speed``)
BURST_OUT = 1.6  # burst 1 pushes them out to 260 %
BURST_MS = 420  # how long the drop burst runs before the files are added
STRETCH = 1.8  # leaning rings come out flat; this fills the box's height again
CLEAR = 0.45  # fade-out band around the text, as a share of its half-size


@dataclass(frozen=True)
class Spot:
    x: float  # px from the box centre
    y: float
    scale: float
    alpha: float  # 0…1, before the GUI's own dimming
    z: float  # -1 far … 1 near (draw in increasing z)


def speed(pull: float) -> float:
    return 1 + PULL_SPEED * pull


def layout(
    tau: float,
    width: float,
    height: float,
    text_w: float,
    text_h: float,
    pull: float = 0.0,
    burst: float = 0.0,
) -> list[Spot]:
    """Where each badge (BADGES order) is at orbit time ``tau`` (seconds) in a box of
    ``width`` × ``height`` px, around text ``text_w`` × ``text_h`` px."""
    sx = max(width / 2 - PAD, 1.0)
    sy = max(height / 2 - PAD, 1.0)
    out = 1 + BURST_OUT * (1 - (1 - burst) ** 3)
    fade = (1 - burst) ** 2
    shrink = 1 - PULL_IN * pull
    spots: list[Spot] = []
    for ring in RINGS:
        tilt, roll = math.radians(ring.tilt), math.radians(ring.roll)
        for k in range(ring.count):
            turn = ring.phase + k / ring.count + ring.direction * tau / ring.period
            a = 2 * math.pi * turn
            r = ring.radius * shrink * out
            x0, y0 = r * math.cos(a), r * math.sin(a)
            y1 = y0 * math.cos(tilt)  # the ring leans back: seen from the front it's flatter
            x = x0 * math.cos(roll) - y1 * math.sin(roll)
            y = x0 * math.sin(roll) + y1 * math.cos(roll)
            depth = math.sin(a)  # -1 far side … 1 near side
            px, py = x * sx, y * sy * STRETCH
            # fade near the text so it always stays readable
            hw, hh = max(text_w / 2, 1.0), max(text_h / 2, 1.0)
            d = math.hypot(px / hw, py / hh)
            near_text = min(max((d - 1) / CLEAR, 0.0), 1.0)
            alpha = (0.5 + 0.25 * (depth + 1)) * near_text * fade
            spots.append(Spot(px, py, 0.82 + 0.18 * depth, alpha, depth))
    return spots


def export() -> dict:
    return {
        "badges": [list(b) for b in BADGES],
        "rings": [asdict(r) for r in RINGS],
        "pad": PAD,
        "pullIn": PULL_IN,
        "pullSpeed": PULL_SPEED,
        "burstOut": BURST_OUT,
        "burstMs": BURST_MS,
        "clear": CLEAR,
        "stretch": STRETCH,
    }


if __name__ == "__main__":  # python -m umc.core.orbit > web/src/ui/orbit.json
    json.dump(export(), sys.stdout, indent=1)
    sys.stdout.write("\n")
