"""Playback through Qt Multimedia: a QAudioSink pulls 16-bit samples from the document on demand
(no copy of the whole sound), and a timer reports the play head."""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from umc.audioedit.document import AudioDoc

log = logging.getLogger(__name__)

NO_DEVICE = "No sound output was found, so the sound can't be played."
TICK_MS = 30


class _Source(QIODevice):
    """Frames [start, end) of ``samples`` as interleaved little-endian int16."""

    def __init__(self, samples: np.ndarray, start: int, end: int) -> None:
        super().__init__()
        self.samples, self.pos, self.end = samples, start, end
        self.channels = samples.shape[0]

    def readData(self, maxlen: int) -> bytes:  # noqa: N802
        n = min(maxlen // (2 * self.channels), self.end - self.pos)
        if n <= 0:
            return b""
        chunk = self.samples[:, self.pos : self.pos + n]
        self.pos += n
        v = np.clip(np.round(chunk.T * 32767.0), -32768, 32767).astype("<i2")
        return np.ascontiguousarray(v).tobytes()

    def writeData(self, _data) -> int:  # noqa: N802
        return -1

    def bytesAvailable(self) -> int:  # noqa: N802
        return max(0, self.end - self.pos) * 2 * self.channels + super().bytesAvailable()

    def isSequential(self) -> bool:  # noqa: N802
        return True


class Player(QObject):
    position_changed = Signal(int)  # play head, in frames
    finished = Signal()  # reached the end, or stopped

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.sink: QAudioSink | None = None
        self.source: _Source | None = None
        self.origin = 0
        self.rate = 1
        self.paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self.sink is not None

    def play(self, doc: AudioDoc, start: int, end: int) -> str:
        """Start playing frames [start, end). Returns "" or why it can't play."""
        self.stop()
        if end <= start:
            return ""
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            return NO_DEVICE
        fmt = QAudioFormat()
        fmt.setSampleRate(doc.rate)
        fmt.setChannelCount(doc.channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            log.info("audio output doesn't take %s Hz × %s", doc.rate, doc.channels)
        self.source = _Source(doc.samples, start, end)
        self.source.open(QIODevice.OpenModeFlag.ReadOnly)
        self.sink = QAudioSink(device, fmt, self)
        self.sink.stateChanged.connect(self._on_state)
        self.origin, self.rate, self.paused = start, doc.rate, False
        self.sink.start(self.source)
        if self.sink.error() != QAudio.Error.NoError:
            self.stop()
            return NO_DEVICE
        self._timer.start()
        return ""

    def position(self) -> int:
        """The frame being heard (what has been handed to the device minus its buffer)."""
        if self.sink is None:
            return self.origin
        heard = self.sink.processedUSecs() * self.rate // 1_000_000
        queued = (self.sink.bufferSize() - self.sink.bytesFree()) // (2 * self.source.channels)
        return int(self.origin + max(0, heard - max(0, queued)))

    def pause(self) -> None:
        if self.sink is not None and not self.paused:
            self.sink.suspend()
            self.paused = True

    def resume(self) -> None:
        if self.sink is not None and self.paused:
            self.sink.resume()
            self.paused = False

    def stop(self) -> None:
        if self.sink is None:
            return
        self._timer.stop()
        sink, self.sink = self.sink, None
        sink.stateChanged.disconnect(self._on_state)
        sink.stop()
        sink.deleteLater()
        if self.source is not None:
            self.source.close()
            self.source = None
        self.paused = False
        self.finished.emit()

    def _tick(self) -> None:
        if self.sink is not None:
            self.position_changed.emit(self.position())

    def _on_state(self, state) -> None:
        # Idle = the source ran dry: everything has been played
        if state == QAudio.State.IdleState and self.source is not None:
            if self.source.pos >= self.source.end:
                self.stop()
        elif (
            state == QAudio.State.StoppedState
            and self.sink is not None
            and self.sink.error() != QAudio.Error.NoError
        ):
            log.info("audio playback stopped: %s", self.sink.error())
            self.stop()
