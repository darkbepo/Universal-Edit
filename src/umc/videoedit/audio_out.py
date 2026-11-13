"""Sound output for the preview: 48 kHz stereo s16le. No Qt.

The real output is in ``umc.gui.videoedit.qt_audio_out``; without a sound card the
editor plays silently on a wall clock (``NullAudioOut``).
"""

from __future__ import annotations

import time
from typing import Protocol

SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_BYTES = CHANNELS * 2  # int16
MAX_QUEUED = 0.5  # seconds; write() blocks while more than this is waiting


class AudioOut(Protocol):
    silent: bool

    def write(self, chunk: bytes) -> None: ...

    def played_seconds(self) -> float: ...

    def close(self) -> None: ...


class NullAudioOut:
    """Plays nothing; the clock runs from the first ``write``."""

    silent = True

    def __init__(self) -> None:
        self._started: float | None = None
        self._written = 0  # frames
        self._closed = False

    def write(self, chunk: bytes) -> None:
        if self._started is None:
            self._started = time.monotonic()
        self._written += len(chunk) // FRAME_BYTES
        while not self._closed and self._written / SAMPLE_RATE - self.played_seconds() > MAX_QUEUED:
            time.sleep(0.01)

    def played_seconds(self) -> float:
        if self._started is None:
            return 0.0
        return time.monotonic() - self._started

    def close(self) -> None:
        self._closed = True


def open_output() -> AudioOut:
    """The fallback output: silent, on a wall clock. The real one is the Qt output
    (``umc.gui.videoedit.qt_audio_out.open_qt_output``)."""
    return NullAudioOut()
