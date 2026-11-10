"""Settings of the selected item (or of the project when nothing is selected).

The inspector never changes the project: it emits ``changed(dict)`` with the fields that differ
from the shown item, and ``committed()`` once the edit is finished; the page turns both into one
undo step.
"""

from __future__ import annotations

import dataclasses
from functools import cache
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFontComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from umc.core.options import option_specs
from umc.gui.widgets.settings_panel import SettingsPanel
from umc.videoedit.project import (
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    TextItem,
    Transition,
    VideoClip,
    find,
)

_TITLES = {
    ProjectSettings: "Project",
    VideoClip: "Video clip",
    PictureClip: "Picture",
    AudioClip: "Music",
    TextItem: "Text",
    Transition: "Transition",
}


@cache
def _schema(cls: type) -> type:
    """The form for ``cls``: its ``opt()`` fields; a text's font has its own font box."""
    if cls is not TextItem:
        return cls
    fields = [
        (f.name, f.type, dataclasses.field(default=f.default, metadata=f.metadata))
        for f in dataclasses.fields(cls)
        if "umc_spec" in f.metadata and f.name != "font"
    ]
    return dataclasses.make_dataclass("TextItemFields", fields)


def target_of(project: Project, item_id: str | None) -> Any:
    """The object the inspector edits for a selection id."""
    if item_id is None:
        return project.settings
    if item_id.startswith("tr:"):
        return find(project, item_id[3:])[3].transition_in
    return find(project, item_id)[3]


def field_names(obj: Any) -> list[str]:
    return [s.name for s in option_specs(type(obj))]


def change_label(obj: Any, changes: dict[str, Any]) -> str:
    if isinstance(obj, Transition):
        return "Change transition"
    labels = {s.name: s.label for s in option_specs(type(obj))}
    if len(changes) == 1:
        (name,) = changes
        return f"Change {labels.get(name, name).lower()}"
    return "Change settings"


class Inspector(QWidget):
    changed = Signal(dict)
    committed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = QLabel(objectName="Section")
        lay.addWidget(self.title)
        self.font_row = QWidget()
        fr = QHBoxLayout(self.font_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(QLabel("Font"))
        self.font_box = QFontComboBox()
        self.font_box.currentFontChanged.connect(self._font_changed)
        fr.addWidget(self.font_box, 1)
        lay.addWidget(self.font_row)
        self.panel = SettingsPanel()
        self.panel.changed.connect(self._panel_changed)
        self.panel.committed.connect(self.committed.emit)
        lay.addWidget(self.panel)
        lay.addStretch(1)
        self.item_id: str | None = None
        self.target: Any = None
        self._cls: type | None = None
        self._values: dict[str, Any] = {}
        self._loading = False

    def show_item(self, project: Project | None, item_id: str | None) -> None:
        """Show the item's settings (the project's when ``item_id`` is None)."""
        if project is None:
            self.item_id, self.target, self._cls = None, None, None
            self.title.setText("")
            self.font_row.hide()
            self.panel.set_schema(None, "", {})
            return
        try:
            target = target_of(project, item_id)
        except KeyError:
            item_id, target = None, project.settings
        values = {name: getattr(target, name) for name in field_names(target)}
        cls = type(target)
        self._loading = True
        try:
            if cls is self._cls and item_id == self.item_id:
                self.panel.set_values(values)  # same item: keep the widgets (and the focus)
            else:
                self.panel.set_schema(_schema(cls), "", values)
            self.title.setText(_TITLES.get(cls, "").upper())
            self.font_row.setVisible(cls is TextItem)
            if cls is TextItem:
                self.font_box.setCurrentFont(QFont(values["font"]))
        finally:
            self._loading = False
        self.item_id, self.target, self._cls, self._values = item_id, target, cls, values

    def changes(self) -> dict[str, Any]:
        """Fields that differ from the shown item."""
        now = dict(self.panel.values())
        if self._cls is TextItem:
            now["font"] = self.font_box.currentFont().family()
        return {k: v for k, v in now.items() if k in self._values and v != self._values[k]}

    def _panel_changed(self) -> None:
        if not self._loading:
            self.changed.emit(self.changes())

    def _font_changed(self, _font: QFont) -> None:
        if self._loading or self._cls is not TextItem:
            return
        self.changed.emit(self.changes())
        self.committed.emit()
