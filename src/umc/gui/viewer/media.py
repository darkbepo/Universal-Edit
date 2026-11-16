"""Playing songs and videos in the viewer: the big player (``MediaView``) and the mini player
under the side list (``MiniPlayer``) that keeps a song or video going while you look at other
files.

One ``Playback`` = one file's QMediaPlayer + QAudioOutput. A view *attaches* to a playback
(shows it, drives its controls) and *detaches* again; moving a playing file from the big view
to the mini player and back hands the same playback over, so it never restarts.

Mouse: a click on the picture / waveform plays or pauses (it never jumps); the position and
volume sliders jump to where they're clicked; the wheel over the picture changes the volume."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from umc.gui import icons, theme

log = logging.getLogger(__name__)

VOLUME_STEP = 5  # % per wheel notch


def clock(ms: int) -> str:
    s = max(0, ms) // 1000
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def wheel_steps(angle_delta_y: int) -> int:
    """Whole wheel notches in one wheel event (touchpads send small parts of a notch)."""
    if angle_delta_y == 0:
        return 0
    steps = int(angle_delta_y / 120)
    return steps if steps else (1 if angle_delta_y > 0 else -1)


# --------------------------------------------------------------------------- a slider that jumps
class JumpSlider(QSlider):
    """A slider that jumps straight to where it's clicked (and can be dragged on from there);
    Qt's own only moves a page per click. Reports the jump through ``sliderMoved``."""

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = self._value_at(event.position().toPoint())
            if value != self.value():
                self.setValue(value)
                self.sliderMoved.emit(value)
        super().mousePressEvent(event)  # the handle is under the pointer now: a drag goes on

    def _value_at(self, pos) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
        )
        if self.orientation() == Qt.Orientation.Horizontal:
            start, span, at = groove.x(), groove.width() - handle.width(), pos.x()
            at -= handle.width() // 2
        else:
            start, span, at = groove.y(), groove.height() - handle.height(), pos.y()
            at -= handle.height() // 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), at - start, max(span, 1), opt.upsideDown
        )


# --------------------------------------------------------------------------- one file playing
class Playback(QObject):
    """One song or video: its player, its sound output and (for songs) its waveform."""

    def __init__(self, path: Path, sound_only: bool, volume: int, parent: QObject) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

        self.path = Path(path)
        self.sound_only = sound_only
        self.error = ""
        self.levels: list[float] | None = None  # the waveform, once worked out
        self.volume = volume
        self.player = QMediaPlayer(self)
        # No sound device (a server, a PC without speakers): no audio output at all. With one
        # that can't open a device, Qt's FFmpeg backend can lock up when playback stops.
        self.audio = QAudioOutput(self) if QMediaDevices.audioOutputs() else None
        if self.audio is not None:
            self.player.setAudioOutput(self.audio)
        self.set_volume(volume)
        self.player.errorOccurred.connect(self._on_error)
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))

    def set_volume(self, percent: int) -> None:
        self.volume = max(0, min(100, percent))
        if self.audio is not None:
            self.audio.setVolume(self.volume / 100)

    def playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def toggle(self) -> None:
        if self.playing():
            self.player.pause()
        else:
            self.player.play()

    def close(self) -> None:
        self.player.stop()
        self.player.setVideoOutput(None)
        self.player.setSource(QUrl())
        self.deleteLater()

    def _on_error(self, _error, message: str) -> None:
        self.error = message or "This file can't be played."
        log.warning("viewer: can't play %s: %s", self.path, message)


# --------------------------------------------------------------------------- waveform
class WaveformView(QWidget):
    """A song's waveform: bars mirrored around the middle, the played part in the accent
    colour, a line where it is now. A click plays / pauses (``clicked``); it never jumps."""

    clicked = Signal()
    wheeled = Signal(int)  # wheel notches (+ = up)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerWave")
        self.levels: list[float] | None = None
        self.fraction = 0.0  # how much has been played (0…1)
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to play or pause")

    def set_levels(self, levels: list[float] | None) -> None:
        self.levels = levels
        self.update()

    def set_fraction(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if abs(fraction - self.fraction) * max(self.width(), 1) >= 0.5:
            self.fraction = fraction
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = wheel_steps(event.angleDelta().y())
        if steps:
            self.wheeled.emit(steps)
        event.accept()

    def bar_heights(self, bars: int) -> list[float]:
        """The waveform resampled to ``bars`` bars (each the loudest of the levels it covers)."""
        levels = self.levels or []
        if not levels or bars <= 0:
            return []
        out = []
        n = len(levels)
        for i in range(bars):
            a = i * n // bars
            b = max(a + 1, (i + 1) * n // bars)
            out.append(max(levels[a:b]))
        return out

    def paintEvent(self, _event) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(24, 16, -24, -16)
        if r.width() <= 0 or r.height() <= 0:
            return
        mid = r.center().y()
        if not self.levels:
            p.setPen(QColor(pal["border_strong"]))
            p.drawLine(r.left(), mid, r.right(), mid)
            return
        bar, gap = 3, 2
        bars = max(1, (r.width() + gap) // (bar + gap))
        played_x = r.left() + self.fraction * r.width()
        played = QPainterPath()
        rest = QPainterPath()
        half = r.height() / 2
        for i, level in enumerate(self.bar_heights(bars)):
            x = r.left() + i * (bar + gap)
            h = max(2.0, level * half)
            box = QRectF(x, mid - h, bar, 2 * h)
            (played if x + bar / 2 <= played_x else rest).addRoundedRect(box, 1.5, 1.5)
        p.fillPath(rest, QColor(pal["border_strong"]))
        p.fillPath(played, QColor(pal["accent"]))
        p.setPen(QColor(pal["accent"]))
        p.drawLine(round(played_x), r.top(), round(played_x), r.bottom())


# --------------------------------------------------------------------------- shared bits
class _ClickFilter(QObject):
    """Clicks on a widget we don't subclass (QVideoWidget) → ``clicked``; wheel → ``wheeled``."""

    clicked = Signal()
    wheeled = Signal(int)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return True
        if t == QEvent.Type.MouseButtonDblClick:
            return True  # the two presses already played and paused
        if t == QEvent.Type.Wheel:
            steps = wheel_steps(event.angleDelta().y())
            if steps:
                self.wheeled.emit(steps)
            return True
        return False


def _play_icon(button: QPushButton, playing: bool, size: int = 18) -> None:
    pal = theme.palette()
    button.setIcon(icons.icon("player-pause" if playing else "player-play", pal["text"], size))
    button.setToolTip("Pause (Space)" if playing else "Play (Space)")


# --------------------------------------------------------------------------- the big player
class MediaView(QWidget):
    """The viewer's player: the picture (or the waveform), play / pause, position, volume."""

    toggled = Signal()  # play / pause was asked for (click, button, Space)
    volume_changed = Signal(int)  # 0…100, from the slider or the wheel
    duration_changed = Signal(int)  # ms of the attached file

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.playback: Playback | None = None
        self.video = QVideoWidget()
        self.music = QWidget(objectName="ViewerMusic")
        ml = QVBoxLayout(self.music)
        ml.setContentsMargins(24, 24, 24, 24)
        self.music_icon = QLabel()
        self.music_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wave = WaveformView()
        ml.addStretch(1)
        ml.addWidget(self.music_icon)
        ml.addSpacing(12)
        ml.addWidget(self.wave, 2)
        ml.addStretch(1)
        self.screen = QStackedWidget()
        self.screen.addWidget(self.video)
        self.screen.addWidget(self.music)

        self._clicks = _ClickFilter(self)
        for w in (self.video, self.music, self.music_icon):
            w.installEventFilter(self._clicks)
        self._clicks.clicked.connect(self.toggled)
        self._clicks.wheeled.connect(self.wheel)
        self.wave.clicked.connect(self.toggled)
        self.wave.wheeled.connect(self.wheel)

        self.volume_note = QLabel(self.screen, objectName="ViewerVolumeNote")
        self.volume_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.volume_note.hide()
        self._note_timer = QTimer(self, singleShot=True, interval=900)
        self._note_timer.timeout.connect(self.volume_note.hide)

        self.play_button = QPushButton(objectName="Small")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setIconSize(QSize(18, 18))
        self.play_button.clicked.connect(self.toggled)
        self.position = JumpSlider(Qt.Orientation.Horizontal)
        self.position.setToolTip("Position: click or drag to jump")
        self.position.sliderMoved.connect(self._seek)
        self.time = QLabel("0:00 / 0:00", objectName="Muted")
        self.volume = JumpSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setToolTip("Volume (or scroll over the picture)")
        self.volume.valueChanged.connect(self.volume_changed)
        self.volume_icon = QLabel()

        controls = QFrame(objectName="ViewerControls")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(12, 6, 12, 6)
        cl.addWidget(self.play_button)
        cl.addWidget(self.position, 1)
        cl.addWidget(self.time)
        cl.addSpacing(8)
        cl.addWidget(self.volume_icon)
        cl.addWidget(self.volume)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.screen, 1)
        lay.addWidget(controls)
        self._retint()
        theme.notifier().changed.connect(self._retint)
        self._update_button()

    def _retint(self) -> None:
        pal = theme.palette()
        self.music_icon.setPixmap(icons.pixmap("music", 72, pal["border_strong"]))
        self.volume_icon.setPixmap(icons.pixmap("volume", 16, pal["muted"]))
        self._update_button()

    # ------------------------------------------------------------ the file shown
    @property
    def player(self):
        return self.playback.player if self.playback is not None else None

    @property
    def error(self) -> str:
        return self.playback.error if self.playback is not None else ""

    def attach(self, playback: Playback) -> None:
        self.detach()
        self.playback = playback
        self.screen.setCurrentWidget(self.music if playback.sound_only else self.video)
        self.wave.set_levels(playback.levels)
        self.wave.fraction = 0.0
        player = playback.player
        player.setVideoOutput(None if playback.sound_only else self.video)
        player.durationChanged.connect(self._on_duration)
        player.positionChanged.connect(self._on_position)
        player.playbackStateChanged.connect(self._on_state)
        self._on_duration(player.duration())
        self._update_button()

    def detach(self) -> Playback | None:
        pb, self.playback = self.playback, None
        if pb is not None:
            player = pb.player
            for signal, slot in (
                (player.durationChanged, self._on_duration),
                (player.positionChanged, self._on_position),
                (player.playbackStateChanged, self._on_state),
            ):
                with contextlib.suppress(RuntimeError, TypeError):
                    signal.disconnect(slot)
            if not pb.sound_only:
                player.setVideoOutput(None)
        self.position.setRange(0, 0)
        self.time.setText("0:00 / 0:00")
        self.wave.set_levels(None)
        self._update_button()
        return pb

    def set_levels(self, playback: Playback, levels: list[float]) -> None:
        playback.levels = levels
        if playback is self.playback:
            self.wave.set_levels(levels)

    def set_volume(self, percent: int) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(percent)
        self.volume.blockSignals(False)

    def wheel(self, steps: int) -> None:
        value = max(0, min(100, self.volume.value() + steps * VOLUME_STEP))
        self.volume.setValue(value)  # → volume_changed
        self.volume_note.setText(f"Volume {value} %")
        self.volume_note.adjustSize()
        self.volume_note.resize(self.volume_note.width() + 28, self.volume_note.height() + 14)
        self.volume_note.move(
            (self.screen.width() - self.volume_note.width()) // 2,
            self.screen.height() - self.volume_note.height() - 24,
        )
        self.volume_note.show()
        self.volume_note.raise_()
        self._note_timer.start()

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = wheel_steps(event.angleDelta().y())
        if steps:
            self.wheel(steps)
        event.accept()

    # ------------------------------------------------------------ player → controls
    def _seek(self, ms: int) -> None:
        if self.playback is not None:
            self.playback.player.setPosition(ms)

    def _on_duration(self, ms: int) -> None:
        self.position.setRange(0, max(ms, 0))
        if self.playback is not None:
            self._on_position(self.playback.player.position())
        self.duration_changed.emit(ms)

    def _on_position(self, ms: int) -> None:
        if self.playback is None:
            return
        if not self.position.isSliderDown():
            self.position.setValue(ms)
        total = self.playback.player.duration()
        self.time.setText(f"{clock(ms)} / {clock(total)}")
        self.wave.set_fraction(ms / total if total > 0 else 0.0)

    def _on_state(self, _state) -> None:
        self._update_button()

    def _update_button(self) -> None:
        _play_icon(self.play_button, self.playback is not None and self.playback.playing())


# --------------------------------------------------------------------------- the mini player
class MiniPlayer(QFrame):
    """Keeps a song or video playing under the side list while other files are shown."""

    reopen = Signal(object)  # Path: show this file again in the big view
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.setObjectName("MiniPlayer")
        self.playback: Playback | None = None
        self.video = QVideoWidget()
        self.video.setFixedHeight(140)
        self._clicks = _ClickFilter(self)
        self.video.installEventFilter(self._clicks)
        self._clicks.clicked.connect(self.toggle)
        self.name = QPushButton(objectName="MiniPlayerName")
        self.name.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name.setToolTip("Show it again")
        self.name.clicked.connect(self._reopen)
        self.close_button = QPushButton(objectName="Small")
        self.close_button.setToolTip("Stop and close")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.stop)
        self.play_button = QPushButton(objectName="Small")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.clicked.connect(self.toggle)
        self.position = JumpSlider(Qt.Orientation.Horizontal)
        self.position.setToolTip("Position: click or drag to jump")
        self.position.sliderMoved.connect(self._seek)
        self.time = QLabel("0:00", objectName="Muted")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.name, 1)
        top.addWidget(self.close_button)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.play_button)
        row.addWidget(self.position, 1)
        row.addWidget(self.time)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lay.addWidget(self.video)
        lay.addLayout(top)
        lay.addLayout(row)
        self._retint()
        theme.notifier().changed.connect(self._retint)
        self.hide()

    def _retint(self) -> None:
        pal = theme.palette()
        self.close_button.setIcon(icons.icon("close", pal["muted"], 14))
        self._update_button()

    def attach(self, playback: Playback) -> None:
        if self.playback is not None:
            self.stop()
        self.playback = playback
        player = playback.player
        self.video.setVisible(not playback.sound_only)
        if not playback.sound_only:
            player.setVideoOutput(self.video)
        name = playback.path.name
        self.name.setText(name if len(name) <= 34 else name[:32] + "…")
        self.name.setToolTip(f"{name}\nClick to show it again")
        player.durationChanged.connect(self._on_duration)
        player.positionChanged.connect(self._on_position)
        player.playbackStateChanged.connect(self._on_state)
        self._on_duration(player.duration())
        self._update_button()
        self.show()

    def detach(self) -> Playback | None:
        pb, self.playback = self.playback, None
        if pb is not None:
            player = pb.player
            for signal, slot in (
                (player.durationChanged, self._on_duration),
                (player.positionChanged, self._on_position),
                (player.playbackStateChanged, self._on_state),
            ):
                with contextlib.suppress(RuntimeError, TypeError):
                    signal.disconnect(slot)
            if not pb.sound_only:
                player.setVideoOutput(None)
        self.hide()
        return pb

    def playing(self) -> bool:
        return self.playback is not None and self.playback.playing()

    def toggle(self) -> None:
        if self.playback is not None:
            self.playback.toggle()

    def stop(self) -> None:
        pb = self.detach()
        if pb is not None:
            pb.close()
        self.closed.emit()

    def _reopen(self) -> None:
        if self.playback is not None:
            self.reopen.emit(self.playback.path)

    def _seek(self, ms: int) -> None:
        if self.playback is not None:
            self.playback.player.setPosition(ms)

    def _on_duration(self, ms: int) -> None:
        self.position.setRange(0, max(ms, 0))
        if self.playback is not None:
            self._on_position(self.playback.player.position())

    def _on_position(self, ms: int) -> None:
        if not self.position.isSliderDown():
            self.position.setValue(ms)
        self.time.setText(clock(ms))

    def _on_state(self, _state) -> None:
        self._update_button()

    def _update_button(self) -> None:
        _play_icon(self.play_button, self.playing(), 14)
