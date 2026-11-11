"""The editor's tool rail: tools and actions in groups (``umc.editor.rail``), with names shown
by default; the chevron button at the top narrows it to icons only (remembered)."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.editor.rail import MENUS, RAIL
from umc.editor.tools import TOOLS
from umc.gui import icons, theme

WIDE, NARROW = 184, 60
_KEY = "editor/rail_narrow"


class ToolRail(QScrollArea):
    tool_selected = Signal(str)
    action_triggered = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolRail")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # never take the keyboard away from the canvas (Space to pan, arrows to nudge)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tools = {cls.id: cls for cls in TOOLS}
        color = theme.palette()["text"]
        accent = theme.palette()["accent"]
        host = QWidget(objectName="ToolRailHost")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(6, 6, 6, 8)
        lay.setSpacing(1)
        self.toggle = QToolButton(objectName="RailToggle")
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(lambda: self.set_narrow(not self.narrow))
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignRight)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        self._labels: list[QLabel] = []
        self._lines: list[QFrame] = []
        for g in RAIL:
            head = QLabel(g["group"].upper(), objectName="RailGroup")
            line = QFrame(objectName="RailLine")
            line.setFrameShape(QFrame.Shape.HLine)
            self._labels.append(head)
            self._lines.append(line)
            lay.addWidget(head)
            lay.addWidget(line)
            for kind, item_id, label, icon_name in g["items"]:
                b = QToolButton(objectName="RailButton")
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setIcon(icons.icon(icon_name, color, 20, accent))
                b.setIconSize(QSize(20, 20))
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                if kind == "tool":
                    cls = self._tools[item_id]
                    b.setText(cls.label)
                    b.setToolTip(f"{cls.label} ({cls.shortcut})")
                    b.setCheckable(True)
                    self._group.addButton(b)
                    b.clicked.connect(lambda _=False, i=item_id: self.tool_selected.emit(i))
                elif kind == "menu":
                    b.setText(label)
                    b.setToolTip(label)
                    menu = QMenu(b)
                    for entry in MENUS[item_id]:
                        if entry is None:
                            menu.addSeparator()
                        else:
                            op, text = entry
                            menu.addAction(text, lambda o=op: self.action_triggered.emit(o))
                    b.setMenu(menu)
                    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                else:
                    b.setText(label)
                    b.setToolTip(label)
                    b.clicked.connect(lambda _=False, i=item_id: self.action_triggered.emit(i))
                self.buttons[item_id] = b
                lay.addWidget(b)
        lay.addStretch(1)
        self.setWidget(host)
        self.narrow = False
        self.set_narrow(bool(QSettings().value(_KEY, False, type=bool)), remember=False)

    def set_narrow(self, narrow: bool, remember: bool = True) -> None:
        """Icons only (names as tooltips) or icons with names (the default)."""
        self.narrow = narrow
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if narrow
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        for b in self.buttons.values():
            b.setToolButtonStyle(style)
        for head in self._labels:
            head.setVisible(not narrow)
        for line in self._lines:
            line.setVisible(narrow)
        color = theme.palette()["muted"]
        self.toggle.setIcon(icons.icon("chevrons-right" if narrow else "chevrons-left", color, 16))
        self.toggle.setToolTip("Show tool names" if narrow else "Icons only")
        self.setFixedWidth(NARROW if narrow else WIDE)
        if remember:
            QSettings().setValue(_KEY, narrow)

    def set_panel_state(self, key: str, state: str) -> None:
        """Mark an icon whose window is open ("open") or minimised into it ("minimised")."""
        b = self.buttons.get(key)
        if b is None:
            return
        b.setProperty("panel", state)
        b.style().unpolish(b)
        b.style().polish(b)

    def select(self, tool_id: str) -> None:
        self.buttons[tool_id].setChecked(True)

    def current(self) -> str:
        return next((i for i, b in self.buttons.items() if b.isCheckable() and b.isChecked()), "")
