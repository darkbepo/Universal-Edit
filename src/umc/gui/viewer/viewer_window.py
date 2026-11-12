"""The viewer window: the file large, the folder's other files beside it, Convert / Edit below.

Opened by ``UniversalEdit --view FILE`` (what "Open with" runs). ← / → (or Page Up / Down)
go through the folder; Home / End jump to the first / last file; F11 is full screen."""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import DetectedFile, detect
from umc.gui import branding, icons, theme
from umc.gui.viewer.tasks import TaskRunner
from umc.gui.viewer.views import (
    ImageCanvas,
    InfoView,
    MediaView,
    PagesView,
    TextView,
    _clock,
    is_animated,
    load_image,
)
from umc.viewer import files as vf
from umc.viewer import preview

log = logging.getLogger(__name__)

THUMB = 44
KIND_NAMES = {
    vf.IMAGE: "Picture",
    vf.VIDEO: "Video",
    vf.AUDIO: "Sound",
    vf.PAGES: "Document",
    vf.CONVERT: "Document",
    vf.TEXT: "Text",
    vf.MODEL: "3D model",
    vf.OTHER: "File",
}


def fmt_size(n: int) -> str:
    from umc.gui.widgets.file_queue import fmt_size as _fmt

    return _fmt(n)


def _thumbnail(path: Path) -> QImage | None:
    """Worker thread: a small picture for the side list (formats Qt reads directly)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    size = reader.size()
    if size.isValid() and min(size.width(), size.height()) > THUMB * 3:
        scale = THUMB * 3 / min(size.width(), size.height())
        reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
    image = reader.read()
    return None if image.isNull() else image


def _warm_up() -> None:
    import umc.tools.pdf  # noqa: F401


class ViewerWindow(QMainWindow):
    convert_requested = Signal(object)  # Path
    edit_requested = Signal(object)  # Path

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(branding.APP_NAME)
        self.setMinimumSize(640, 420)
        self.resize(1200, 780)
        self.tasks = TaskRunner(self)
        self.items: list[Path] = []
        self.current: DetectedFile | None = None
        self.kind = vf.OTHER
        self.details = ""  # "1920 × 1080", "12 pages", …
        self._model_view = None  # made on first 3D file (needs OpenGL)
        self._preview_dir: Path | None = None
        self._preview_cancel: threading.Event | None = None
        self._pdf_for_pages: Path | None = None
        self._thumbs_requested: set[Path] = set()

        root = QWidget(objectName="Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.stack = QStackedWidget(objectName="ViewerStage")
        self.canvas = ImageCanvas()
        self.media = MediaView()
        self.pages = PagesView()
        self.pages.request_page.connect(self._render_page)
        self.text = TextView()
        self.info = InfoView()
        for w in (self.canvas, self.media, self.pages, self.text, self.info):
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)

        self.list = QListWidget(objectName="ViewerList")
        self.list.setFixedWidth(250)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.currentRowChanged.connect(self._on_row)
        self.list.verticalScrollBar().valueChanged.connect(lambda _v: self._thumb_timer.start())
        body.addWidget(self.list)
        outer.addLayout(body, 1)

        bar = QFrame(objectName="BottomBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(22, 10, 22, 10)
        bl.setSpacing(12)
        self.name_label = QLabel(objectName="CardTitle")
        self.detail_label = QLabel(objectName="Muted")
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.convert_button = QPushButton("Convert", objectName="Secondary")
        self.convert_button.setToolTip("Open this file in the converter")
        self.edit_button = QPushButton("Edit", objectName="Primary")
        for b in (self.convert_button, self.edit_button):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumWidth(110)
        self.convert_button.clicked.connect(self._convert)
        self.edit_button.clicked.connect(self._edit)
        bl.addWidget(self.name_label)
        bl.addWidget(self.detail_label, 1)
        bl.addWidget(self.convert_button)
        bl.addWidget(self.edit_button)
        outer.addWidget(bar)
        self.setCentralWidget(root)

        # PyMuPDF takes about a second to import: do it early, so the first PDF opens quickly
        QTimer.singleShot(
            800, self, lambda: self.tasks.submit("warm", _warm_up, lambda _r: None, lane="pdf")
        )
        self.media.player.durationChanged.connect(self._on_duration)
        self._thumb_timer = QTimer(self, singleShot=True, interval=50)
        self._thumb_timer.timeout.connect(self._request_thumbs)
        keys = (
            (("Right", "PgDown"), lambda: self.step(1)),
            (("Left", "PgUp"), lambda: self.step(-1)),
            (("Home",), lambda: self.go_to(0)),
            (("End",), lambda: self.go_to(len(self.items) - 1)),
            (("F11",), self.toggle_full_screen),
            (("Esc",), self._leave_full_screen),
            (("Space",), self._space),
        )
        for seqs, fn in keys:
            for seq in seqs:
                QShortcut(QKeySequence(seq), self, fn).setContext(Qt.ShortcutContext.WindowShortcut)

    # ------------------------------------------------------------ opening
    def open_path(self, path: str | Path) -> None:
        """Show ``path`` and fill the side list with its folder."""
        path = Path(path).resolve()
        self.items = vf.folder_items(path)
        self.tasks.cancel_group("thumb")
        self._thumbs_requested.clear()
        self.list.blockSignals(True)
        self.list.clear()
        pal = theme.palette()
        for p in self.items:
            item = QListWidgetItem(p.name)
            item.setToolTip(p.name)
            item.setIcon(icons.icon(vf.ICON_OF_KIND[vf.kind_of_path(p)], pal["muted"], THUMB))
            item.setSizeHint(QSize(0, THUMB + 10))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.go_to(self.items.index(path) if path in self.items else 0)
        self._thumb_timer.start()

    def go_to(self, row: int) -> None:
        if not self.items:
            return
        row = max(0, min(row, len(self.items) - 1))
        if self.list.currentRow() != row:
            self.list.setCurrentRow(row)  # → _on_row
        elif self.current is None or self.current.path != self.items[row]:
            self._on_row(row)

    def step(self, delta: int) -> None:
        self.go_to(self.list.currentRow() + delta)

    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self.items):
            self.show_file(self.items[row])
            self.list.scrollToItem(self.list.item(row))

    def show_file(self, path: Path) -> None:
        self._stop_current()
        f = detect(path)
        self.current = f
        self.kind = vf.view_kind(f) if f is not None else vf.OTHER
        self.details = ""
        self.setWindowTitle(f"{path.name} — {branding.APP_NAME}")
        self.name_label.setText(path.name)
        self._update_buttons()
        self._update_details()
        if f is None or self.kind == vf.OTHER:
            self._show_info("file", "There's no preview for this kind of file.")
            return
        getattr(self, f"_show_{self.kind}")(f)

    def _stop_current(self) -> None:
        for group in ("main", "page"):
            self.tasks.cancel_group(group)
        self.media.stop()
        self.canvas.clear()
        if self._preview_cancel is not None:
            self._preview_cancel.set()
            self._preview_cancel = None

    def _show_info(self, icon: str, text: str, title: str | None = None) -> None:
        name = title if title is not None else (self.current.name if self.current else "")
        self.info.show_info(icon, name, text)
        self.stack.setCurrentWidget(self.info)

    def _failed(self, default: str):
        def show(message: str) -> None:
            self._show_info("alert", message or default)

        return show

    # ------------------------------------------------------------ each kind
    def _show_image(self, f: DetectedFile) -> None:
        self._show_info("photo", "Opening…")

        def load() -> tuple[QImage | None, bool]:
            # imageCount() reads a whole GIF: decide here, not on the UI thread
            if is_animated(f.path):
                return None, True
            return load_image(f.path), False

        def done(result: tuple[QImage | None, bool]) -> None:
            image, animated = result
            if animated:
                self.canvas.set_movie(f.path)
                image = self.canvas.image
            else:
                self.canvas.set_image(image)
            self._image_ready(image)

        self.tasks.submit("main", load, done, self._failed("This picture can't be opened."))

    def _image_ready(self, image: QImage | None) -> None:
        self.stack.setCurrentWidget(self.canvas)
        self.canvas.setFocus()
        if image is not None and not image.isNull():
            self.details = f"{image.width()} × {image.height()}"
            self._update_details()

    def _show_video(self, f: DetectedFile) -> None:
        self.media.open(f.path, sound_only=False)
        self.stack.setCurrentWidget(self.media)

    def _show_audio(self, f: DetectedFile) -> None:
        self.media.open(f.path, sound_only=True)
        self.stack.setCurrentWidget(self.media)

    def _show_pages(self, f: DetectedFile) -> None:
        self._open_pages(f.path)

    def _open_pages(self, pdf_path: Path) -> None:
        self._show_info("pdf", "Opening…")
        self.tasks.cancel_group("page")  # renders of the document shown before
        self._pdf_for_pages = pdf_path

        def done(sizes: list[tuple[float, float]]) -> None:
            if not sizes:
                self._show_info("alert", "This document has no pages.")
                return
            self.stack.setCurrentWidget(self.pages)
            self.pages.set_pages(sizes)
            self.details = "1 page" if len(sizes) == 1 else f"{len(sizes)} pages"
            self._update_details()

        self.tasks.submit(
            "main",
            lambda: preview.page_sizes(pdf_path),
            done,
            self._failed("This document can't be opened."),
            lane="pdf",
        )

    def _render_page(self, index: int, width: int) -> None:
        pdf_path = self._pdf_for_pages
        if pdf_path is None:
            return

        def render() -> QImage:
            img = preview.render_page(pdf_path, index, width).convert("RGB")
            q = QImage(
                img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888
            )
            return q.copy()

        self.tasks.submit(
            "page",
            render,
            lambda image: self.pages.set_page(index, image),
            exclusive=False,
            lane="pdf",
        )

    def _show_convert(self, f: DetectedFile) -> None:
        """Word, Excel, PowerPoint, … : made into a PDF first (the converter's own route)."""
        self._show_info("document", "Preparing a preview…")
        if self._preview_dir is None:
            self._preview_dir = Path(tempfile.mkdtemp(prefix="umc-view-"))
        out = Path(tempfile.mkdtemp(prefix="p", dir=self._preview_dir))
        cancel = threading.Event()
        self._preview_cancel = cancel

        def failed(message: str) -> None:
            self._show_info(
                "alert",
                message or "There's no preview for this file. Convert can still change it.",
            )

        self.tasks.submit(
            "main",
            lambda: preview.preview_pdf(f, out, cancel),
            self._open_pages_later,
            failed,
            lane="pdf",
        )

    def _open_pages_later(self, pdf_path: Path) -> None:
        self._open_pages(pdf_path)

    def _show_text(self, f: DetectedFile) -> None:
        def done(result: tuple[str, bool]) -> None:
            text, cut = result
            self.text.setPlainText(text)
            self.stack.setCurrentWidget(self.text)
            lines = text.count("\n") + 1
            self.details = f"{lines:,} lines" + (" (first part shown)" if cut else "")
            self._update_details()

        self._show_info("document", "Opening…")
        self.tasks.submit(
            "main", lambda: preview.read_text(f.path), done, self._failed("Couldn't read the text.")
        )

    def _show_model(self, f: DetectedFile) -> None:
        from umc.gui.editor3d.gl_view import ModelGLView, gl_problem

        problem = gl_problem()
        if problem:
            self._show_info("cube", "The 3D view needs OpenGL, which isn't working here.")
            return
        if self._model_view is None:
            self._model_view = ModelGLView()
            self.stack.addWidget(self._model_view)
        self._show_info("cube", "Opening…")

        def load():
            from umc.editor3d.mesh import load_view_model

            model = load_view_model(f.path, f.ext)
            model.triangle_buffer()
            model.edge_buffer()
            return model

        def done(model) -> None:
            self._model_view.set_model(model)
            self.stack.setCurrentWidget(self._model_view)
            w, d, h = model.size
            self.details = f"{w:.1f} × {d:.1f} × {h:.1f} mm"
            self._update_details()

        self.tasks.submit("main", load, done, self._failed("This 3D model can't be opened."))

    # ------------------------------------------------------------ bottom bar
    def _update_details(self) -> None:
        parts = [KIND_NAMES.get(self.kind, "File")]
        if self.details:
            parts.append(self.details)
        if self.items and 0 <= self.list.currentRow() < len(self.items):
            with contextlib.suppress(OSError):
                parts.append(fmt_size(self.items[self.list.currentRow()].stat().st_size))
        if self.items:
            parts.append(f"{self.list.currentRow() + 1} of {len(self.items)}")
        self.detail_label.setText(" · ".join(parts))

    def _on_duration(self, ms: int) -> None:
        if self.kind in (vf.VIDEO, vf.AUDIO) and ms > 0:
            self.details = _clock(ms)
            self._update_details()

    def _update_buttons(self) -> None:
        self.convert_button.setEnabled(self.current is not None)
        editor = vf.editor_for(self.kind)
        can_edit = editor is not None and editor.available and self.current is not None
        self.edit_button.setEnabled(can_edit)
        if can_edit:
            self.edit_button.setToolTip(f"Open this file in the {editor.title.lower()}")
        elif editor is not None:
            self.edit_button.setToolTip(f"The {editor.title.lower()} is coming soon")
        else:
            self.edit_button.setToolTip("There's no editor for this kind of file yet")

    def _convert(self) -> None:
        if self.current is not None:
            self.convert_requested.emit(self.current.path)

    def _edit(self) -> None:
        if self.current is not None and self.edit_button.isEnabled():
            self.edit_requested.emit(self.current.path)

    def _space(self) -> None:
        if self.stack.currentWidget() is self.media:
            self.media.toggle()
        else:
            self.step(1)

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _leave_full_screen(self) -> None:
        if self.isFullScreen():
            self.showNormal()

    # ------------------------------------------------------------ side list thumbnails
    def _request_thumbs(self) -> None:
        """Pictures in view (and a screen ahead) get a real thumbnail; others keep the icon."""
        if not self.items:
            return
        first = self.list.indexAt(self.list.viewport().rect().topLeft()).row()
        first = max(first, 0)
        rows = self.list.viewport().height() // (THUMB + 10) + 2
        for row in range(max(0, first - rows), min(len(self.items), first + 2 * rows)):
            path = self.items[row]
            if path in self._thumbs_requested or vf.kind_of_path(path) != vf.IMAGE:
                continue
            self._thumbs_requested.add(path)

            def done(image: QImage | None, row: int = row, path: Path = path) -> None:
                if image is not None and row < len(self.items) and self.items[row] == path:
                    self.list.item(row).setIcon(QIcon(QPixmap.fromImage(image)))

            self.tasks.submit("thumb", lambda p=path: _thumbnail(p), done, exclusive=False)

    # ------------------------------------------------------------ closing
    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_current()
        self.tasks.shutdown()  # doesn't wait: a slow preview conversion mustn't freeze closing
        folder, self._preview_dir = self._preview_dir, None
        if folder is not None:
            tasks = self.tasks

            def remove_when_idle() -> None:  # after any conversion still writing into it
                tasks.wait(120_000)
                shutil.rmtree(folder, ignore_errors=True)

            threading.Thread(target=remove_when_idle, name="umc-view-cleanup", daemon=True).start()
        super().closeEvent(event)
