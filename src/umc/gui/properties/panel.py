"""The right column in Properties mode: "About this file" for one of the added files.

A file picker on top when there are several; the sections (``properties.read.describe``) are read
off the UI thread — PDFs on the PyMuPDF thread — and the newest request wins."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import DetectedFile
from umc.gui import icons, theme
from umc.gui.viewer.tasks import TaskRunner
from umc.properties.info import Section
from umc.properties.read import describe, uses_pdf_engine


LABEL_W = 110


class PropertiesPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesPanel")
        self.tasks = TaskRunner(self)
        self.files: list[DetectedFile] = []
        self.current: DetectedFile | None = None
        self.sections: list[Section] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.heading = QLabel("About this file", objectName="PaneTitle")
        head.addWidget(self.icon)
        head.addWidget(self.heading)
        head.addStretch(1)
        self.picker = QComboBox(objectName="PropertiesFile")
        self.picker.setAccessibleName("File")
        self.picker.setMinimumContentsLength(18)
        self.picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.picker.currentIndexChanged.connect(self._picked)
        head.addWidget(self.picker)
        lay.addLayout(head)
        self.status = QLabel(objectName="Muted")
        lay.addWidget(self.status)
        self.scroll = QScrollArea(objectName="PropertiesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget(objectName="PropertiesBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 4, 0)
        self.body_lay.setSpacing(10)
        self.body_lay.addStretch(1)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)
        self.retint()

    def retint(self) -> None:
        self.icon.setPixmap(icons.pixmap("info-circle", 18, theme.palette()["accent"]))

    # ------------------------------------------------------------------ files
    def set_files(self, files: list[DetectedFile]) -> None:
        """Keep showing the same file when it's still there (a file added or removed)."""
        keep = self.current
        self.files = list(files)
        self.picker.blockSignals(True)
        self.picker.clear()
        for f in self.files:
            self.picker.addItem(f.name)
        self.picker.setVisible(len(self.files) > 1)
        index = self.files.index(keep) if keep in self.files else 0
        self.picker.setCurrentIndex(index if self.files else -1)
        self.picker.blockSignals(False)
        if not self.files:
            self.current = None
            self._show([])
            self.status.clear()
            return
        if self.files[index] != self.current or not self.sections:
            self.show_file(index)

    def _picked(self, index: int) -> None:
        if 0 <= index < len(self.files):
            self.show_file(index)

    def show_file(self, index: int) -> None:
        f = self.files[index]
        self.current = f
        if self.picker.currentIndex() != index:
            self.picker.blockSignals(True)
            self.picker.setCurrentIndex(index)
            self.picker.blockSignals(False)
        self.status.setText(f"Reading {f.name}…")
        self.status.setVisible(True)
        self.tasks.submit(
            "describe",
            lambda: describe(f),
            self._loaded,
            self._failed,
            lane="pdf" if uses_pdf_engine(f) else "pool",
        )

    def _loaded(self, sections: list[Section]) -> None:
        self.status.clear()
        self.status.setVisible(False)
        self._show(sections)

    def _failed(self, message: str) -> None:
        self.status.setText(message or "The details of this file couldn't be read.")
        self._show([])

    # ------------------------------------------------------------------ drawing
    def _show(self, sections: list[Section]) -> None:
        self.sections = sections
        while self.body_lay.count() > 1:
            item = self.body_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for i, s in enumerate(sections):
            self.body_lay.insertWidget(i, self._card(s))

    @staticmethod
    def _card(s: Section) -> QFrame:
        card = QFrame(objectName="Card")
        card.setAccessibleName(s.title)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.setSpacing(6)
        cl.addWidget(QLabel(s.title, objectName="CardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, LABEL_W)  # values line up from card to card
        for row, (label, value) in enumerate(s.rows):
            name = QLabel(label, objectName="Muted")
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            text = QLabel(value, objectName="PropertyValue")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if label:
                grid.addWidget(name, row, 0)
                grid.addWidget(text, row, 1)
            else:
                grid.addWidget(text, row, 0, 1, 2)
        cl.addLayout(grid)
        return card

    def rows(self) -> dict[str, dict[str, str]]:
        """{section: {label: value}} of what's shown (tests)."""
        return {s.title: dict(s.rows) for s in self.sections}

    def shutdown(self) -> None:
        self.tasks.shutdown()
