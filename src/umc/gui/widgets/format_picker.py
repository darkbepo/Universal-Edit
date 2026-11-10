"""Output formats as foldable category cards with icon tiles, in as many columns as fit."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QTransform
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.core.registry import Target
from umc.gui import icons, theme
from umc.gui.widgets.flow_layout import FlowLayout

CARD_MIN_W = 340  # a column per this many pixels of width
TILE = QSize(78, 64)


class CategoryCard(QFrame):
    """A titled card: header (chevron, icon, name, badge) and a wrapping row of tiles."""

    toggled = Signal(str, bool)  # group, open

    def __init__(self, group: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CategoryCard")
        self.group = group
        self.tiles: list[QToolButton] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)
        self.header = QToolButton(objectName="CardHeader")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setText(group)
        self.header.setIconSize(QSize(16, 16))
        self.header.clicked.connect(lambda: self.set_open(not self.is_open, emit=True))
        self.badge = QLabel(objectName="Badge")
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        head.addWidget(self.header)
        head.addStretch(1)
        head.addWidget(self.badge)
        lay.addLayout(head)
        self.body = QWidget(objectName="CardBody")
        self.flow = FlowLayout(self.body, spacing=6)
        lay.addWidget(self.body)
        self.is_open = True
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def add_tile(self, tile: QToolButton) -> None:
        self.tiles.append(tile)
        self.flow.addWidget(tile)

    def set_open(self, open_: bool, emit: bool = False) -> None:
        self.is_open = open_
        self.body.setVisible(open_)
        self.refresh()
        if emit:
            self.toggled.emit(self.group, open_)

    def refresh(self) -> None:
        pal = theme.palette()
        self.header.setIcon(_rotated_chevron(pal["muted"], self.is_open))
        chosen = next((t.text() for t in self.tiles if t.isChecked()), "")
        if chosen:
            self.badge.setText(chosen)
            self.badge.setObjectName("Badge")
        elif not self.is_open:
            self.badge.setText(str(len(self.tiles)))
            self.badge.setObjectName("Count")
        else:
            self.badge.setText("")
        self.badge.setVisible(bool(self.badge.text()))
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)


def _rotated_chevron(color: str, open_: bool) -> QIcon:
    pm = icons.pixmap("chevron", 16, color)
    if not open_:
        pm = pm.transformed(QTransform().rotate(-90))
    return QIcon(pm)


class FormatPicker(QWidget):
    target_changed = Signal(object)  # Target | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(10)
        self._outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._columns: list[QVBoxLayout] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._targets: dict[int, Target] = {}
        self.cards: list[CategoryCard] = []
        self._ncols = 0
        self._store = QSettings()
        self._group.idClicked.connect(self._on_clicked)

    # ------------------------------------------------------------------ building
    def set_targets(self, targets: list[Target], keep_id: str | None = None) -> None:
        for card in self.cards:
            card.hide()
            card.deleteLater()
        self.cards = []
        for b in self._group.buttons():
            self._group.removeButton(b)
        self._targets.clear()

        folded = set(self._store.value("folded_groups", [], list) or [])
        pal = theme.palette()
        restored = None
        cards: dict[str, CategoryCard] = {}
        for idx, t in enumerate(targets):
            card = cards.get(t.group)
            if card is None:
                card = cards[t.group] = CategoryCard(t.group)
                card.toggled.connect(self._remember_fold)
            tile = QToolButton(objectName="Tile")
            tile.setText(t.label)
            tile.setCheckable(True)
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setFixedHeight(TILE.height())
            tile.setMinimumWidth(TILE.width())
            tile.setIconSize(QSize(22, 22))
            tile.setIcon(icons.icon(icons.group_icon(t.group), pal["muted"], 22, pal["accent"]))
            tile.setToolTip(t.hint or t.label)
            tile.setProperty("target", t.id)
            self._group.addButton(tile, idx)
            self._targets[idx] = t
            card.add_tile(tile)
            if t.id == keep_id:
                restored = (tile, t)
        self.cards = list(cards.values())
        if restored:
            restored[0].setChecked(True)
        for card in self.cards:
            card.set_open(card.group not in folded or bool(restored and restored[0] in card.tiles))
        self._ncols = 0
        self._relayout()
        self.target_changed.emit(restored[1] if restored else None)

    def _relayout(self) -> None:
        n = max(1, min(len(self.cards) or 1, self.width() // CARD_MIN_W))
        self._ncols = n
        for col in self._columns:
            while col.count():
                col.takeAt(0)
            self._outer.removeItem(col)
            col.deleteLater()
        self._columns = []
        heights = [0] * n
        for _ in range(n):
            col = QVBoxLayout()
            col.setSpacing(10)
            col.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._outer.addLayout(col, 1)
            self._columns.append(col)
        # Greedy "masonry": each card goes to the currently shortest column (in tile rows).
        per_row = max(1, (self.width() // n - 24) // (TILE.width() + 6))
        for card in self.cards:
            i = heights.index(min(heights))
            self._columns[i].addWidget(card)
            card.setParent(self)
            card.show()
            heights[i] += 1 + (-(-len(card.tiles) // per_row) if card.is_open else 0)
        for col in self._columns:
            col.addStretch(1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.cards and max(1, min(len(self.cards), self.width() // CARD_MIN_W)) != self._ncols:
            self._relayout()

    # ------------------------------------------------------------------ state
    def _on_clicked(self, idx: int) -> None:
        for card in self.cards:
            card.refresh()
        self.target_changed.emit(self._targets[idx])

    def _remember_fold(self, group: str, open_: bool) -> None:
        folded = set(self._store.value("folded_groups", [], list) or [])
        (folded.discard if open_ else folded.add)(group)
        self._store.setValue("folded_groups", sorted(folded))

    def current(self) -> Target | None:
        return self._targets.get(self._group.checkedId())

    def tile_for(self, target_id: str) -> QToolButton | None:
        for i, t in self._targets.items():
            if t.id == target_id:
                return self._group.button(i)
        return None

    def card_for(self, group: str) -> CategoryCard | None:
        return next((c for c in self.cards if c.group == group), None)

    def retint(self) -> None:
        pal = theme.palette()
        for i, t in self._targets.items():
            self._group.button(i).setIcon(
                icons.icon(icons.group_icon(t.group), pal["muted"], 22, pal["accent"])
            )
        for card in self.cards:
            card.refresh()
