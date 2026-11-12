"""Large drop area that also opens a file picker when clicked."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QBoxLayout, QFileDialog, QFrame, QLabel, QSizePolicy, QVBoxLayout

from umc.gui import icons, theme
from umc.gui.widgets.orbit import Orbit

SUBTITLE = "Images, video, audio, documents, 3D models, fonts and more"


class DropZone(QFrame):
    files_dropped = Signal(list)  # list[str]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Drop files or folders here, or click to choose files")
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._row.setSpacing(6)
        self._row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(self._row)
        self.icon = QLabel(objectName="DropIcon")
        self.title = QLabel(objectName="DropTitle")
        self.subtitle = QLabel(SUBTITLE, objectName="Muted")
        self.subtitle.setWordWrap(True)
        self.subtitle.setMinimumWidth(240)
        for w in (self.icon, self.title, self.subtitle):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._row.addWidget(w, 0, Qt.AlignmentFlag.AlignCenter)
        self.compact = False
        # file-type badges orbiting the text while the box is big and empty
        self.orbit = Orbit(self, lambda: [self.icon, self.title, self.subtitle])
        self.set_compact(False)

    def set_compact(self, compact: bool) -> None:
        """Compact mode once files are loaded: a slim "add more" bar."""
        self.compact = compact
        self.setProperty("compact", compact)
        self.style().unpolish(self)
        self.style().polish(self)
        self.subtitle.setVisible(not compact)
        self._row.setDirection(
            QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom
        )
        self._row.setSpacing(8 if compact else 6)
        self.orbit.set_active(not compact)
        self.title.setText("Add more files" if compact else "Drop files here or click to browse")
        if compact:
            self.setMinimumHeight(48)
            self.setMaximumHeight(48)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        else:
            self.setMinimumHeight(220)
            self.setMaximumHeight(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.retint()

    def retint(self) -> None:
        pal = theme.palette()
        name, size = ("plus", 18) if self.compact else ("upload", 34)
        self.icon.setPixmap(icons.pixmap(name, size, pal["accent"]))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)  # the frame (stylesheet); the text labels draw on top
        p = QPainter(self)
        self.orbit.paint(p)
        p.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.orbit.update_timer()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.orbit.update_timer()

    def drop_with_burst(self, done) -> None:
        """A drop on the big empty box: the badges burst outward, then ``done``."""
        self._set_hover(False)
        self.orbit.set_pull(False)
        if self.compact:
            done()
        else:
            self.orbit.burst_then(done)

    def _set_hover(self, on: bool) -> None:
        self.orbit.set_pull(on)
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
            event.acceptProposedAction()
            self.drop_with_burst(lambda: self.files_dropped.emit(paths))
