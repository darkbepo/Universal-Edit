"""A layer effect's settings window (drop shadow, glow, outline, colour overlay).

Opening it switches the effect on for the selected layer; every change shows at once and a run
of changes is one undo step (``ops.tweak_layer_fx``). Choosing another layer, or undoing, shows
that layer's settings. "Remove" switches the effect off, "Done" closes the window.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from umc.editor import layerfx
from umc.gui.widgets.settings_panel import SettingsPanel


class FxWindow(QDialog):
    def __init__(self, page, key: str) -> None:
        super().__init__(page)
        self.page, self.key = page, key
        self.setWindowTitle(layerfx.LABELS[key])
        self.setObjectName(f"fx_{key}")
        lay = QVBoxLayout(self)
        self.form = SettingsPanel(self)
        self.form.set_schema(layerfx.OPTIONS[key], "", self._layer_values())
        self.form.changed.connect(self._changed)
        lay.addWidget(self.form)
        row = QHBoxLayout()
        row.addStretch(1)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        done = QPushButton("Done")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        row.addWidget(self.remove)
        row.addWidget(done)
        lay.addLayout(row)
        if not self._layer_values()[key]:  # opened to add it: on with these settings
            self._apply({**self.form.values(), key: True})

    def _layer_fx(self):
        doc = self.page.doc
        return doc.active_layer.fx if doc is not None and doc.layers else None

    def _layer_values(self) -> dict:
        return layerfx.window_values(self._layer_fx(), self.key)

    def _apply(self, values: dict) -> None:
        self.page.tweak_fx(self.key, values)
        self.sync()

    def _changed(self) -> None:
        self._apply(self.form.values())

    def _remove(self) -> None:
        self._apply({self.key: False})

    def sync(self) -> None:
        """Show the selected layer's settings (after an undo, or another layer chosen)."""
        values = self._layer_values()
        if values != self.form.values():
            self.form.set_values(values)
