"""Dialogs of the video editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.videoedit.projectfile import MissingSource


class LocateDialog(QDialog):
    """Missing project files: find each one, or remove it (and its clips) from the project.

    After ``exec()``, ``choices`` maps every missing source id to the file found, or None
    ("Remove from project", the default).
    """

    def __init__(self, missing: list[MissingSource], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing files")
        self.missing = list(missing)
        self.choices: dict[str, Path | None] = {m.source_id: None for m in self.missing}
        root = QVBoxLayout(self)
        intro = QLabel(
            "Some files of this project can't be found. Find them, or remove them (and the clips"
            " that use them) from the project."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        grid = QGridLayout()
        self._status: dict[str, QLabel] = {}
        for row, m in enumerate(self.missing):
            grid.addWidget(QLabel(f"“{m.path.name}”"), row, 0)
            status = QLabel("Will be removed", objectName="Muted")
            grid.addWidget(status, row, 1)
            self._status[m.source_id] = status
            find_button = QPushButton("Find file…", objectName="Small")
            find_button.clicked.connect(lambda _=False, m=m: self._find(m))
            grid.addWidget(find_button, row, 2)
            remove = QPushButton("Remove from project", objectName="Small")
            remove.clicked.connect(lambda _=False, m=m: self.choose(m.source_id, None))
            grid.addWidget(remove, row, 3)
        root.addLayout(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open project")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def choose(self, source_id: str, path: Path | None) -> None:
        self.choices[source_id] = path
        self._status[source_id].setText(f"Use “{path.name}”" if path else "Will be removed")

    def _find(self, m: MissingSource) -> None:
        folder = m.path.parent if m.path.parent.exists() else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self, f"Find “{m.path.name}”", str(folder / m.path.name), "All files (*)"
        )
        if path:
            self.choose(m.source_id, Path(path))
