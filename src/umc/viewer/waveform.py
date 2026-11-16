"""The viewer's waveform for a song (no Qt): FFmpeg decodes a low-rate mono copy, and each of
``columns`` slices becomes its loudest level (0…1).

Only a picture is needed, so 2000 samples a second are plenty and keep an hour-long file at
~29 MB of floats while it's read."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

RATE = 2000  # samples per second decoded for the picture
COLUMNS = 1200  # bars the waveform is made of (drawn scaled to the widget's width)


def levels_from_samples(samples: np.ndarray, columns: int = COLUMNS) -> list[float]:
    """Peak level (0…1) of each of ``columns`` equal slices; louder parts are lifted a little
    (square root) so quiet songs still show a shape. Scaled so the loudest bar is 1."""
    samples = np.abs(np.asarray(samples, dtype=np.float32).ravel())
    if samples.size == 0 or columns <= 0:
        return []
    columns = min(columns, samples.size)
    edges = np.linspace(0, samples.size, columns + 1).astype(np.int64)
    peaks = np.maximum.reduceat(samples, edges[:-1])
    top = float(peaks.max())
    if top <= 0:
        return [0.0] * columns
    return [round(float(v), 4) for v in np.sqrt(np.clip(peaks / top, 0.0, 1.0))]


def levels(
    path: Path, columns: int = COLUMNS, cancel: threading.Event | None = None
) -> list[float]:
    """The waveform of the sound in ``path`` (a song, or a video's sound)."""
    from umc.tools import ffmpeg

    raw = ffmpeg.ffmpeg_bytes(
        ["-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        timeout=300.0,
        cancel_event=cancel,
        error_message="Couldn't read the sound.",
    )
    return levels_from_samples(np.frombuffer(raw, dtype="<f4"), columns)
