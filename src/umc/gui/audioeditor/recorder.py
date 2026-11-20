"""Recording through Qt Multimedia: a QAudioSource reads the default microphone, and every
chunk is handed over as planar float32 (``chunk`` signal). The page collects them in a
``umc.audioedit.record.Take``; tests replace this class with a fake that emits chunks."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from umc.audioedit import record
from umc.gui.audioeditor.player import NO_ERROR, STOPPED

log = logging.getLogger(__name__)
TICK_MS = 50

_INT16 = QAudioFormat.SampleFormat.Int16
_FLOAT = QAudioFormat.SampleFormat.Float


class Recorder(QObject):
    chunk = Signal(object)  # planar float32 (channels × frames)
    failed = Signal(str)  # the device stopped by itself

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.source: QAudioSource | None = None
        self._io = None
        self.rate, self.channels, self._float = 0, 0, False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._read)

    @property
    def active(self) -> bool:
        return self.source is not None

    def start(self, rate: int, channels: int) -> str:
        """Start recording, at ``rate`` × ``channels`` if the microphone takes it (else its own
        format: see ``rate`` / ``channels`` afterwards). Returns "" or why it can't."""
        self.stop()
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            return record.NO_INPUT
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(channels)
        fmt.setSampleFormat(_INT16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
            if fmt.sampleFormat() not in (_INT16, _FLOAT):
                fmt.setSampleFormat(_INT16)
            log.info("microphone records %s Hz × %s instead", fmt.sampleRate(), fmt.channelCount())
        self.rate, self.channels = fmt.sampleRate(), max(1, fmt.channelCount())
        self._float = fmt.sampleFormat() == _FLOAT
        self.source = QAudioSource(device, fmt, self)
        self._io = self.source.start()
        if self._io is None or self.source.error() != NO_ERROR:
            self.stop()
            return record.NO_INPUT
        self._timer.start()
        return ""

    def stop(self) -> None:
        if self.source is None:
            return
        self._timer.stop()
        source, io = self.source, self._io
        self.source, self._io = None, None  # first: a chunk handler may call stop() again
        self._drain(io)  # what is still waiting
        source.stop()
        source.deleteLater()

    def _drain(self, io) -> None:
        if io is None:
            return
        raw = bytes(io.readAll())
        if raw:
            convert = record.from_float32 if self._float else record.from_int16
            x = convert(raw, self.channels)
            if x.shape[1]:
                self.chunk.emit(x)

    def _read(self) -> None:
        self._drain(self._io)
        # polled like the player: PySide6 6.12 can't deliver stateChanged
        if self.source is not None and self.source.state() == STOPPED:
            log.info("recording stopped by the device: %s", self.source.error())
            self.stop()
            self.failed.emit("The microphone stopped, so the recording ended.")
