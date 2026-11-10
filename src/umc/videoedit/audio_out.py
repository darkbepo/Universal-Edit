"""Sound output for the preview: 48 kHz stereo s16le. No Qt.

``sounddevice`` is imported only when a real output is opened, so the editor still works
(silently, on a wall clock) without it or without a sound card.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Protocol

log = logging.getLogger(__name__)

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


class SoundDeviceOut:
    """The default output device through PortAudio (``sounddevice``)."""

    silent = False

    def __init__(self) -> None:
        import sounddevice

        self._buf = bytearray()
        self._cond = threading.Condition()
        self._played = 0  # frames really played
        self._closed = False
        self._stream = sounddevice.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=self._callback
        )
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status) -> None:  # noqa: ANN001
        need = frames * FRAME_BYTES
        with self._cond:
            take = min(need, len(self._buf))
            take -= take % FRAME_BYTES
            outdata[:take] = self._buf[:take]
            del self._buf[:take]
            self._played += take // FRAME_BYTES
            self._cond.notify_all()
        if take < need:  # underrun: silence, and the clock waits
            outdata[take:need] = bytes(need - take)

    def write(self, chunk: bytes) -> None:
        limit = int(MAX_QUEUED * SAMPLE_RATE) * FRAME_BYTES
        with self._cond:
            while not self._closed and len(self._buf) > limit:
                self._cond.wait(timeout=0.1)
            if not self._closed:
                self._buf += chunk

    def played_seconds(self) -> float:
        with self._cond:
            return self._played / SAMPLE_RATE

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._buf.clear()
            self._cond.notify_all()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            log.warning("Couldn't close the sound output", exc_info=True)


def open_output() -> AudioOut:
    """A real sound output if possible, otherwise a silent one (the reason is logged)."""
    if os.environ.get("UMC_NO_AUDIO"):
        log.info("Sound output disabled (UMC_NO_AUDIO)")
        return NullAudioOut()
    try:
        import sounddevice
    except Exception:
        log.warning("sounddevice is not available; preview plays without sound", exc_info=True)
        return NullAudioOut()
    try:
        sounddevice.query_devices(kind="output")
    except Exception:
        log.warning("No sound output device; preview plays without sound", exc_info=True)
        return NullAudioOut()
    try:
        return SoundDeviceOut()
    except Exception:
        log.warning("Couldn't open the sound output; preview plays without sound", exc_info=True)
        return NullAudioOut()
