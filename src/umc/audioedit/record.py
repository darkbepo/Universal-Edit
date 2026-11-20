"""Recording from the microphone, the part that isn't Qt (port: web/src/audioedit/record.ts).

A ``Take`` collects the chunks the device hands over (planar float32) up to a frame limit, and
knows the loudest sample of the last chunk for the level meter. When it ends, the take is fitted
to the sound's channels (and, by the caller, its sample rate) and goes in at the cursor, or in
place of the selection, as one undo step called ``LABEL``."""

from __future__ import annotations

import numpy as np

LABEL = "Record"
NO_INPUT = "No microphone was found, so nothing can be recorded."
NO_ROOM = "The sound is as long as the audio editor allows, so nothing more can be recorded."
FULL = "Recording stopped: the sound is as long as the audio editor allows."
NEW_RATE = 48_000  # an empty editor records at this rate (if the device takes it) …
NEW_CHANNELS = 2  # … and channel count


def room(max_samples: int, doc_frames: int, doc_channels: int, selected: int, channels: int) -> int:
    """How many frames a take may have so the sound stays within ``max_samples``: the
    selection is replaced, and the take ends up with the sound's channels (or its own when
    there's no sound yet, doc_channels = 0)."""
    width = doc_channels or channels
    return max(0, max_samples // max(1, width) - (doc_frames - selected))


def from_int16(raw: bytes, channels: int) -> np.ndarray:
    """Interleaved little-endian int16 → planar float32 (−1…1)."""
    v = np.frombuffer(raw[: len(raw) - len(raw) % (2 * channels)], dtype="<i2")
    return np.ascontiguousarray(v.reshape(-1, channels).T, dtype=np.float32) / 32768.0


def from_float32(raw: bytes, channels: int) -> np.ndarray:
    """Interleaved little-endian float32 → planar float32."""
    v = np.frombuffer(raw[: len(raw) - len(raw) % (4 * channels)], dtype="<f4")
    return np.ascontiguousarray(v.reshape(-1, channels).T, dtype=np.float32)


class Take:
    def __init__(self, rate: int, channels: int, max_frames: int) -> None:
        self.rate, self.channels, self.max_frames = rate, channels, max_frames
        self.frames = 0
        self.level = 0.0  # loudest sample of the last chunk, 0…1
        self._chunks: list[np.ndarray] = []

    @property
    def full(self) -> bool:
        return self.frames >= self.max_frames

    @property
    def seconds(self) -> float:
        return self.frames / self.rate

    def add(self, chunk: np.ndarray) -> bool:
        """Add planar samples; False once the take is full (the rest is dropped)."""
        chunk = chunk[:, : max(0, self.max_frames - self.frames)]
        if chunk.shape[1]:
            self._chunks.append(np.array(chunk, dtype=np.float32))
            self.frames += chunk.shape[1]
            self.level = min(1.0, float(np.abs(chunk).max()))
        return not self.full

    def samples(self) -> np.ndarray:
        """Everything recorded, planar float32 (channels × frames)."""
        if not self._chunks:
            return np.zeros((self.channels, 0), np.float32)
        return np.concatenate(self._chunks, axis=1)
