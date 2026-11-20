"""The video next to the subtitles: the picture with the current subtitle drawn over it (a
QGraphicsView, so the text can sit on top of the video — QVideoWidget is a native window on
some systems and would hide it), play / pause, a position slider and the time.

Playback is the viewer's ``Playback`` (Qt Multimedia, no audio output without a sound device)."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from umc.gui import icons, theme
from umc.gui.viewer.media import JumpSlider, Playback
from umc.subedit.formats import clock, convert_markup

_TAGS = ("i", "b", "u", "s")


def caption_html(texts: list[str], fmt: str) -> str:
    """The shown subtitles as safe HTML: only italic / bold / underline / strike kept."""
    parts = []
    for text in texts:
        safe = html.escape(convert_markup(text, fmt, "srt"))
        for t in _TAGS:
            safe = safe.replace(f"&lt;{t}&gt;", f"<{t}>").replace(f"&lt;/{t}&gt;", f"</{t}>")
        parts.append(safe.replace("\n", "<br>"))
    return "<br>".join(parts)


class _Stage(QGraphicsView):
    clicked = Signal()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class VideoPreview(QWidget):
    """``position_changed`` (ms) while playing and after a seek; ``closed`` when the video is
    taken away."""

    position_changed = Signal(int)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        self.setObjectName("SubtitlePreview")
        self.playback: Playback | None = None
        self.path: Path | None = None
        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(QBrush(QColor("#000000")))
        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)
        self.caption_box = QGraphicsRectItem()
        self.caption_box.setBrush(QBrush(QColor(0, 0, 0, 150)))
        self.caption_box.setPen(Qt.PenStyle.NoPen)
        self.scene.addItem(self.caption_box)
        self.caption = QGraphicsTextItem()
        self.caption.setDefaultTextColor(QColor("#ffffff"))
        font = QFont()
        font.setPointSize(15)
        self.caption.setFont(font)
        self.scene.addItem(self.caption)
        self.caption_text = ""  # the HTML shown, for tests

        self.stage = _Stage(self.scene)
        self.stage.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stage.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stage.setFrameShape(QFrame.Shape.NoFrame)
        self.stage.setMinimumSize(240, 135)
        self.stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stage.clicked.connect(self.toggle)

        self.play_button = QPushButton(objectName="Small")
        self.play_button.setAccessibleName("Play")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.clicked.connect(self.toggle)
        self.slider = JumpSlider(Qt.Orientation.Horizontal)
        self.slider.setToolTip("Position: click or drag to jump")
        self.slider.sliderMoved.connect(self.seek)
        self.time = QLabel("0:00 / 0:00", objectName="Muted")
        self.close_button = QPushButton("Close video", objectName="Link")
        self.close_button.setToolTip("Take the video away (the subtitles stay)")
        self.close_button.clicked.connect(self.close_video)
        self.problem = QLabel(objectName="Error")
        self.problem.setWordWrap(True)
        self.problem.hide()

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 4)
        controls.addWidget(self.play_button)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time)
        controls.addWidget(self.close_button)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.stage, 1)
        root.addWidget(self.problem)
        root.addLayout(controls)

        self._timer = QTimer(self, interval=40)
        self._timer.timeout.connect(self._tick)
        self._show_play_icon()
        self.set_caption([], "srt")

    # ------------------------------------------------------------------ the file
    @property
    def has_video(self) -> bool:
        return self.playback is not None

    def open_video(self, path: Path) -> None:
        self.close_video(quiet=True)
        self.path = Path(path)
        pb = Playback(self.path, False, 70, self)
        pb.player.setVideoOutput(self.video_item)
        pb.player.durationChanged.connect(self._duration)
        pb.player.positionChanged.connect(self._moved)
        pb.player.playbackStateChanged.connect(self._state)
        pb.player.errorOccurred.connect(self._error)
        self.playback = pb
        pb.load()
        self.problem.hide()
        self.show()
        self._layout()

    def close_video(self, quiet: bool = False) -> None:
        if self.playback is None:
            return
        self._timer.stop()
        self.playback.close()
        self.playback = None
        self.path = None
        self.slider.setRange(0, 0)
        self._show_play_icon()
        if not quiet:
            self.closed.emit()

    # ------------------------------------------------------------------ playing
    def position(self) -> int:
        return int(self.playback.player.position()) if self.playback else 0

    def playing(self) -> bool:
        return self.playback is not None and self.playback.playing()

    def toggle(self) -> None:
        if self.playback is not None:
            self.playback.toggle()

    def pause(self) -> None:
        if self.playing():
            self.playback.player.pause()

    def seek(self, ms: int) -> None:
        if self.playback is None:
            return
        self.playback.player.setPosition(max(0, int(ms)))
        self._moved(max(0, int(ms)))

    def set_caption(self, texts: list[str], fmt: str) -> None:
        shown = caption_html(texts, fmt)
        if shown == self.caption_text:
            return
        self.caption_text = shown
        self.caption.setHtml(f"<div align='center'>{shown}</div>")
        self._place_caption()

    # ------------------------------------------------------------------ Qt events
    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, max(0, int(ms)))
        self._show_time()

    def _moved(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(int(ms))
        self._show_time()
        self.position_changed.emit(int(ms))

    def _state(self, _state) -> None:
        self._show_play_icon()
        if self.playing():
            self._timer.start()
        else:
            self._timer.stop()
            self.position_changed.emit(self.position())

    def _tick(self) -> None:
        self.position_changed.emit(self.position())

    def _error(self, _error, message: str) -> None:
        self.problem.setText(f"This video can't be played here: {message or 'unknown error'}")
        self.problem.show()

    def _show_time(self) -> None:
        self.time.setText(f"{clock(self.position())} / {clock(self.slider.maximum())}")

    def _show_play_icon(self) -> None:
        playing = self.playing()
        name = "player-pause" if playing else "player-play"
        self.play_button.setIcon(icons.icon(name, theme.palette()["text"], 18))
        self.play_button.setAccessibleName("Pause" if playing else "Play")
        self.play_button.setToolTip(
            ("Pause" if playing else "Play") + " (Ctrl+Space, or Space in the list)"
        )

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._layout()

    def _layout(self) -> None:
        r = self.stage.viewport().rect()
        self.scene.setSceneRect(QRectF(0, 0, r.width(), r.height()))
        self.video_item.setPos(0, 0)
        self.video_item.setSize(QSizeF(r.width(), r.height()))
        self._place_caption()

    def _place_caption(self) -> None:
        r = self.scene.sceneRect()
        self.caption.setTextWidth(max(50.0, r.width() * 0.9))
        b = self.caption.boundingRect()
        x = (r.width() - b.width()) / 2
        y = r.height() - b.height() - max(8.0, r.height() * 0.05)
        self.caption.setPos(x, y)
        empty = not self.caption_text
        self.caption_box.setVisible(not empty)
        self.caption.setVisible(not empty)
        self.caption_box.setRect(QRectF(x, y, b.width(), b.height()))
