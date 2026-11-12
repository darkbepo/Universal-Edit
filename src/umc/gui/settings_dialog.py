"""App settings (the cog in the top bar): Windows right-click "Convert" and "Open with"."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.tools import shell_integration as shell

log = logging.getLogger(__name__)

ITEMS = (
    (
        "context-menu",
        'Add "Convert with Universal Edit" to the right-click menu',
        "For every kind of file the converter reads. On Windows 11 it's under "
        '"Show more options".',
    ),
    (
        "open-with",
        'Show Universal Edit in "Open with"',
        "Pictures, video, music, PDFs, documents and 3D models open in the viewer. "
        "To make it the default, choose it in Windows' Default apps.",
    ),
)


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, registry=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.registry = registry if registry is not None else shell.registry()
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        head = QLabel("Windows", objectName="CardTitle")
        lay.addWidget(head)
        self.boxes: dict[str, QCheckBox] = {}
        for key, label, help_text in ITEMS:
            box = QCheckBox(label)
            box.setChecked(shell.feature_on(key, self.registry) if self.registry else False)
            box.toggled.connect(lambda on, k=key: self._toggle(k, on))
            note = QLabel(help_text, objectName="Help")
            note.setWordWrap(True)
            note.setContentsMargins(26, 0, 0, 8)
            lay.addWidget(box)
            lay.addWidget(note)
            self.boxes[key] = box
        self.default_apps = QPushButton("Choose default apps…", objectName="Small")
        self.default_apps.setToolTip("Opens Windows' Default apps page")
        self.default_apps.clicked.connect(shell.open_default_apps)
        lay.addWidget(self.default_apps, 0, Qt.AlignmentFlag.AlignLeft)
        self.message = QLabel(objectName="Error")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        if self.registry is None:
            for box in self.boxes.values():
                box.setEnabled(False)
            self.default_apps.setEnabled(False)
            self.message.setObjectName("Help")
            self.message.setText("These settings are only available on Windows.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _toggle(self, key: str, on: bool) -> None:
        try:
            shell.set_feature(key, on, self.registry)
            self.message.setText("")
        except OSError as exc:
            log.exception("settings: %s", key)
            self.message.setText(f"That didn't work: {exc}")
            box = self.boxes[key]
            box.blockSignals(True)
            box.setChecked(not on)
            box.blockSignals(False)
