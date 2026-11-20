"""Noise reduction for the audio editor (a spectral gate, the way Audacity does it).

1. ``learn`` a noise profile from a part with only background noise: the mean power of every
   frequency band (all channels together).
2. ``reduce`` another part: in each short block (``SIZE`` frames, ``HOP`` apart, Hann window),
   bands that aren't louder than the noise (+ ``sensitivity`` dB) are turned down by
   ``reduce_db``. A turned-down band comes back at once but fades down over ``RELEASE``
   seconds (no fluttering), and each gain is averaged with ``smoothing`` neighbouring bands.

``web/src/audioedit/noise.ts`` is a line-by-line port (own FFT there); both check
``tests/fixtures/audioedit_noise_cases.json``. Everything is computed in float64."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

import numpy as np

from umc.core.errors import ConversionCancelled

SIZE = 2048  # frames per block
HOP = SIZE // 4
BANDS = SIZE // 2 + 1
RELEASE = 0.1  # seconds for a band to fade down to the reduced level
CHUNK = 256  # blocks per progress / cancel step
LEARN_MAX_SECONDS = 30.0  # a longer noise sample only uses its first 30 s

REDUCE_MIN, REDUCE_MAX, REDUCE_DEFAULT = 1.0, 48.0, 12.0  # dB
SENSITIVITY_MIN, SENSITIVITY_MAX, SENSITIVITY_DEFAULT = 0.0, 24.0, 6.0  # dB
SMOOTHING_MIN, SMOOTHING_MAX, SMOOTHING_DEFAULT = 0, 12, 3  # bands on each side

MIN_NOISE_FRAMES = SIZE * 2
NO_PROFILE = "Select a part with only background noise first and press “Learn noise”."


def noise_too_short(rate: int) -> str:
    """The refusal for a too short noise part, naming the real minimum (rounded up to 0.1 s)."""
    tenths = max(1, math.ceil(MIN_NOISE_FRAMES * 10 / rate))
    return f"Select a longer part of only noise (at least {tenths // 10}.{tenths % 10} seconds)."


def window() -> np.ndarray:
    """Periodic Hann window."""
    n = np.arange(SIZE)
    return 0.5 - 0.5 * np.cos(2.0 * math.pi * n / SIZE)


def _blocks(x: np.ndarray, start: int, count: int) -> np.ndarray:
    """``count`` windowed blocks of the padded channel ``x``, the first at ``start * HOP``."""
    idx = (start + np.arange(count))[:, None] * HOP + np.arange(SIZE)[None, :]
    return x[idx] * window()[None, :]


def _padded(x: np.ndarray) -> tuple[np.ndarray, int]:
    """``x`` with SIZE zeros before and enough after; and the number of blocks."""
    frames = x.shape[-1]
    count = (frames + SIZE) // HOP + 1
    total = (count - 1) * HOP + SIZE
    out = np.zeros(total, np.float64)
    out[SIZE : SIZE + frames] = x
    return out, count


def learn(samples: np.ndarray, rate: int) -> np.ndarray:
    """Mean power per band of ``samples`` (channels × frames): the noise profile."""
    frames = min(samples.shape[1], math.floor(LEARN_MAX_SECONDS * rate))
    if frames < MIN_NOISE_FRAMES:
        raise ValueError(noise_too_short(rate))
    total = np.zeros(BANDS, np.float64)
    count = 0
    for ch in samples:
        x = np.asarray(ch[:frames], np.float64)
        n = (frames - SIZE) // HOP + 1  # whole blocks inside the sample only
        for s in range(0, n, CHUNK):
            k = min(CHUNK, n - s)
            idx = (s + np.arange(k))[:, None] * HOP + np.arange(SIZE)[None, :]
            spec = np.fft.rfft(x[idx] * window()[None, :], axis=1)
            total += (spec.real**2 + spec.imag**2).sum(axis=0)
        count += n
    return total / count


def _smooth(gains: np.ndarray, width: int) -> np.ndarray:
    """Each gain averaged with ``width`` bands on each side (fewer at the ends)."""
    if width <= 0:
        return gains
    c = np.concatenate([np.zeros((gains.shape[0], 1)), np.cumsum(gains, axis=1)], axis=1)
    lo = np.clip(np.arange(BANDS) - width, 0, BANDS)
    hi = np.clip(np.arange(BANDS) + width + 1, 0, BANDS)
    return (c[:, hi] - c[:, lo]) / (hi - lo)


def reduce(
    samples: np.ndarray,
    rate: int,
    profile: np.ndarray,
    reduce_db: float = REDUCE_DEFAULT,
    sensitivity_db: float = SENSITIVITY_DEFAULT,
    smoothing: int = SMOOTHING_DEFAULT,
    cancel_event: threading.Event | None = None,
    progress: Callable[[float], None] = lambda _f: None,
) -> np.ndarray:
    """``samples`` (channels × frames) with the noise in ``profile`` turned down."""
    reduce_db = max(REDUCE_MIN, min(REDUCE_MAX, reduce_db))
    sensitivity_db = max(SENSITIVITY_MIN, min(SENSITIVITY_MAX, sensitivity_db))
    smoothing = int(max(SMOOTHING_MIN, min(SMOOTHING_MAX, smoothing)))
    floor = 10.0 ** (-reduce_db / 20.0)
    threshold = np.asarray(profile, np.float64) * 10.0 ** (sensitivity_db / 10.0)
    fall = floor ** (HOP / (RELEASE * rate))
    win = window()
    channels, frames = samples.shape
    out = np.zeros((channels, frames), np.float32)
    steps = 0
    total_steps = channels * math.ceil(((frames + SIZE) // HOP + 1) / CHUNK)
    for c in range(channels):
        x, count = _padded(np.asarray(samples[c], np.float64))  # one channel at a time (memory)
        acc = np.zeros_like(x)
        held = np.full(BANDS, floor)
        for s in range(0, count, CHUNK):
            if cancel_event is not None and cancel_event.is_set():
                raise ConversionCancelled()
            k = min(CHUNK, count - s)
            spec = np.fft.rfft(_blocks(x, s, k), axis=1)
            power = spec.real**2 + spec.imag**2
            raw = np.where(power > threshold[None, :], 1.0, floor)
            gains = np.empty_like(raw)
            for i in range(k):
                held = np.maximum(raw[i], np.maximum(held * fall, floor))
                gains[i] = held
            back = np.fft.irfft(spec * _smooth(gains, smoothing), n=SIZE, axis=1) * win[None, :]
            for i in range(k):
                at = (s + i) * HOP
                acc[at : at + SIZE] += back[i]
            steps += 1
            progress(steps / total_steps)
        out[c] = acc[SIZE : SIZE + frames] / 1.5  # Hann² at 75 % overlap sums to 1.5
    return out
