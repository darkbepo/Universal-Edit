"""The waveform's min / max per screen column (port: web/src/audioedit/peaks.ts).

A summary of min / max per ``BLOCK`` frames is built once per change; zoomed out, columns are
read from it, zoomed in (fewer than ``BLOCK`` frames per column) from the samples."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BLOCK = 256


@dataclass
class Peaks:
    mins: np.ndarray  # (channels, blocks)
    maxs: np.ndarray


def build(samples: np.ndarray) -> Peaks:
    ch, n = samples.shape
    blocks = -(-n // BLOCK)
    if blocks == 0:
        empty = np.zeros((ch, 0), dtype=np.float32)
        return Peaks(empty, empty.copy())
    pad = blocks * BLOCK - n
    if pad:  # repeat the last sample: it changes neither min nor max
        samples = np.concatenate((samples, np.repeat(samples[:, -1:], pad, axis=1)), axis=1)
    r = samples.reshape(ch, blocks, BLOCK)
    return Peaks(r.min(axis=2), r.max(axis=2))


def columns(
    samples: np.ndarray, peaks: Peaks, start: float, frames_per_px: float, width: int
) -> tuple[np.ndarray, np.ndarray]:
    """(mins, maxs), each (channels, k): column i covers frames
    [start + i·fpp, start + (i+1)·fpp). k ≤ width: columns past the end are left out."""
    ch, n = samples.shape
    if width <= 0 or n == 0 or start >= n:
        z = np.zeros((ch, 0), dtype=np.float32)
        return z, z.copy()
    edges = start + np.arange(width + 1, dtype=np.float64) * frames_per_px
    first = np.floor(edges[:-1]).astype(np.int64)
    k = int(np.searchsorted(first, n))  # columns that start inside the sound
    first = np.clip(first[:k], 0, n - 1)
    if frames_per_px >= BLOCK:
        idx = np.minimum(first // BLOCK, peaks.mins.shape[1] - 1)
        mins = np.minimum.reduceat(peaks.mins, idx, axis=1)
        maxs = np.maximum.reduceat(peaks.maxs, idx, axis=1)
        # a block shared by two columns (the next column starts inside it) counts for both
        shared = (first[1:] % BLOCK) != 0
        nxt = idx[1:][shared]
        cols = np.nonzero(shared)[0]
        mins[:, cols] = np.minimum(mins[:, cols], peaks.mins[:, nxt])
        maxs[:, cols] = np.maximum(maxs[:, cols], peaks.maxs[:, nxt])
    else:
        mins = np.minimum.reduceat(samples, first, axis=1)
        maxs = np.maximum.reduceat(samples, first, axis=1)
    # reduceat runs the last column to the end of the array: limit it to its own width
    last_end = min(n, int(np.ceil(edges[k])))
    a = int(first[-1])
    if frames_per_px >= BLOCK:
        lo, hi = a // BLOCK, max(a // BLOCK + 1, -(-last_end // BLOCK))
        mins[:, -1] = peaks.mins[:, lo:hi].min(axis=1)
        maxs[:, -1] = peaks.maxs[:, lo:hi].max(axis=1)
    else:
        hi = max(a + 1, last_end)
        mins[:, -1], maxs[:, -1] = samples[:, a:hi].min(axis=1), samples[:, a:hi].max(axis=1)
    return mins, maxs
