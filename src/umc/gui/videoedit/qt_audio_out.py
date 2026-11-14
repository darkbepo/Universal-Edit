"""The preview's sound through Qt Multimedia: a pull-mode QAudioSink reads a locked buffer.

The sink and ``_Feed`` live on the GUI thread; the feeder thread only calls ``write`` (which
touches the buffer under a lock) and never the sink.
"""

from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QIODevice, QMetaObject, QObject, Qt, QThread, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from umc.videoedit.audio_out import (
    CHANNELS,
    FRAME_BYTES,
    MAX_QUEUED,
    SAMPLE_RATE,
    AudioOut,
    NullAudioOut,
)

log = logging.getLogger(__name__)


class _Feed(QIODevice):
    """Sequential, read-only: bytes pushed by the feeder thread, read by the sink."""

    _arrived = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Condition()
        self._buf = bytearray()
        self._closed = False
        self._starved = True  # the last read came up short, so the sink waits for readyRead
        self._arrived.connect(self._announce, Qt.ConnectionType.QueuedConnection)
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    @Slot()
    def _announce(self) -> None:
        self.readyRead.emit()

    def push(self, chunk: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            self._buf += chunk
            wake = self._starved
            self._starved = False
        if wake:
            self._arrived.emit()

    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._buf)

    def close_feed(self) -> None:
        with self._lock:
            self._closed = True
            self._buf.clear()
            self._lock.notify_all()

    def wait_below(self, limit: int, stop) -> None:  # noqa: ANN001
        """Block while more than ``limit`` bytes are queued and ``stop()`` is false."""
        with self._lock:
            while len(self._buf) > limit and not stop():
                self._lock.wait(timeout=0.05)

    def wake(self) -> None:
        with self._lock:
            self._lock.notify_all()

    def readData(self, maxlen: int) -> bytes:  # noqa: N802
        with self._lock:
            take = min(maxlen, len(self._buf))
            take -= take % FRAME_BYTES
            if take <= 0:
                self._starved = True
                return b""
            data = bytes(self._buf[:take])
            del self._buf[:take]
            if not self._buf:  # emptied: the sink may go idle without reading again
                self._starved = True
            self._lock.notify_all()
            return data

    def writeData(self, _data) -> int:  # noqa: N802
        return -1

    def bytesAvailable(self) -> int:  # noqa: N802
        return self.queued_bytes() + super().bytesAvailable()

    def isSequential(self) -> bool:  # noqa: N802
        return True


class QtAudioOut(QObject):
    """``AudioOut`` on a QAudioSink. Create it on the GUI thread."""

    silent = False

    def __init__(self, feed: _Feed, sink: QAudioSink | None) -> None:
        super().__init__()
        self._feed = feed
        self._sink = sink
        self._closed = False
        self._dead = False  # the device failed: nothing drains the queue any more
        self._last = 0.0
        self._owner = QThread.currentThread()
        if sink is not None:
            sink.stateChanged.connect(self._on_state)

    @classmethod
    def _for_test(cls, feed: _Feed) -> QtAudioOut:
        return cls(feed, None)

    def _on_state(self, state) -> None:  # noqa: ANN001
        sink = self._sink
        if (
            state == QtAudio.State.StoppedState
            and sink is not None
            and sink.error() != QtAudio.Error.NoError
        ):
            log.info("preview sound stopped: %s", sink.error())
            self._dead = True
            self._feed.wake()

    @property
    def dead(self) -> bool:
        """The device failed: nothing is played any more and ``write`` drops the sound."""
        return self._dead

    def write(self, chunk: bytes) -> None:
        limit = int(MAX_QUEUED * SAMPLE_RATE) * FRAME_BYTES
        self._feed.wait_below(limit, lambda: self._closed or self._dead)
        if not self._closed and not self._dead:
            self._feed.push(chunk)

    def played_seconds(self) -> float:
        """Seconds heard: what the sink has processed minus what is still in its buffer."""
        sink = self._sink
        if sink is None or self._closed or QThread.currentThread() is not self._owner:
            return self._last
        heard = sink.processedUSecs() / 1_000_000
        queued = max(0, sink.bufferSize() - sink.bytesFree()) / (FRAME_BYTES * SAMPLE_RATE)
        self._last = max(0.0, heard - queued)
        return self._last

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._feed.close_feed()
        if QThread.currentThread() is self.thread():
            self._stop_sink()
        else:
            QMetaObject.invokeMethod(self, "_stop_sink", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _stop_sink(self) -> None:
        sink, self._sink = self._sink, None
        if sink is None:
            return
        try:
            sink.stateChanged.disconnect(self._on_state)
            sink.stop()
        except Exception:
            log.warning("Couldn't stop the sound output", exc_info=True)
        sink.deleteLater()


def open_qt_output() -> AudioOut:
    """A Qt sound output (call on the GUI thread), or a silent one; the reason is logged."""
    if os.environ.get("UMC_NO_AUDIO"):
        log.info("Sound output disabled (UMC_NO_AUDIO)")
        return NullAudioOut()
    try:
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            log.warning("No sound output device; preview plays without sound")
            return NullAudioOut()
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        feed = _Feed()
        sink = QAudioSink(device, fmt)
        sink.start(feed)
        if sink.error() != QtAudio.Error.NoError:
            log.warning("Couldn't open the sound output (%s); preview is silent", sink.error())
            sink.stop()
            sink.deleteLater()
            return NullAudioOut()
        return QtAudioOut(feed, sink)
    except Exception:
        log.warning("Couldn't open the sound output; preview plays without sound", exc_info=True)
        return NullAudioOut()
