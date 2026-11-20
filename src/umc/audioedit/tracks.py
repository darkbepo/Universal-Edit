"""Several tracks and their mix (port: web/src/audioedit/tracks.ts).

Every track keeps its own :class:`AudioDoc` (its own undo history); the editor edits one of
them at a time. All tracks share one sample rate (the first track's). The mix is the audible
tracks summed track by track: each fitted to the output channels, times its volume, times its
left / right balance (stereo output only). There is no limiter: :func:`peak_db` tells the page
when the mix goes over 0 dB."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from umc.audioedit import ops
from umc.audioedit.document import AudioDoc

MIN_GAIN_DB = -24.0
MAX_GAIN_DB = 12.0
LOUD = "The mix peaks at {db} — turn a track down, or it will sound distorted."


@dataclass
class Track:
    doc: AudioDoc
    name: str
    gain_db: float = 0.0
    pan: float = 0.0  # −1 = left only … 0 = centre … 1 = right only
    mute: bool = False
    solo: bool = False


def audible(tracks: list[Track]) -> list[Track]:
    """The tracks you hear: the soloed ones if any are, never the muted ones."""
    soloed = any(t.solo for t in tracks)
    return [t for t in tracks if not t.mute and (t.solo or not soloed)]


def pan_gains(pan: float) -> tuple[float, float]:
    """Balance law: centre plays both sides at full, so a centred track is as loud as alone."""
    pan = max(-1.0, min(1.0, pan))
    return min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)


def gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def out_channels(tracks: list[Track]) -> int:
    return max((t.doc.channels for t in tracks), default=1)


def length(tracks: list[Track]) -> int:
    """Frames of the mix: the longest audible track."""
    return max((t.doc.frames for t in audible(tracks)), default=0)


def mix(tracks: list[Track], start: int = 0, end: int | None = None) -> np.ndarray:
    """The audible tracks' frames [start, end) mixed; (channels, frames) float32."""
    channels = out_channels(tracks)
    end = length(tracks) if end is None else end
    frames = max(0, end - start)
    out = np.zeros((channels, frames), dtype=np.float32)
    for t in audible(tracks):
        b = min(end, t.doc.frames)
        if b <= start:
            continue
        x = ops.fit_channels(t.doc.samples[:, start:b], channels)
        g = gain(t.gain_db)
        left, right = pan_gains(t.pan) if channels == 2 else (1.0, 1.0)
        for c in range(channels):
            k = np.float32(g * (left if c == 0 else right))
            out[c, : b - start] += x[c] * k
    return out


def peak_db(samples: np.ndarray) -> float:
    """The loudest sample in dB (−inf for silence)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    return 20.0 * math.log10(peak) if peak > 0 else -math.inf


def changed(t: Track) -> bool:
    """A volume, pan, mute or solo away from the default (then even one track is mixed)."""
    return t.gain_db != 0 or t.pan != 0 or t.mute or t.solo


class LazyMix:
    """The mix of ``tracks`` [0, frames) as an array-like the player slices (``[:, a:b]``):
    each slice is mixed when it is read, so playing never holds the whole mix."""

    def __init__(self, tracks: list[Track], frames: int) -> None:
        self.tracks = tracks
        self.shape = (out_channels(tracks), frames)

    def __getitem__(self, key: tuple[slice, slice]) -> np.ndarray:
        part = key[1]
        a = part.start or 0
        b = self.shape[1] if part.stop is None else min(part.stop, self.shape[1])
        return mix(self.tracks, a, max(a, b))


def total_samples(tracks: list[Track]) -> int:
    return sum(t.doc.channels * t.doc.frames for t in tracks)


def next_name(tracks: list[Track]) -> str:
    used = {t.name for t in tracks}
    n = len(tracks) + 1
    while f"Track {n}" in used:
        n += 1
    return f"Track {n}"
