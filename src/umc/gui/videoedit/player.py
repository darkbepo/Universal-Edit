"""Playback of the preview with sound, and the background thumbnail / waveform scanner."""

from __future__ import annotations

import contextlib
import logging
import math
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from umc.gui.videoedit.preview_view import rgb_image
from umc.videoedit import audio_out, preview
from umc.videoedit.project import Project, Source, length

log = logging.getLogger(__name__)

MAX_QUEUED_FRAMES = 12
TICK_MS = 16
JOIN_SECONDS = 1.0
NO_SOUND = "No sound device — preview is silent"

THUMB_HEIGHT = 56
THUMB_EVERY_PX = 120
THUMB_MAX = 40
THUMB_PPS = 80.0  # the timeline's default zoom
WAVE_PER_SECOND = 50
WAVE_MAX = 4000

_STRAGGLERS: list[object] = []  # threads that outlived their owner; kept until they end


def _forget(obj: object) -> None:
    with contextlib.suppress(ValueError):
        _STRAGGLERS.remove(obj)


class _Run:
    """One playback from ``t0``: the streams, the sound output and the two worker threads."""

    def __init__(self, project: Project, t0: float, tmp: Path) -> None:
        self.t0 = t0
        self.stop = threading.Event()
        self.frames: queue.Queue = queue.Queue(MAX_QUEUED_FRAMES)  # (t, bytes), None at the end
        self.error: str | None = None
        self.feeder_done = False
        self._last = t0  # last sound clock reading and when it last moved
        self._moved = time.monotonic()
        self.streams = preview.PlaybackStreams(project, t0, tmp)
        self.size = self.streams.size
        try:
            self.out = audio_out.open_output()
        except Exception:
            self.streams.close()
            raise
        self.reader = threading.Thread(target=self._read, name="umc-vedit-frames", daemon=True)
        self.feeder = threading.Thread(target=self._feed, name="umc-vedit-sound", daemon=True)

    def start(self) -> None:
        self.reader.start()
        self.feeder.start()

    def _put(self, item: object) -> bool:
        while not self.stop.is_set():
            try:
                self.frames.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _read(self) -> None:
        try:
            for item in self.streams.frames():
                if not self._put(item):
                    return
        except Exception:
            if not self.stop.is_set():
                log.exception("preview playback failed")
                self.error = preview.ERROR
        self._put(None)
        _forget(threading.current_thread())

    def _feed(self) -> None:
        try:
            for chunk in self.streams.audio():
                if self.stop.is_set():
                    return
                self.out.write(chunk)
        except Exception:
            if not self.stop.is_set():
                log.exception("preview sound failed")
                self.error = self.error or preview.ERROR
        finally:
            self.feeder_done = True
            _forget(threading.current_thread())

    def clock(self) -> float:
        """Where playback is: the sound's clock; once no more sound comes (the end of the
        stream, a failure) the clock goes on by itself, since a device clock stops then."""
        audio = self.t0 + self.out.played_seconds()
        now = time.monotonic()
        if audio > self._last + 1e-9:
            self._last, self._moved = audio, now
        if self.feeder_done:
            return max(audio, self._last + (now - self._moved))
        return audio

    def shut_down(self) -> None:
        """Stop everything; waits at most about a second for the threads."""
        self.stop.set()
        self.streams.close()
        self.out.close()
        deadline = time.monotonic() + JOIN_SECONDS
        for th in (self.reader, self.feeder):
            if th.is_alive():
                th.join(max(0.0, deadline - time.monotonic()))
            if th.is_alive():
                log.warning("Preview thread %s didn't stop in time", th.name)
                _STRAGGLERS.append(th)
                if not th.is_alive():
                    _forget(th)


class Player(QObject):
    """Plays a project from a time: frames on a clock driven by the sound output."""

    frame = Signal(QImage)
    position = Signal(float)
    finished = Signal()
    notice = Signal(str)  # information
    failed = Signal(str)  # an error to show as one

    def __init__(self, tmp: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tmp = Path(tmp)
        self.loop = False
        self.total = 0.0  # length of the project being played (computed once per play)
        self._fps = 30.0
        self._last_pos = -1.0
        self._run: _Run | None = None
        self._project: Project | None = None
        self._held: tuple[float, bytes] | None = None
        self._announced = False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def is_playing(self) -> bool:
        return self._run is not None

    def play(self, project: Project, t: float) -> None:
        self.stop()
        t = max(0.0, min(float(t), length(project)))
        try:
            run = _Run(project, t, self.tmp)
        except Exception:
            log.exception("couldn't start the preview playback")
            self.failed.emit(preview.ERROR)
            return
        self.total = length(project)
        self._fps = run.streams.fps
        self._last_pos = -1.0
        self._project, self._run, self._held = project, run, None
        if run.out.silent and not os.environ.get("UMC_NO_AUDIO") and not self._announced:
            self._announced = True
            self.notice.emit(NO_SOUND)
        run.start()
        self._timer.start()

    def stop(self) -> None:
        run, self._run = self._run, None
        self._timer.stop()
        self._held = None
        if run is not None:
            run.shut_down()

    def _tick(self) -> None:
        run, project = self._run, self._project
        if run is None or project is None:
            return
        clock = run.clock()
        latest: tuple[float, bytes] | None = None
        ended = False
        while True:
            if self._held is None:
                try:
                    item = run.frames.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    ended = True
                    break
                self._held = item
            if self._held[0] <= clock:
                latest, self._held = self._held, None
            else:
                break
        if latest is not None:
            self.frame.emit(rgb_image(latest[1], run.size))
        if ended:
            self._ended(run, project)
            return
        pos = min(clock, self.total)
        if pos - self._last_pos >= 1 / self._fps - 1e-9:
            self._last_pos = pos
            self.position.emit(pos)

    def _ended(self, run: _Run, project: Project) -> None:
        error = run.error
        self.stop()
        if error:
            self.failed.emit(error)
        if self.loop and not error and self.total > 0:
            self.play(project, 0.0)
            return
        self.position.emit(self.total)
        self.finished.emit()


# ------------------------------------------------------------------ thumbnails / waveforms


def thumb_count(source: Source) -> int:
    if source.kind == "image":
        return 1
    return max(1, min(THUMB_MAX, math.ceil(source.duration * THUMB_PPS / THUMB_EVERY_PX)))


def wave_buckets(source: Source) -> int:
    return max(50, min(WAVE_MAX, round(source.duration * WAVE_PER_SECOND)))


class _ScanWorker(QObject):
    thumbs = Signal(str, object)  # source id, list of JPEG paths
    wave = Signal(str, object)  # source id, peaks

    def __init__(self, tmp: Path) -> None:
        super().__init__()
        self.tmp = tmp
        self.closed = False

    @Slot(object)
    def scan(self, source: Source) -> None:
        if self.closed or not self.tmp.exists():
            return
        try:
            if source.kind in ("video", "image"):
                paths = preview.thumbnails(source, thumb_count(source), THUMB_HEIGHT, self.tmp)
                self.thumbs.emit(source.id, [str(p) for p in paths])
            if source.kind in ("video", "audio") and not self.closed:
                self.wave.emit(source.id, preview.waveform(source, wave_buckets(source)))
        except Exception:
            log.warning("couldn't scan %s for the timeline", source.path, exc_info=True)
        if self.closed:  # the page is gone: leave nothing behind in its folder
            shutil.rmtree(self.tmp, ignore_errors=True)


class MediaScanner(QObject):
    """Makes each source's thumbnails and waveform once, on a background thread."""

    thumbnails = Signal(str, object)  # source id, list[QImage]
    waveform = Signal(str, object)  # source id, list[float]
    _scan = Signal(object)

    def __init__(self, tmp: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._seen: set[str] = set()
        self._thumbs: dict[str, list[QImage]] = {}
        self._waves: dict[str, list[float]] = {}
        self._closed = False
        self._thread = QThread(self)
        self._worker = _ScanWorker(Path(tmp))
        self._worker.moveToThread(self._thread)
        self._scan.connect(self._worker.scan)
        self._worker.thumbs.connect(self._got_thumbs)
        self._worker.wave.connect(self._got_wave)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def want(self, sources) -> None:
        """Scan the sources not seen before; scanned ones are re-sent from the cache."""
        if self._closed:
            return
        for s in sources:
            key = f"{s.id}|{s.path}"
            if key in self._seen:
                if s.id in self._thumbs:
                    self.thumbnails.emit(s.id, self._thumbs[s.id])
                if s.id in self._waves:
                    self.waveform.emit(s.id, self._waves[s.id])
                continue
            self._seen.add(key)
            self._scan.emit(s)

    def _got_thumbs(self, source_id: str, paths: list[str]) -> None:
        images = []
        for p in paths:
            img = QImage(p)
            if not img.isNull():
                images.append(img)
            with contextlib.suppress(OSError):
                Path(p).unlink()
        if images and not self._closed:
            self._thumbs[source_id] = images
            self.thumbnails.emit(source_id, images)

    def _got_wave(self, source_id: str, peaks: list[float]) -> None:
        if peaks and not self._closed:
            self._waves[source_id] = peaks
            self.waveform.emit(source_id, peaks)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._worker.closed = True
        self._thread.quit()
        if not self._thread.wait(300):
            log.warning("Thumbnail thread didn't stop quickly; letting it finish")
            entry = (self._thread, self._worker)
            self._thread.setParent(None)
            _STRAGGLERS.append(entry)
            self._thread.finished.connect(lambda: _STRAGGLERS.remove(entry))
