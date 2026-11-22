"""Several tracks and their mix (port: web/src/audioedit/tracks.ts).

Every track keeps its own :class:`AudioDoc` (its own undo history); the editor edits one of
them at a time. All tracks share one sample rate (the first track's). The mix is the audible
tracks summed track by track: each fitted to the output channels, times its volume, times its
left / right balance (stereo output only). There is no limiter: :func:`peak_db` tells the page
when the mix goes over 0 dB.

Each track starts ``offset`` frames into the mix (dragged on its lane, or typed): before that
and after its end it is silent."""

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
    offset: int = 0  # where the track starts in the mix (frames, ≥ 0)

    @property
    def end(self) -> int:
        """Where the track ends in the mix (an empty track ends nowhere)."""
        return self.offset + self.doc.frames if self.doc.frames else 0


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
    """Stereo when any track is stereo or panned (a panned mono track must reach one side)."""
    if any(t.pan != 0 for t in tracks):
        return 2
    return max((t.doc.channels for t in tracks), default=1)


def length(tracks: list[Track]) -> int:
    """Frames of the mix: up to the end of the last audible track."""
    return max((t.end for t in audible(tracks)), default=0)


def extent(tracks: list[Track]) -> int:
    """Up to the end of the last track, heard or not (what the lanes and the waveform span)."""
    return max((t.end for t in tracks), default=0)


def mix(
    tracks: list[Track], start: int = 0, end: int | None = None, channels: int | None = None
) -> np.ndarray:
    """The audible tracks' frames [start, end) mixed; (channels, frames) float32.
    ``channels`` fixes the output (playback stays stereo while a slider moves)."""
    channels = out_channels(tracks) if channels is None else channels
    end = length(tracks) if end is None else end
    frames = max(0, end - start)
    out = np.zeros((channels, frames), dtype=np.float32)
    for t in audible(tracks):
        a, b = max(start, t.offset), min(end, t.offset + t.doc.frames)
        if b <= a:
            continue
        x = ops.fit_channels(t.doc.samples[:, a - t.offset : b - t.offset], channels)
        g = gain(t.gain_db)
        left, right = pan_gains(t.pan) if channels == 2 else (1.0, 1.0)
        for c in range(channels):
            k = np.float32(g * (left if c == 0 else right))
            out[c, a - start : b - start] += x[c] * k
    return out


def peak_db(samples: np.ndarray) -> float:
    """The loudest sample in dB (−inf for silence)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    return 20.0 * math.log10(peak) if peak > 0 else -math.inf


def changed(t: Track) -> bool:
    """A volume, pan, mute, solo or start away from the default (then even one track is mixed)."""
    return t.gain_db != 0 or t.pan != 0 or t.mute or t.solo or t.offset != 0


class LazyMix:
    """The mix of ``tracks`` [0, frames) as an array-like the player slices (``[:, a:b]``):
    each slice is mixed when it is read, so playing never holds the whole mix. The tracks are
    read as they are at that moment, so a volume / pan / mute / solo change is heard at once."""

    def __init__(self, tracks: list[Track], frames: int, channels: int | None = None) -> None:
        self.tracks = tracks
        self.shape = (out_channels(tracks) if channels is None else channels, frames)

    def __getitem__(self, key: tuple[slice, slice]) -> np.ndarray:
        part = key[1]
        a = part.start or 0
        b = self.shape[1] if part.stop is None else min(part.stop, self.shape[1])
        return mix(self.tracks, a, max(a, b), self.shape[0])


def total_samples(tracks: list[Track]) -> int:
    return sum(t.doc.channels * t.doc.frames for t in tracks)


def duplicate(t: Track, name: str) -> Track:
    """A copy of ``t`` with its own sound (no undo history), same settings and start."""
    doc = AudioDoc(t.doc.rate, t.doc.samples)
    return Track(doc, name, t.gain_db, t.pan, t.mute, t.solo, t.offset)


def copy_name(name: str, tracks: list[Track]) -> str:
    """ "Voice copy", then "Voice copy 2" … (never a name already used)."""
    used = {t.name for t in tracks}
    base = f"{name} copy"
    n, out = 2, base
    while out in used:
        out, n = f"{base} {n}", n + 1
    return out


def next_name(tracks: list[Track]) -> str:
    used = {t.name for t in tracks}
    n = len(tracks) + 1
    while f"Track {n}" in used:
        n += 1
    return f"Track {n}"
