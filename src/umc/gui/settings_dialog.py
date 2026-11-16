"""App settings (the cog in the top bar): theme, Windows right-click "Quick Convert" and "Open
with" (on Linux: Quick Convert in Thunar), editor add-ons."""

from __future__ import annotations

import logging
import sys

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
        'Add "Quick Convert" to the right-click menu',
        "Pick a format and the file is converted next to the original, or open it in "
        'Universal Edit. On Windows 11 it\'s under "Show more options".',
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
        self.thunar_box: QCheckBox | None = None
        if self.registry is None:
            for box in (*self.boxes.values(), self.default_apps):
                box.setEnabled(False)
            if sys.platform.startswith("linux"):
                for w in (head, *self.boxes.values(), self.default_apps):
                    w.hide()
                for i in range(lay.count()):  # their help lines too
                    w = lay.itemAt(i).widget()
                    if isinstance(w, QLabel) and w.objectName() == "Help":
                        w.hide()
                self._linux_section(lay)
            else:
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

    def _linux_section(self, lay: QVBoxLayout) -> None:
        from umc.tools import linux_filemanagers as lf

        box = QCheckBox('Add "Quick Convert" to Thunar\'s right-click menu')
        box.setChecked(lf.thunar_registered())
        box.setEnabled(lf.thunar_present())
        box.toggled.connect(self._toggle_thunar)
        note = QLabel(
            "GNOME Files (Nautilus), Dolphin and Nemo get Quick Convert from the installed "
            "package. Thunar keeps its menu entries in your own settings, so it's switched here."
            if lf.thunar_present()
            else "Thunar isn't installed. GNOME Files (Nautilus), Dolphin and Nemo get Quick "
            "Convert from the installed package.",
            objectName="Help",
        )
        note.setWordWrap(True)
        note.setContentsMargins(26, 0, 0, 8)
        from umc.tools import linux_integration as li

        viewer = QCheckBox("Open pictures, video, music and documents in Universal Edit's viewer")
        viewer.setChecked(li.is_default_viewer())
        viewer.toggled.connect(self._toggle_default_viewer)
        viewer_note = QLabel(
            "Makes the viewer the default app for every kind of file it shows. Switching it "
            "off (or removing Universal Edit) gives your earlier choices back.",
            objectName="Help",
        )
        viewer_note.setWordWrap(True)
        viewer_note.setContentsMargins(26, 0, 0, 8)
        for w in (
            QLabel("File manager", objectName="CardTitle"),
            box,
            note,
            viewer,
            viewer_note,
        ):
            lay.insertWidget(lay.indexOf(self.message), w)  # above the message line
        self.thunar_box = box
        self.viewer_box = viewer

    def _toggle_default_viewer(self, on: bool) -> None:
        from umc.tools import linux_integration as li

        try:
            if on:
                li.set_default_viewer()
            else:
                li.restore_defaults()
            self.message.setText("")
        except OSError as exc:
            log.exception("settings: default viewer")
            self.message.setText(f"That didn't work: {exc}")
            self.viewer_box.blockSignals(True)
            self.viewer_box.setChecked(not on)
            self.viewer_box.blockSignals(False)

    def _toggle_thunar(self, on: bool) -> None:
        from PySide6.QtCore import QSettings

        from umc.tools import linux_filemanagers as lf

        QSettings().setValue(lf.THUNAR_SETTING, on)
        if lf.set_thunar(on):
            self.message.setText("")
        else:
            self.message.setText("Thunar's settings file couldn't be changed (see the log).")
            assert self.thunar_box is not None
            self.thunar_box.blockSignals(True)
            self.thunar_box.setChecked(not on)
            self.thunar_box.blockSignals(False)

    def _refresh_addons(self) -> None:
        for addon_id, (state, button) in self.addon_rows.items():
            if addons.from_source():
                state.setText("included" if addons.bundled() else "included (running from source)")
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
