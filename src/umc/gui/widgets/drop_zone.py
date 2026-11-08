"""Large drop area that also opens a file picker when clicked."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout


class DropZone(QFrame):
    files_dropped = Signal(list)  # list[str]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)
        self.icon = QLabel("⬆")
        self.icon.setObjectName("DropIcon")
        self.title = QLabel("Drop files here")
        self.title.setObjectName("DropTitle")
        self.subtitle = QLabel("or click to browse — documents, images or videos")
        self.subtitle.setObjectName("Muted")
        for w in (self.icon, self.title, self.subtitle):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(w)
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        """Compact mode once files are loaded: a slim "add more" bar."""
        self.icon.setVisible(not compact)
        self.subtitle.setVisible(not compact)
        self.title.setText("+  Add more files" if compact else "Drop files here")
        self.setMinimumHeight(52 if compact else 190)
        self.setMaximumHeight(52 if compact else 16777215)

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to convert")
            if paths:
                self.files_dropped.emit(paths)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_hover(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
