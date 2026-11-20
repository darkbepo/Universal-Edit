"""The spectrogram view: how loud each frequency is, per screen column (port:
web/src/audioedit/spectrum.ts; both check tests/fixtures/audioedit_spectrum_cases.json).

Column i is one Hann-windowed block of ``noise.SIZE`` frames centred on the column's middle
(zeros past either end of the sound). Its bands are grouped into ``rows`` rows on a log
frequency scale from ``F_MIN`` to half the sample rate (each row ≥ one band; the loudest band
counts) and turned into a level 0…1: ``FLOOR_DB`` dB (or quieter) … 0 dB, where 0 dB is a
full-scale sine. Row 0 is the lowest frequency (the bottom of the lane)."""

from __future__ import annotations

import math

import numpy as np

from umc.audioedit.noise import BANDS, SIZE, window

F_MIN = 30.0
FLOOR_DB = -100.0
MAX_ROWS = 512
MAX_COLUMNS = 800  # wider views compute one column per `column_step` pixels and stretch it
LABELS = (100, 1000, 10000)  # Hz marks drawn at the lane's edge
# colour ramp: quiet → loud (dark blue, purple, red, orange, pale yellow)
RAMP = ((12, 7, 48), (88, 24, 128), (190, 45, 92), (246, 128, 32), (252, 238, 160))


def row_bands(rate: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
    """(first, end) band of every row: row r reads bands [first[r], end[r]). Low rows can
    share one band (there are few bands down there), so the frequency scale stays right."""
    rows = max(1, min(MAX_ROWS, rows))
    top = rate / 2
    lo = min(F_MIN, top / 2)
    hz = lo * (top / lo) ** (np.arange(rows + 1, dtype=np.float64) / rows)
    edges = np.minimum(np.floor(hz * SIZE / rate).astype(np.int64), BANDS - 1)
    first = edges[:-1]
    end = np.maximum(edges[1:], first + 1)
    end[-1] = BANDS
    return first, end


def column_step(width: int) -> int:
    """Screen pixels per computed column, so at most MAX_COLUMNS are computed."""
    return max(1, math.ceil(width / MAX_COLUMNS))


def row_of(hz: float, rate: int, rows: int) -> float:
    """Where `hz` sits on the row scale (0 = bottom edge, rows = top edge)."""
    top = rate / 2
    lo = min(F_MIN, top / 2)
    return rows * math.log(max(hz, lo) / lo) / math.log(top / lo)


def columns(
    samples: np.ndarray, rate: int, start: float, frames_per_px: float, width: int, rows: int
) -> np.ndarray:
    """Levels 0…1 as (channels, k, rows); k ≤ width (columns past the end are left out)."""
    ch, n = samples.shape
    rows = max(1, min(MAX_ROWS, rows))
    if width <= 0 or n == 0 or start >= n:
        return np.zeros((ch, 0, rows), dtype=np.float32)
    first = np.floor(start + np.arange(width, dtype=np.float64) * frames_per_px)
    k = int(np.searchsorted(first, n))
    centre = np.floor(start + (np.arange(k, dtype=np.float64) + 0.5) * frames_per_px).astype(
        np.int64
    )
    idx = centre[:, None] - SIZE // 2 + np.arange(SIZE)[None, :]
    inside = (idx >= 0) & (idx < n)
    idx = np.clip(idx, 0, n - 1)
    win = window()
    scale = 2 / win.sum()  # a full-scale sine → magnitude 1
    first_band, end_band = row_bands(rate, rows)
    out = np.empty((ch, k, rows), dtype=np.float32)
    for c in range(ch):
        x = np.where(inside, samples[c].astype(np.float64)[idx], 0.0) * win[None, :]
        mag = np.abs(np.fft.rfft(x, axis=1)) * scale
        peak = np.empty((k, rows), dtype=np.float64)
        for r in range(rows):
            peak[:, r] = mag[:, first_band[r] : end_band[r]].max(axis=1)
        db = 20 * np.log10(np.maximum(peak, 1e-12))
        out[c] = np.clip((db - FLOOR_DB) / -FLOOR_DB, 0, 1)
    return out


def colours(levels: np.ndarray) -> np.ndarray:
    """uint8 RGB for levels 0…1 (any shape + a last axis of 3), along RAMP."""
    ramp = np.asarray(RAMP, dtype=np.float64)
    t = np.clip(levels.astype(np.float64), 0, 1) * (len(RAMP) - 1)
    i = np.minimum(np.floor(t).astype(np.int64), len(RAMP) - 2)
    f = (t - i)[..., None]
    return np.round(ramp[i] * (1 - f) + ramp[i + 1] * f).astype(np.uint8)
