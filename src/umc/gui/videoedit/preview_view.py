"""The preview: the frame at the playhead on a dark background, and the transport row."""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from umc.core.errors import ConversionCancelled, ConversionError
from umc.gui.videoedit.threads import keep_until_finished
from umc.videoedit import preview
from umc.videoedit.project import Project, output_fps

log = logging.getLogger(__name__)

DEBOUNCE_MS = 40
SCREEN_BG = "#0b0c0f"  # a dark "window" behind the frame in both themes


def format_time(t: float) -> str:
    """``m:ss.cc`` (hundredths), e.g. ``1:05.40``."""
    cs = max(0, round(t * 100))
    minutes, rest = divmod(cs, 6000)
    return f"{minutes}:{rest // 100:02d}.{rest % 100:02d}"


class _Screen(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: QImage | None = None
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def frame_rect(self) -> QRect:
        """Where the frame is drawn: as large as fits, centred, aspect kept."""
        if self.image is None or self.image.isNull():
            return QRect()
        iw, ih = self.image.width(), self.image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = max(1, round(iw * scale)), max(1, round(ih * scale))
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(SCREEN_BG))
        if self.image is not None and not self.image.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(self.frame_rect(), self.image)
        p.end()


class PreviewView(QWidget):
    play_toggled = Signal(bool)
    step_requested = Signal(int)  # -1 previous frame, +1 next frame
    loop_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreview")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.screen = _Screen()
        root.addWidget(self.screen, 1)

        row = QHBoxLayout()
        row.setSpacing(6)

        def button(text: str, tip: str, checkable: bool = False) -> QPushButton:
            b = QPushButton(text, objectName="Small")
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
            return b

        row.addStretch(1)
        self.prev_button = button("⏮", "Previous frame (←)")
        self.play_button = button("▶", "Play / pause (Space)", checkable=True)
        self.next_button = button("⏭", "Next frame (→)")
        self.time_label = QLabel(objectName="Muted")
        self.time_label.setMinimumWidth(150)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.time_label)
        self.loop_button = button("Loop", "Loop playback (L)", checkable=True)
        row.addStretch(1)
        root.addLayout(row)

        self.prev_button.clicked.connect(lambda: self.step_requested.emit(-1))
        self.next_button.clicked.connect(lambda: self.step_requested.emit(1))
        self.play_button.toggled.connect(self._on_play)
        self.loop_button.toggled.connect(self.loop_toggled.emit)
        self.set_time(0.0, 0.0)

    @property
    def loop(self) -> bool:
        return self.loop_button.isChecked()

    @loop.setter
    def loop(self, on: bool) -> None:
        self.loop_button.setChecked(on)

    @property
    def playing(self) -> bool:
        return self.play_button.isChecked()

    def set_playing(self, on: bool) -> None:
        """Show the play / pause state without emitting ``play_toggled``."""
        self.play_button.blockSignals(True)
        self.play_button.setChecked(on)
        self.play_button.blockSignals(False)
        self.play_button.setText("⏸" if on else "▶")

    def _on_play(self, on: bool) -> None:
        self.play_button.setText("⏸" if on else "▶")
        self.play_toggled.emit(on)

    def show_frame(self, image: QImage) -> None:
        self.screen.image = image
        self.screen.update()

    def set_time(self, t: float, total: float) -> None:
        self.time_label.setText(f"{format_time(t)} / {format_time(total)}")


# ------------------------------------------------------------------ stills


def frame_number(project: Project, t: float) -> int:
    return math.floor(max(0.0, t) * output_fps(project) + 1e-6)


def rgb_image(data: bytes, size: tuple[int, int]) -> QImage:
    w, h = size
    return QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


class _StillWorker(QObject):
    done = Signal(int, float, int, object)  # version, t, frame number, RGB bytes or None
    failed = Signal(str)

    def __init__(self, tmp: Path) -> None:
        super().__init__()
        self.tmp = tmp

    @Slot(object)
    def render(self, job: tuple) -> None:
        project, version, t, frame_no, cancel = job
        data: bytes | None = None
        try:
            data = preview.still(project, t, self.tmp, cancel_event=cancel)
        except ConversionCancelled:
            pass
        except ConversionError as exc:
            log.error("preview still failed: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("preview still failed")
            self.failed.emit(preview.ERROR)
        self.done.emit(version, t, frame_no, data)


class FrameService(QObject):
    """Stills for the preview: debounced, rendered on one worker thread, newest request wins."""

    ready = Signal(int, float, QImage)  # version, t, frame
    failed = Signal(str)
    _render = Signal(object)

    def __init__(self, tmp: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = preview.StillCache()
        self._latest: tuple[Project, int, float] | None = None
        self._busy: tuple[int, int] | None = None  # (version, frame) being rendered
        self._cancel: threading.Event | None = None
        self._sizes: dict[int, tuple[int, int]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._start)
        self._thread = QThread(self)
        self._worker = _StillWorker(Path(tmp))
        self._worker.moveToThread(self._thread)
        self._render.connect(self._worker.render)
        self._worker.done.connect(self._done)
        self._worker.failed.connect(self.failed)
        self._thread.start()
        self._closed = False

    def request(self, project: Project, version: int, t: float) -> None:
        if self._closed:
            return
        self._latest = (project, version, t)
        key = (version, frame_number(project, t))
        if self._busy is not None and self._busy != key and self._cancel is not None:
            self._cancel.set()  # a newer frame is wanted: stop FFmpeg early
        self._timer.start()

    def _start(self) -> None:
        if self._latest is None or self._busy is not None or self._closed:
            return
        project, version, t = self._latest
        frame_no = frame_number(project, t)
        size = preview.preview_size(project)
        self._sizes = {version: size}
        cached = self.cache.get(version, frame_no)
        if cached is not None:
            self._latest = None
            self.ready.emit(version, t, rgb_image(cached, size))
            return
        self._busy = (version, frame_no)
        self._cancel = threading.Event()
        self._render.emit((project, version, t, frame_no, self._cancel))

    def _done(self, version: int, t: float, frame_no: int, data: bytes | None) -> None:
        self._busy, self._cancel = None, None
        if self._closed:
            return
        latest = self._latest
        size = self._sizes.get(version)
        if data is not None and size is not None:
            self.cache.put(version, frame_no, data)
        if latest is None:
            return
        project, want_version, want_t = latest
        if (want_version, frame_number(project, want_t)) == (version, frame_no):
            self._latest = None
            if data is not None and size is not None:
                self.ready.emit(version, want_t, rgb_image(data, size))
        elif not self._timer.isActive():
            self._start()  # a newer request came in while this one rendered

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if self._cancel is not None:
            self._cancel.set()
        self._thread.quit()
        if not self._thread.wait(10_000):
            # Still inside FFmpeg: never destroy a running QThread. Detach it from this object and
            # keep it alive until it finishes.
            log.warning("Preview thread didn't stop within 10 s; letting it finish")
            keep_until_finished(self._thread, self._worker)
