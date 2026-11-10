"""The project's files: thumbnail, name and duration. Drag a row onto the timeline."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QPushButton

from umc.gui import theme
from umc.gui.videoedit.preview_view import format_time
from umc.gui.videoedit.timeline import SOURCE_MIME
from umc.videoedit.project import Source

ICON = QSize(64, 36)
_KIND_TOKEN = {"video": "clip_video", "image": "clip_picture", "audio": "clip_audio"}
_KIND_LABEL = {"video": "Video", "image": "Picture", "audio": "Audio"}


def _plain_icon(kind: str) -> QIcon:
    pm = QPixmap(ICON)
    pm.fill(QColor(theme.palette()[_KIND_TOKEN.get(kind, "clip_video")]))
    return QIcon(pm)


class MediaList(QListWidget):
    add_requested = Signal(str)  # source id, added at the playhead
    files_requested = Signal()
    text_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaList")
        self.setIconSize(ICON)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(True)
        self.itemDoubleClicked.connect(self._double_clicked)
        # The page puts these buttons under the list.
        self.add_button = QPushButton("+ Add files", objectName="Small")
        self.add_button.setToolTip("Add videos, pictures or music to the project")
        self.add_button.clicked.connect(self.files_requested.emit)
        self.text_button = QPushButton("+ Text", objectName="Small")
        self.text_button.setToolTip("Add a text at the playhead")
        self.text_button.clicked.connect(self.text_requested.emit)
        self._sources: tuple[Source, ...] = ()

    def set_sources(self, sources: Sequence[Source]) -> None:
        sources = tuple(sources)
        if sources == self._sources:
            return
        self._sources = sources
        current = self.current_source_id()
        self.clear()
        for src in sources:
            item = QListWidgetItem(_plain_icon(src.kind), self._text(src))
            item.setData(Qt.ItemDataRole.UserRole, src.id)
            item.setToolTip(str(src.path))
            self.addItem(item)
            if src.id == current:
                self.setCurrentItem(item)

    @staticmethod
    def _text(src: Source) -> str:
        detail = "Picture" if src.kind == "image" else format_time(src.duration)
        if src.kind == "audio":
            detail = f"{_KIND_LABEL['audio']} · {detail}"
        return f"{src.path.name}\n{detail}"

    def set_thumbnail(self, source_id: str, image: QImage) -> None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == source_id:
                item.setIcon(QIcon(QPixmap.fromImage(image)))

    def current_source_id(self) -> str | None:
        item = self.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.add_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    # ---- dragging -------------------------------------------------------------------------
    def mimeTypes(self) -> list[str]:  # noqa: N802
        return [SOURCE_MIME]

    def mimeData(self, items) -> QMimeData:  # noqa: N802
        md = QMimeData()
        if items:
            md.setData(SOURCE_MIME, str(items[0].data(Qt.ItemDataRole.UserRole)).encode("utf-8"))
        return md

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.DropAction.CopyAction
