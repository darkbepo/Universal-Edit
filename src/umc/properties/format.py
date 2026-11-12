"""How Properties shows values. The web's ``properties/format.ts`` is a line-by-line port; both
test suites check ``tests/fixtures/properties_format_cases.json``."""

from __future__ import annotations

from datetime import datetime

PAPER_MM = {  # portrait sizes, matched within 2 mm either way round
    "A3": (297, 420),
    "A4": (210, 297),
    "A5": (148, 210),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}


def _num(v: float, decimals: int) -> str:
    """Fixed decimals, trailing zeros (and a bare point) dropped: 2.50 → "2.5", 8.0 → "8"."""
    text = f"{v:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def count(n: int) -> str:
    return f"{n:,}"


def size(n: int) -> str:
    """Like Windows: 1 KB = 1024 bytes, about three significant digits."""
    if n < 1024:
        return "1 byte" if n == 1 else f"{n} bytes"
    v = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        v /= 1024
        if v < 1024 or unit == "TB":
            text = f"{v:.2f}" if v < 10 else f"{v:.1f}" if v < 100 else f"{v:.0f}"
            return f"{text} {unit}"
    raise AssertionError  # unreachable


def size_exact(n: int) -> str:
    """ "2.30 MB (2,412,345 bytes)"; small files just "512 bytes"."""
    return size(n) if n < 1024 else f"{size(n)} ({count(n)} bytes)"


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{_num(seconds, 1)} s"
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


def exposure(t: float) -> str:
    if t <= 0:
        return ""
    if t >= 1:
        return f"{_num(t, 1)} s"
    return f"1/{round(1 / t)} s"


def aperture(f: float) -> str:
    return f"f/{_num(f, 1)}"


def focal(mm: float) -> str:
    return f"{_num(mm, 1)} mm"


def location(lat: float, lon: float) -> str:
    return f"{lat:.5f}, {lon:.5f}"


def pixels(w: int, h: int) -> str:
    return f"{count(w)} × {count(h)} px"


def megapixels(w: int, h: int) -> str:
    return f"{_num(w * h / 1e6, 1)} MP"


def fps(rate: float) -> str:
    return f"{_num(rate, 2)} fps"


def bitrate(bits_per_s: float) -> str:
    if bits_per_s >= 1e6:
        return f"{_num(bits_per_s / 1e6, 1)} Mbit/s"
    return f"{round(bits_per_s / 1e3)} kbit/s"


def sample_rate(hz: int) -> str:
    return f"{_num(hz / 1000, 1)} kHz"


def channels(n: int) -> str:
    return {1: "Mono", 2: "Stereo", 6: "5.1 surround", 8: "7.1 surround"}.get(n, f"{n} channels")


def paper(width_pt: float, height_pt: float) -> str:
    """A PDF page size in points → "A4 (210 × 297 mm)", "Letter, landscape (…)" or "… mm"."""
    w, h = width_pt * 25.4 / 72, height_pt * 25.4 / 72
    dims = f"{_num(w, 0)} × {_num(h, 0)} mm"
    for name, (pw, ph) in PAPER_MM.items():
        if abs(w - pw) <= 2 and abs(h - ph) <= 2:
            return f"{name} ({dims})"
        if abs(w - ph) <= 2 and abs(h - pw) <= 2:
            return f"{name}, landscape ({dims})"
    return dims
