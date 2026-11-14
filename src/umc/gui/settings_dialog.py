"""App settings (the cog in the top bar): theme, Windows right-click "Convert" and "Open with",
editor add-ons."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from umc.core import addons
from umc.gui import theme
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
        lay.addWidget(QLabel("Theme", objectName="CardTitle"))
        theme_row = QHBoxLayout()
        self.theme_group = QButtonGroup(self)
        self.theme_buttons: dict[str, QRadioButton] = {}
        for key in theme.CHOICES:
            radio = QRadioButton(theme.LABELS[key])
            radio.setChecked(key == theme.choice())
            radio.toggled.connect(lambda on, k=key: on and theme.set_choice(k))
            self.theme_group.addButton(radio)
            theme_row.addWidget(radio)
            self.theme_buttons[key] = radio
        theme_row.addStretch(1)
        lay.addLayout(theme_row)
        lay.addSpacing(10)
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
        lay.addSpacing(10)
        lay.addWidget(QLabel("Editors", objectName="CardTitle"))
        self.addon_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        for addon in addons.ADDONS.values():
            row = QHBoxLayout()
            state = QLabel()
            button = QPushButton(objectName="Small")
            button.clicked.connect(lambda _c=False, a=addon.id: self._addon_clicked(a))
            row.addWidget(QLabel(addon.title))
            row.addWidget(state, 1)
            row.addWidget(button)
            lay.addLayout(row)
            self.addon_rows[addon.id] = (state, button)
        self._refresh_addons()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _refresh_addons(self) -> None:
        for addon_id, (state, button) in self.addon_rows.items():
            if addons.from_source():
                state.setText("included (running from source)")
                button.setText("Remove")
                button.setEnabled(False)
            elif addons.installed(addon_id):
                state.setText("installed")
                button.setText("Remove")
                button.setEnabled(True)
            else:
                size = addons.download_size(addon_id)
                state.setText(
                    f"not installed ({size / 1e6:.0f} MB)" if size >= 1e6 else "not installed"
                )
                button.setText("Install")
                button.setEnabled(True)
            state.setObjectName("Help")

    def _addon_clicked(self, addon_id: str) -> None:
        from umc.gui.addon_install import install_with_progress

        if addons.installed(addon_id):
            addons.remove(addon_id)
            self.message.setText(
                f"The {addons.ADDONS[addon_id].title.lower()} is removed. If it was open, it "
                "closes for good when Universal Edit restarts."
            )
        else:
            error = install_with_progress(self, addon_id)
            self.message.setText("" if not error or error == "cancelled" else error)
        self._refresh_addons()
        from umc.gui.addon_install import refresh_all_windows

        refresh_all_windows()

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
