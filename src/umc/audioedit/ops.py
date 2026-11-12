"""Sample maths for the audio editor: pure functions on float32 arrays (channels, frames).

Port: web/src/audioedit/ops.ts (tests use the same cases)."""

from __future__ import annotations

import math

import numpy as np

GAIN_MIN_DB, GAIN_MAX_DB = -48.0, 48.0
NORMALIZE_DB = -1.0  # peak level Normalize aims for


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def peak(x: np.ndarray) -> float:
    """Largest absolute sample (0 for silence / nothing)."""
    return float(np.max(np.abs(x))) if x.size else 0.0


def peak_db(x: np.ndarray) -> float | None:
    """Peak level in dBFS, None for silence."""
    p = peak(x)
    return 20.0 * math.log10(p) if p > 0 else None


def gain(x: np.ndarray, db: float) -> np.ndarray:
    return (x * np.float32(db_to_gain(db))).astype(np.float32)


def normalize_db(x: np.ndarray, target_db: float = NORMALIZE_DB) -> float | None:
    """The gain (dB) that brings the peak to ``target_db``; None for silence."""
    p = peak_db(x)
    return None if p is None else target_db - p


def fade(x: np.ndarray, fade_in: bool) -> np.ndarray:
    """Linear fade over the whole range: 0 → 1 (in) or 1 → 0 (out)."""
    n = x.shape[1]
    if n == 0:
        return x.copy()
    ramp = np.arange(n, dtype=np.float64) / max(n - 1, 1)
    if not fade_in:
        ramp = 1.0 - ramp
    return (x * ramp.astype(np.float32)).astype(np.float32)


def reverse(x: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(x[:, ::-1])


def silence(channels: int, frames: int) -> np.ndarray:
    return np.zeros((channels, frames), dtype=np.float32)


def fit_channels(x: np.ndarray, channels: int) -> np.ndarray:
    """Pasting between mono and stereo: mono is copied to both sides, stereo is averaged."""
    have = x.shape[0]
    if have == channels:
        return x
    if channels == 1:
        return x.mean(axis=0, keepdims=True).astype(np.float32)
    return np.repeat(x[:1], channels, axis=0) if have == 1 else x[:channels]


def join(parts: list[np.ndarray]) -> np.ndarray:
    return np.ascontiguousarray(np.concatenate(parts, axis=1), dtype=np.float32)


def format_time(seconds: float) -> str:
    """Like 1:05.250 (m:ss.mmm), with hours when needed: 1:02:03.000."""
    ms = max(0, int(round(seconds * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms:03d}" if h else f"{m}:{s:02d}.{ms:03d}"
