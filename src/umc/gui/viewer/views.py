"""What the viewer shows in its big area: one widget per kind of file."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.gui import icons, theme

log = logging.getLogger(__name__)

MAX_PAGE_WIDTH = 1400  # px a page is drawn at most (logical)


# --------------------------------------------------------------------------- pictures
def load_image(path: Path) -> QImage:
    """Runs on a worker thread. Qt reads the common formats (turned upright by EXIF); Pillow the
    rest (HEIC, AVIF, camera RAW, PSD, …)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if reader.canRead():
        image = reader.read()
        if not image.isNull():
            return image
    from PIL import ImageOps

    from umc.converters.imaging import open_image

    img = ImageOps.exif_transpose(open_image(Path(path))).convert("RGBA")
    data = img.tobytes()
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()  # own the pixels (``data`` goes away)


def is_animated(path: Path) -> bool:
    reader = QImageReader(str(path))
    return reader.canRead() and reader.supportsAnimation() and reader.imageCount() != 1


class ImageCanvas(QWidget):
    """A picture fitted to the window. Wheel zooms around the pointer, drag pans,
    double-click switches between "fit" and 100 %."""

    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerCanvas")
        self.image: QImage | None = None
        self.movie: QMovie | None = None
        self.fit = True
        self.zoom = 1.0
        self.center = QPointF(0, 0)  # image point shown in the middle of the widget
        self._drag: QPointF | None = None
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def clear(self) -> None:
        self._stop_movie()
        self.image = None
        self.update()

    def set_image(self, image: QImage) -> None:
        self._stop_movie()
        self.image = image
        self.fit_to_window()

    def set_movie(self, path: Path) -> None:
        """Animated GIF / WebP / APNG: plays in a loop."""
        self._stop_movie()
        self.movie = QMovie(str(path))
        self.movie.frameChanged.connect(self._on_frame)
        self.movie.start()
        self.image = self.movie.currentImage()
        self.fit_to_window()

    def _stop_movie(self) -> None:
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None

    def _on_frame(self, _n: int) -> None:
        if self.movie is not None:
            self.image = self.movie.currentImage()
            self.update()

    # ------------------------------------------------------------ zoom
    def fit_zoom(self) -> float:
        if self.image is None or self.image.isNull():
            return 1.0
        w, h = self.image.width(), self.image.height()
        return min(1.0, (self.width() - 24) / max(w, 1), (self.height() - 24) / max(h, 1))

    def fit_to_window(self) -> None:
        self.fit = True
        self._apply_fit()
        self.update()

    def _apply_fit(self) -> None:
        if self.image is None:
            return
        self.zoom = max(self.fit_zoom(), 0.01)
        self.center = QPointF(self.image.width() / 2, self.image.height() / 2)
        self.zoom_changed.emit(self.zoom)

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        if self.image is None:
            return
        zoom = max(0.02, min(32.0, zoom))
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        before = self.to_image(anchor)
        self.zoom = zoom
        after = self.to_image(anchor)
        self.center += before - after
        self.fit = False
        self.zoom_changed.emit(self.zoom)
        self.update()

    def to_image(self, p: QPointF) -> QPointF:
        mid = QPointF(self.width() / 2, self.height() / 2)
        return self.center + (p - mid) / self.zoom

    def image_rect(self) -> QRectF:
        assert self.image is not None
        w, h = self.image.width() * self.zoom, self.image.height() * self.zoom
        mid = QPointF(self.width() / 2, self.height() / 2)
        top_left = mid - self.center * self.zoom
        return QRectF(top_left.x(), top_left.y(), w, h)

    # ------------------------------------------------------------ events
    def resizeEvent(self, event) -> None:  # noqa: N802
        if self.fit:
            self._apply_fit()
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        if self.image is None or self.image.isNull():
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 4)
        rect = self.image_rect()
        if self.image.hasAlphaChannel():
            self._checkerboard(p, rect.toAlignedRect().intersected(self.rect()))
        p.drawImage(rect, self.image)

    def _checkerboard(self, p: QPainter, r: QRect) -> None:
        pal = theme.palette()
        p.fillRect(r, QColor(pal["surface"]))
        dark = QColor(pal["surface_alt"])
        s = 10
        for y in range(r.top() - r.top() % s, r.bottom() + 1, s):
            for x in range(r.left() - r.left() % s, r.right() + 1, s):
                if (x // s + y // s) % 2:
                    p.fillRect(QRect(x, y, s, s).intersected(r), dark)

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.zoom * (1.25**steps), event.position())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.fit:
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            delta = event.position() - self._drag
            self._drag = event.position()
            self.center -= delta / self.zoom
            self.update()

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.fit:
            self.set_zoom(1.0, event.position())
        else:
            self.fit_to_window()


# --------------------------------------------------------------------------- video and music
def _clock(ms: int) -> str:
    s = max(ms, 0) // 1000
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MediaView(QWidget):
    """Plays a video or a sound file: play / pause, seek, volume. Space toggles."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.player.setVideoOutput(self.video)
        self.music = QLabel(objectName="ViewerMusic")
        self.music.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen = QStackedWidget()
        self.screen.addWidget(self.video)
        self.screen.addWidget(self.music)

        self.play_button = QPushButton(objectName="Small")
        self.play_button.setToolTip("Play / pause (Space)")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.clicked.connect(self.toggle)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setToolTip("Position")
        self.position.sliderMoved.connect(self.player.setPosition)
        self.time = QLabel("0:00 / 0:00", objectName="Muted")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(110)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)

        controls = QFrame(objectName="ViewerControls")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(12, 6, 12, 6)
        cl.addWidget(self.play_button)
        cl.addWidget(self.position, 1)
        cl.addWidget(self.time)
        cl.addSpacing(8)
        cl.addWidget(QLabel("Volume", objectName="Muted"))
        cl.addWidget(self.volume)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.screen, 1)
        lay.addWidget(controls)

        self.error = ""
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(lambda _s: self._update_button())
        self.player.errorOccurred.connect(self._on_error)
        self._update_button()

    def open(self, path: Path, sound_only: bool) -> None:
        self.error = ""
        self.screen.setCurrentWidget(self.music if sound_only else self.video)
        pal = theme.palette()
        self.music.setPixmap(icons.pixmap("music", 96, pal["border_strong"]))
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def stop(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())

    def toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _update_button(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing else "Play")

    def _on_duration(self, ms: int) -> None:
        self.position.setRange(0, max(ms, 0))
        self._on_position(self.player.position())

    def _on_position(self, ms: int) -> None:
        if not self.position.isSliderDown():
            self.position.setValue(ms)
        self.time.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")

    def _on_error(self, _error, message: str) -> None:
        self.error = message or "This file can't be played."
        log.warning("viewer: can't play %s: %s", self.player.source().toLocalFile(), message)


# --------------------------------------------------------------------------- pages
class PagesView(QScrollArea):
    """Pages under each other, drawn as they scroll into view (``request_page`` asks the window
    to render page i at a width; ``set_page`` hands the picture back)."""

    request_page = Signal(int, int)  # index, width in device pixels

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerPages")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.holder = QWidget(objectName="ViewerPagesHolder")
        self.column = QVBoxLayout(self.holder)
        self.column.setContentsMargins(20, 20, 20, 20)
        self.column.setSpacing(16)
        self.column.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(self.holder)
        self.labels: list[QLabel] = []
        self.sizes: list[tuple[float, float]] = []
        self.drawn: dict[int, int] = {}  # page → width it was drawn at
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._request_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._timer.start())

    def set_pages(self, sizes: list[tuple[float, float]]) -> None:
        for label in self.labels:
            label.deleteLater()
        self.labels = []
        self.drawn = {}
        self.sizes = sizes
        for i in range(len(sizes)):
            label = QLabel(objectName="ViewerPage")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setToolTip(f"Page {i + 1} of {len(sizes)}")
            self.column.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self.labels.append(label)
        self._layout_pages()
        self.verticalScrollBar().setValue(0)
        self._timer.start()

    def page_width(self) -> int:
        return max(120, min(MAX_PAGE_WIDTH, self.viewport().width() - 40))

    def _layout_pages(self) -> None:
        width = self.page_width()
        for label, (pw, ph) in zip(self.labels, self.sizes, strict=True):
            label.setFixedSize(QSize(width, max(1, round(width * ph / max(pw, 1)))))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.labels:
            self._layout_pages()
            self._timer.start()

    def visible_pages(self) -> list[int]:
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        margin = self.viewport().height()  # one screen ahead
        return [
            i
            for i, label in enumerate(self.labels)
            if label.y() + label.height() >= top - margin and label.y() <= bottom + margin
        ]

    def _request_visible(self) -> None:
        width = round(self.page_width() * self.devicePixelRatioF())
        for i in self.visible_pages():
            if self.drawn.get(i) != width:
                self.drawn[i] = width
                self.request_page.emit(i, width)

    def set_page(self, index: int, image: QImage) -> None:
        if 0 <= index < len(self.labels):
            pm = QPixmap.fromImage(image)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            self.labels[index].setPixmap(pm)


# --------------------------------------------------------------------------- text, other
class TextView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerText")
        self.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)


class InfoView(QWidget):
    """A big icon, the file name and a line of text (also used for "Opening…" and errors)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        self.icon = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(objectName="DropTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.text = QLabel(objectName="Muted", alignment=Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(520)
        for w in (self.icon, self.title, self.text):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignCenter)

    def show_info(self, icon: str, title: str, text: str) -> None:
        self.icon.setPixmap(icons.pixmap(icon, 72, theme.palette()["border_strong"]))
        self.title.setText(title)
        self.text.setText(text)
