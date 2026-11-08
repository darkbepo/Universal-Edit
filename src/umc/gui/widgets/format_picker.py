"""Grouped, wrapping "pill" buttons for choosing the output format."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget

from umc.core.registry import Target
from umc.gui.widgets.flow_layout import FlowLayout


class FormatPicker(QWidget):
    target_changed = Signal(object)  # Target | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._targets: dict[int, Target] = {}
        self._group.idClicked.connect(lambda i: self.target_changed.emit(self._targets[i]))

    def set_targets(self, targets: list[Target], keep_id: str | None = None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for b in self._group.buttons():
            self._group.removeButton(b)
        self._targets.clear()

        groups: dict[str, list[Target]] = {}
        for t in targets:
            groups.setdefault(t.group, []).append(t)
        restored = None
        idx = 0
        for name, items in groups.items():
            heading = QLabel(name)
            heading.setObjectName("GroupHeading")
            self._layout.addWidget(heading)
            row = QWidget()
            flow = FlowLayout(row, spacing=6)
            for t in items:
                b = QPushButton(t.label)
                b.setObjectName("Pill")
                b.setCheckable(True)
                if t.hint:
                    b.setToolTip(t.hint)
                self._group.addButton(b, idx)
                self._targets[idx] = t
                flow.addWidget(b)
                if t.id == keep_id:
                    restored = (b, t)
                idx += 1
            self._layout.addWidget(row)
        if restored:
            restored[0].setChecked(True)
        self.target_changed.emit(restored[1] if restored else None)

    def current(self) -> Target | None:
        i = self._group.checkedId()
        return self._targets.get(i)
