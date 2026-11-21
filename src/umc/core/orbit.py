"""The file-type badges that orbit the empty drop zone (both apps; no Qt).

Badges sit on three tilted rings around the "Drop files here" text, seen in perspective: the
far side of a ring is smaller and fainter, the near side bigger and brighter. While a file is
dragged over the box they speed up and pull in (``pull`` 0…1); a drop bursts them outward and
fades them (``burst`` 0…1) before the file list appears. ``layout`` is pure maths — the GUI
advances ``tau`` (faster while pulled), draws the badges, and keeps the text readable (badges
fade as they come near it).

Badges also come and go (``slot_badge``): each of the 48 slots fades a badge in, keeps it for a
while, fades it out and later shows the next one, so all the types take turns.

``web/src/ui/orbit.json`` is this module's BADGES / RINGS (``python -m umc.core.orbit``) and
``web/src/ui/orbit.ts`` ports ``layout``; both test suites check
``tests/fixtures/orbit_cases.json`` (``python -m umc.core.orbit --cases``)."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass

# Every file type the apps open (owner, Nov 2026: all of them, coming and going), common ones
# first: they are the first on screen. icon (Tabler, both icon sets), label, colour (softened
# further when drawn). tests/test_orbit.py checks every extension of core/detect.py is here.
BADGES: list[tuple[str, str, str]] = [
    ("photo", "JPG", "#ec6f9c"), ("movie", "MP4", "#ef6b4a"), ("music", "MP3", "#f0a43a"),
    ("pdf", "PDF", "#e5534b"), ("document", "DOCX", "#4f8ef7"), ("cube", "STL", "#2bb3a3"),
    ("font", "TTF", "#9b7bf2"), ("archive", "ZIP", "#c9a227"), ("subtitle", "SRT", "#4cbf73"),
    ("vector", "SVG", "#2fb5d6"), ("gif", "GIF", "#d96fd1"), ("table", "XLSX", "#3fae5a"),
    ("code", "CSS", "#5c6bc0"), ("code", "JS", "#e3c341"), ("photo", "PNG", "#ec6f9c"),
    ("photo", "HEIC", "#ec6f9c"), ("movie", "MKV", "#ef6b4a"), ("music", "FLAC", "#f0a43a"),
    ("document", "EPUB", "#4f8ef7"), ("vector", "DXF", "#22a6a0"), ("photo", "WEBP", "#ec6f9c"),
    ("photo", "CR2", "#ec6f9c"), ("photo", "PSD", "#ec6f9c"), ("movie", "MOV", "#ef6b4a"),
    ("movie", "AVI", "#ef6b4a"), ("movie", "WEBM", "#ef6b4a"), ("music", "WAV", "#f0a43a"),
    ("music", "M4A", "#f0a43a"), ("music", "OGG", "#f0a43a"), ("document", "PPTX", "#4f8ef7"),
    ("document", "ODT", "#4f8ef7"), ("document", "MD", "#4f8ef7"), ("document", "HTML", "#4f8ef7"),
    ("cube", "OBJ", "#2bb3a3"), ("cube", "3MF", "#2bb3a3"), ("cube", "STEP", "#2bb3a3"),
    ("font", "OTF", "#9b7bf2"), ("font", "WOFF2", "#9b7bf2"), ("archive", "7Z", "#c9a227"),
    ("archive", "RAR", "#c9a227"), ("subtitle", "VTT", "#4cbf73"), ("subtitle", "ASS", "#4cbf73"),
    ("vector", "EPS", "#2fb5d6"), ("vector", "DWG", "#22a6a0"), ("document", "JSON", "#4f8ef7"),
    ("document", "YAML", "#4f8ef7"), ("code", "PY", "#5c6bc0"), ("photo", "TIFF", "#ec6f9c"),
    ("music", "AAC", "#f0a43a"), ("document", "RTF", "#4f8ef7"), ("photo", "3FR", "#ec6f9c"),
    ("movie", "3GP", "#ef6b4a"), ("document", "AZW3", "#4f8ef7"), ("vector", "AI", "#2fb5d6"),
    ("code", "BASH", "#5c6bc0"), ("photo", "ARW", "#ec6f9c"), ("movie", "ASF", "#ef6b4a"),
    ("music", "AC3", "#f0a43a"), ("document", "CBZ", "#4f8ef7"), ("cube", "GLB", "#2bb3a3"),
    ("subtitle", "LRC", "#4cbf73"), ("table", "TSV", "#3fae5a"), ("code", "BAT", "#5c6bc0"),
    ("photo", "AVIF", "#ec6f9c"), ("music", "AIF", "#f0a43a"), ("table", "CSV", "#3fae5a"),
    ("cube", "IGES", "#2bb3a3"), ("font", "WOFF", "#9b7bf2"), ("archive", "TAR", "#c9a227"),
    ("subtitle", "SMI", "#4cbf73"), ("vector", "PS", "#2fb5d6"), ("document", "XML", "#4f8ef7"),
    ("code", "C", "#5c6bc0"), ("photo", "BMP", "#ec6f9c"), ("movie", "FLV", "#ef6b4a"),
    ("music", "AIFF", "#f0a43a"), ("document", "DOC", "#4f8ef7"),
    ("archive", "TAR.BZ2", "#c9a227"), ("code", "CC", "#5c6bc0"), ("movie", "M2TS", "#ef6b4a"),
    ("music", "AMR", "#f0a43a"), ("cube", "OFF", "#2bb3a3"), ("archive", "TAR.GZ", "#c9a227"),
    ("subtitle", "SSA", "#4cbf73"), ("code", "CFG", "#5c6bc0"), ("photo", "CR3", "#ec6f9c"),
    ("movie", "M4V", "#ef6b4a"), ("music", "APE", "#f0a43a"), ("cube", "PLY", "#2bb3a3"),
    ("archive", "TAR.XZ", "#c9a227"), ("code", "CJS", "#5c6bc0"), ("photo", "CRW", "#ec6f9c"),
    ("music", "AU", "#f0a43a"), ("document", "FB2", "#4f8ef7"), ("code", "CMD", "#5c6bc0"),
    ("photo", "DCR", "#ec6f9c"), ("music", "CAF", "#f0a43a"), ("code", "CONF", "#5c6bc0"),
    ("photo", "DDS", "#ec6f9c"), ("code", "CPP", "#5c6bc0"), ("photo", "DNG", "#ec6f9c"),
    ("movie", "MPG", "#ef6b4a"), ("code", "CS", "#5c6bc0"), ("photo", "ERF", "#ec6f9c"),
    ("movie", "MTS", "#ef6b4a"), ("music", "MKA", "#f0a43a"), ("document", "MOBI", "#4f8ef7"),
    ("movie", "MXF", "#ef6b4a"), ("music", "MP2", "#f0a43a"), ("document", "ODP", "#4f8ef7"),
    ("code", "CXX", "#5c6bc0"), ("movie", "OGV", "#ef6b4a"), ("table", "ODS", "#3fae5a"),
    ("code", "ENV", "#5c6bc0"), ("photo", "ICO", "#ec6f9c"), ("movie", "TS", "#ef6b4a"),
    ("music", "OGA", "#f0a43a"), ("code", "GO", "#5c6bc0"), ("photo", "IIQ", "#ec6f9c"),
    ("movie", "VOB", "#ef6b4a"), ("document", "ORG", "#4f8ef7"), ("code", "H", "#5c6bc0"),
    ("photo", "JP2", "#ec6f9c"), ("music", "OPUS", "#f0a43a"), ("code", "HH", "#5c6bc0"),
    ("movie", "WMV", "#ef6b4a"), ("document", "PPT", "#4f8ef7"), ("code", "HPP", "#5c6bc0"),
    ("photo", "KDC", "#ec6f9c"), ("music", "WMA", "#f0a43a"), ("code", "INI", "#5c6bc0"),
    ("photo", "MEF", "#ec6f9c"), ("music", "WV", "#f0a43a"), ("document", "RST", "#4f8ef7"),
    ("code", "IPYNB", "#5c6bc0"), ("photo", "MOS", "#ec6f9c"), ("code", "JAVA", "#5c6bc0"),
    ("photo", "MRW", "#ec6f9c"), ("document", "TEX", "#4f8ef7"), ("photo", "NEF", "#ec6f9c"),
    ("code", "JSX", "#5c6bc0"), ("photo", "NRW", "#ec6f9c"), ("document", "TXT", "#4f8ef7"),
    ("code", "KT", "#5c6bc0"), ("photo", "ORA", "#ec6f9c"), ("document", "WPD", "#4f8ef7"),
    ("code", "KTS", "#5c6bc0"), ("photo", "ORF", "#ec6f9c"), ("table", "XLS", "#3fae5a"),
    ("code", "LESS", "#5c6bc0"), ("photo", "PBM", "#ec6f9c"), ("code", "LOG", "#5c6bc0"),
    ("photo", "PCX", "#ec6f9c"), ("code", "LUA", "#5c6bc0"), ("photo", "PEF", "#ec6f9c"),
    ("document", "XPS", "#4f8ef7"), ("code", "MJS", "#5c6bc0"), ("photo", "PGM", "#ec6f9c"),
    ("code", "MK", "#5c6bc0"), ("code", "PHP", "#5c6bc0"), ("photo", "PPM", "#ec6f9c"),
    ("code", "PL", "#5c6bc0"), ("code", "PM", "#5c6bc0"), ("photo", "QOI", "#ec6f9c"),
    ("code", "PROPERTIES", "#5c6bc0"), ("photo", "RAF", "#ec6f9c"), ("code", "PS1", "#5c6bc0"),
    ("photo", "RW2", "#ec6f9c"), ("code", "PSM1", "#5c6bc0"), ("photo", "SR2", "#ec6f9c"),
    ("photo", "SRF", "#ec6f9c"), ("code", "PYW", "#5c6bc0"), ("photo", "SRW", "#ec6f9c"),
    ("code", "R", "#5c6bc0"), ("photo", "TGA", "#ec6f9c"), ("code", "RB", "#5c6bc0"),
    ("code", "RS", "#5c6bc0"), ("code", "SCSS", "#5c6bc0"), ("photo", "X3F", "#ec6f9c"),
    ("code", "SH", "#5c6bc0"), ("code", "SQL", "#5c6bc0"), ("code", "SVELTE", "#5c6bc0"),
    ("code", "SWIFT", "#5c6bc0"), ("code", "TOML", "#5c6bc0"), ("code", "TSX", "#5c6bc0"),
    ("code", "VUE", "#5c6bc0"), ("code", "ZSH", "#5c6bc0"),
]  # fmt: skip


@dataclass(frozen=True)
class Ring:
    radius: float  # share of the box's half-size
    tilt: float  # degrees the ring leans away from the viewer (0 = flat circle facing you)
    roll: float  # degrees the ellipse is turned on screen
    period: float  # seconds per lap at normal speed
    direction: int  # 1 or -1
    phase: float  # start angle, share of a lap
    count: int  # badge slots on it (slots are dealt out in order)


# Six rings, 48 slots. Two rings are tipped nearly upright (their badges travel up and down)
# and two slantwise; tipped rings run along the box's short side, so they are smaller (0.56).
RINGS: list[Ring] = [
    Ring(radius=1.0, tilt=62, roll=-10, period=100, direction=1, phase=0.0, count=11),
    Ring(radius=0.936 * 0.56, tilt=60, roll=82, period=94.8, direction=-1, phase=0.1, count=10),
    Ring(radius=0.872, tilt=56, roll=14, period=89.6, direction=1, phase=0.3, count=8),
    Ring(radius=0.808 * 0.56, tilt=64, roll=-78, period=84.4, direction=-1, phase=0.45, count=7),
    Ring(radius=0.744, tilt=58, roll=38, period=79.2, direction=1, phase=0.6, count=7),
    Ring(radius=0.68, tilt=66, roll=-34, period=74, direction=-1, phase=0.75, count=5),
]
SLOTS = sum(r.count for r in RINGS)

# Living badges: every slot runs on the same PERIOD; it shows a badge for LIFE_MIN…+LIFE_SPAN s
# of it (fading in and out over FADE s), stays empty for the rest, then shows the next one:
# slot i's k-th badge is BADGES[(i + k * SLOTS) % len(BADGES)], so every type takes its turn.
# One shared period keeps any two slots at most one turn apart, so no type is ever on screen
# twice (that would need two slots 48 apart). All from ``tau`` (no randomness), so both apps
# draw the same; the spread comes from golden-ratio steps per slot.
PERIOD = 14.0
LIFE_MIN, LIFE_SPAN = 9.0, 4.0
FADE = 1.6
GOLDEN = 0.6180339887498949

PAD = 34  # px kept free at the box edges (half a badge)
# Calm on purpose (owner, Nov 2026: "more calm and slow"): laps of 72–100 s, a gentle pull.
PULL_IN = 0.35  # pull 1 shrinks the rings to 65 %
PULL_SPEED = 0.8  # pull 1 makes them 1.8 × as fast (the GUI advances tau with ``speed``)
PULL_EASE = 2.0  # how quickly the pull follows the drag (per second; the GUI eases toward it)
BURST_OUT = 0.9  # burst 1 pushes them out to 190 %
BURST_MS = 650  # how long the drop burst runs before the files are added
STRETCH = 1.8  # leaning rings come out flat; this fills the box's height again
CLEAR = 0.45  # fade-out band around the text, as a share of its half-size


@dataclass(frozen=True)
class Spot:
    x: float  # px from the box centre
    y: float
    scale: float
    alpha: float  # 0…1, before the GUI's own dimming
    z: float  # -1 far … 1 near (draw in increasing z)
    badge: int = 0  # index into BADGES


def _frac(x: float) -> float:
    return x - math.floor(x)


def slot_badge(slot: int, tau: float) -> tuple[int, float]:
    """(badge index, life 0…1) of ``slot`` at orbit time ``tau`` (≥ 0)."""
    life = LIFE_MIN + LIFE_SPAN * _frac(slot * GOLDEN)
    u = tau + _frac(slot * GOLDEN * 3) * PERIOD
    k = math.floor(u / PERIOD)
    t = u - k * PERIOD
    shown = min(max(min(t / FADE, (life - t) / FADE), 0.0), 1.0) if t < life else 0.0
    return (slot + k * SLOTS) % len(BADGES), shown


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
    still: bool = False,
) -> list[Spot]:
    """Where each of the SLOTS badges is at orbit time ``tau`` (seconds) in a box of
    ``width`` × ``height`` px, around text ``text_w`` × ``text_h`` px; ``badge`` says which.
    ``still`` (motion reduced): every slot shows its first badge fully, none half-faded."""
    sx = max(width / 2 - PAD, 1.0)
    sy = max(height / 2 - PAD, 1.0)
    out = 1 + BURST_OUT * (1 - (1 - burst) ** 2)
    fade = (1 - burst) ** 2
    shrink = 1 - PULL_IN * pull
    spots: list[Spot] = []
    slot = 0
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
            d = max(
                abs(px) / hw, abs(py) / hh
            )  # the text's box, corners too (tipped rings cross them)
            near_text = min(max((d - 1) / CLEAR, 0.0), 1.0)
            badge, life = (slot, 1.0) if still else slot_badge(slot, tau)
            slot += 1
            alpha = (0.5 + 0.25 * (depth + 1)) * near_text * fade * life
            scale = (0.82 + 0.18 * depth) * (0.85 + 0.15 * life)  # grows a little as it appears
            spots.append(Spot(px, py, scale, alpha, depth, badge))
    return spots


def export() -> dict:
    return {
        "badges": [list(b) for b in BADGES],
        "rings": [asdict(r) for r in RINGS],
        "pad": PAD,
        "pullIn": PULL_IN,
        "pullSpeed": PULL_SPEED,
        "pullEase": PULL_EASE,
        "burstOut": BURST_OUT,
        "burstMs": BURST_MS,
        "clear": CLEAR,
        "stretch": STRETCH,
        "period": PERIOD,
        "lifeMin": LIFE_MIN,
        "lifeSpan": LIFE_SPAN,
        "fade": FADE,
        "golden": GOLDEN,
    }


CASE_ARGS = [
    (0, 1640, 570, 330, 110, 0, 0),
    (3.7, 1640, 570, 330, 110, 0, 0),
    (12.25, 900, 400, 300, 100, 1, 0),
    (5, 1640, 570, 330, 110, 0.5, 0.5),
    (5, 1640, 570, 330, 110, 0, 1),
    (0, 300, 220, 240, 90, 0, 0),
    (431.3, 1640, 570, 330, 110, 0, 0),  # far along: other badges in the slots
    (431.3, 1640, 570, 330, 110, 0, 0, True),  # motion reduced
]


def cases() -> list[dict]:
    """tests/fixtures/orbit_cases.json (``python -m umc.core.orbit --cases``, on purpose only)."""
    return [{"args": list(a), "spots": [asdict(s) for s in layout(*a)]} for a in CASE_ARGS]


if __name__ == "__main__":  # python -m umc.core.orbit > web/src/ui/orbit.json
    json.dump(cases() if "--cases" in sys.argv else export(), sys.stdout, indent=1)
    sys.stdout.write("\n")
