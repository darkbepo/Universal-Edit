"""Renders an options dataclass (see umc.core.options) as a form, automatically."""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from umc.core.options import OptionSpec, option_specs


class _Field:
    """One option: its editor widget plus get/set helpers."""

    def __init__(self, spec: OptionSpec, on_change) -> None:
        self.spec = spec
        self.widget, self.get, self.set = _make_editor(spec, on_change)
        self.label = QLabel(spec.label)
        self.help = QLabel(spec.help) if spec.help else None
        if self.help:
            self.help.setObjectName("Help")
            self.help.setWordWrap(True)
        self.container = QWidget()
        lay = QVBoxLayout(self.container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.widget)
        if self.help:
            lay.addWidget(self.help)

    def set_visible(self, visible: bool) -> None:
        self.label.setVisible(visible)
        self.container.setVisible(visible)


def _make_editor(spec: OptionSpec, on_change):
    k = spec.kind
    if k == "bool":
        w = QCheckBox()
        w.setChecked(bool(spec.default))
        w.toggled.connect(on_change)
        return w, w.isChecked, w.setChecked

    if k == "choice":
        w = QComboBox()
        for value, label in spec.choices:
            w.addItem(label, value)

        def get_choice():
            return w.currentData()

        def set_choice(v):
            for i in range(w.count()):
                if w.itemData(i) == v:
                    w.setCurrentIndex(i)
                    return

        set_choice(spec.default)
        w.currentIndexChanged.connect(on_change)
        return w, get_choice, set_choice

    if k == "slider":
        step = spec.step or 1
        scale = 1 / step
        is_float = isinstance(spec.default, float)
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(round(spec.min * scale), round(spec.max * scale))
        value_label = QLabel()
        value_label.setMinimumWidth(64)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(slider, 1)
        lay.addWidget(value_label)

        def get_slider():
            v = slider.value() / scale
            return round(v, spec.decimals or 3) if is_float else int(round(v))

        def refresh():
            v = get_slider()
            text = f"{v:.{spec.decimals}f}" if is_float else str(v)
            value_label.setText(f"{text} {spec.unit}".strip())

        def set_slider(v):
            slider.setValue(round(float(v) * scale))
            refresh()

        slider.valueChanged.connect(lambda _: (refresh(), on_change()))
        set_slider(spec.default)
        return box, get_slider, set_slider

    if k in ("int", "float"):
        w = QSpinBox() if k == "int" else QDoubleSpinBox()
        if k == "float":
            w.setDecimals(spec.decimals or 2)
        w.setRange(
            spec.min if spec.min is not None else -1e9, spec.max if spec.max is not None else 1e9
        )
        if spec.step:
            w.setSingleStep(spec.step)
        if spec.unit:
            w.setSuffix(f" {spec.unit}")
        w.setValue(spec.default)
        w.valueChanged.connect(on_change)
        return w, w.value, w.setValue

    if k == "color":
        w = QPushButton()
        state = {"value": spec.default}

        def paint():
            c = state["value"]
            w.setText(f"  {c.upper()}")
            w.setStyleSheet(f"QPushButton {{ text-align: left; border-left: 22px solid {c}; }}")

        def pick():
            c = QColorDialog.getColor(QColor(state["value"]), w, spec.label)
            if c.isValid():
                state["value"] = c.name()
                paint()
                on_change()

        def set_color(v):
            state["value"] = v or "#ffffff"
            paint()

        w.clicked.connect(pick)
        paint()
        return w, lambda: state["value"], set_color

    if k == "file":
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setPlaceholderText("None")
        browse = QPushButton("Browse…")
        clear = QPushButton("✕")
        clear.setFixedWidth(36)
        lay.addWidget(edit, 1)
        lay.addWidget(browse)
        lay.addWidget(clear)

        def pick_file():
            path, _ = QFileDialog.getOpenFileName(box, spec.label, "", spec.file_filter)
            if path:
                edit.setText(path)

        browse.clicked.connect(pick_file)
        clear.clicked.connect(edit.clear)
        edit.textChanged.connect(on_change)
        edit.setText(spec.default)
        return box, edit.text, edit.setText

    # text / password
    w = QLineEdit()
    if k == "password":
        w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setText(str(spec.default))
    w.setPlaceholderText(str(spec.default) or "")
    w.textChanged.connect(on_change)
    return w, w.text, w.setText


class SettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._fields: dict[str, _Field] = {}
        self._target_id = ""
        self._building = False
        self._group_labels: list[tuple[QLabel, list[_Field]]] = []

    def set_schema(self, options_cls: type | None, target_id: str, values: dict[str, Any]) -> None:
        self._building = True
        while self._root.count():
            item = self._root.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._fields.clear()
        self._group_labels.clear()
        self._target_id = target_id
        if options_cls is None:
            self._building = False
            return

        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        current_group = None
        for spec in option_specs(options_cls):
            if spec.group and spec.group != current_group:
                current_group = spec.group
                heading = QLabel(spec.group.upper())
                heading.setObjectName("Section")
                form.addRow(heading)
                self._group_labels.append((heading, []))
            f = _Field(spec, self._on_change)
            if spec.name in values:
                with contextlib.suppress(TypeError, ValueError):
                    f.set(values[spec.name])
            form.addRow(f.label, f.container)
            self._fields[spec.name] = f
            if spec.group and self._group_labels:
                self._group_labels[-1][1].append(f)
        self._root.addWidget(form_host)
        self._building = False
        self._update_visibility()

    def values(self) -> dict[str, Any]:
        return {name: f.get() for name, f in self._fields.items()}

    def has_visible_fields(self) -> bool:
        vals = self.values()
        return any(f.spec.visible(vals, self._target_id) for f in self._fields.values())

    def _on_change(self, *_args) -> None:
        if self._building:
            return
        self._update_visibility()
        self.changed.emit()

    def _update_visibility(self) -> None:
        vals = self.values()
        for f in self._fields.values():
            f.set_visible(f.spec.visible(vals, self._target_id))
        for heading, fields in self._group_labels:
            heading.setVisible(any(f.spec.visible(vals, self._target_id) for f in fields))
