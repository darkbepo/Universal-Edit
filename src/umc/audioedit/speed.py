"""Speed / tempo / pitch for the audio editor: the FFmpeg filter for each change.

- speed: faster and higher (or slower and lower), like a record player: ``asetrate`` + ``aresample``
- tempo: faster or slower, same pitch: ``atempo`` (one takes 0.5…2, so it is chained)
- pitch: higher or lower, same length: speed, then tempo back to the original length

``web/src/audioedit/speed.ts`` is a line-by-line port; both check
``tests/fixtures/audioedit_speed_cases.json``."""

from __future__ import annotations

import math

MODES = ("speed", "tempo", "pitch")
PERCENT_MIN, PERCENT_MAX = 25.0, 400.0  # speed / tempo
SEMITONES_MIN, SEMITONES_MAX = -12.0, 12.0  # pitch
MIN_SECONDS = 0.05  # shorter parts are refused (atempo needs a few blocks of sound)
TOO_SHORT = "Select a longer part (at least a twentieth of a second)."
PAD_SECONDS = 0.5  # silence added after the part, so atempo hands back its last blocks too


def clamp(mode: str, value: float) -> float:
    """``value`` (percent for speed / tempo, semitones for pitch) inside its range."""
    if mode == "pitch":
        return max(SEMITONES_MIN, min(SEMITONES_MAX, value))
    if mode in ("speed", "tempo"):
        return max(PERCENT_MIN, min(PERCENT_MAX, value))
    raise ValueError(mode)


def is_identity(mode: str, value: float) -> bool:
    """True when the change would do nothing (100 %, 0 semitones)."""
    return clamp(mode, value) == (0.0 if mode == "pitch" else 100.0)


def _num(x: float) -> str:
    """A filter number: up to 6 decimals, no trailing zeros."""
    text = f"{x:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def atempo_chain(factor: float) -> list[str]:
    """``atempo`` filters multiplying to ``factor`` (each one 0.5…2)."""
    parts: list[str] = []
    while factor > 2.0:
        parts.append("atempo=2")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    if abs(factor - 1.0) > 1e-9:
        parts.append(f"atempo={_num(factor)}")
    return parts


def stretched_rate(mode: str, value: float, rate: int) -> int:
    """The sample rate ``asetrate`` claims for speed / pitch (whole numbers only)."""
    value = clamp(mode, value)
    ratio = value / 100.0 if mode == "speed" else 2.0 ** (value / 12.0)
    return max(1, math.floor(rate * ratio + 0.5))


def filter_chain(mode: str, value: float, rate: int) -> str:
    """The ``-af`` text for this change of a sound at ``rate`` Hz."""
    value = clamp(mode, value)
    if mode == "tempo":
        parts = atempo_chain(value / 100.0)
    else:
        fake = stretched_rate(mode, value, rate)
        parts = [f"asetrate={fake}", f"aresample={rate}"]
        if mode == "pitch":
            parts += atempo_chain(rate / fake)
    return ",".join(parts) or "anull"


def new_seconds(mode: str, value: float, seconds: float, rate: int) -> float:
    """About how long ``seconds`` of sound will be afterwards."""
    value = clamp(mode, value)
    if mode == "tempo":
        return seconds * 100.0 / value
    if mode == "speed":
        return seconds * rate / stretched_rate(mode, value, rate)
    return seconds


def expected_frames(mode: str, value: float, frames: int, rate: int) -> int:
    """How many frames ``frames`` become (the result is cut / padded to this)."""
    value = clamp(mode, value)
    if mode == "tempo":
        return math.floor(frames * 100.0 / value + 0.5)
    if mode == "speed":
        return math.floor(frames * rate / stretched_rate(mode, value, rate) + 0.5)
    return frames
