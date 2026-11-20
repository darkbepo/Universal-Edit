"""Editor tiles on the empty start screen: open an editor without a file.

Tiles come from ``core.editors.EDITORS``; editors that aren't ready are greyed out with
"Coming soon". Also the "New picture" window the image editor tile opens."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from umc.core import addons
from umc.core.editors import (
    COMING_SOON,
    EDITORS,
    MIN_TILE_W,
    TILE_GAP,
    EditorInfo,
    hub_rows,
    hub_tile_width,
)
from umc.editor.blank import BACKGROUNDS, PRESETS
from umc.editor.document import MAX_SIDE
from umc.gui import icons, theme

CUSTOM = "Custom size"
TILE_H = 84
ICON_BOX = 48


class _RowsLayout(QLayout):
    """Tiles in centred rows, as many per row as fit, spread evenly (``core.editors.hub_rows``);
    every tile is as wide as the longest row needs to fill the width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rows = len(hub_rows(len(self._items), width))
        return max(0, rows * TILE_H + (rows - 1) * TILE_GAP)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        return QSize(MIN_TILE_W, TILE_H)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        rows = hub_rows(len(self._items), rect.width())
        tile = int(hub_tile_width(rows, rect.width()))
        at, y = 0, rect.y()
        for n in rows:
            used = n * tile + (n - 1) * TILE_GAP
            x = rect.x() + (rect.width() - used) // 2
            for item in self._items[at : at + n]:
                item.setGeometry(QRect(x, y, tile, TILE_H))
                x += tile + TILE_GAP
            at += n
            y += TILE_H + TILE_GAP


class _TilesScroll(QScrollArea):
    """Frameless, vertical only; asks for the tiles' full height, takes less when it must."""

    def __init__(self, inner: QWidget) -> None:
        super().__init__(objectName="EditorHubScroll")
        self.setWidget(inner)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        inner.setAutoFillBackground(False)

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        inner = self.widget()
        height = inner.layout().heightForWidth(max(1, self.viewport().width()))
        hint.setHeight(max(height, TILE_H))  # the rows at the real width (not at the narrowest)
        return hint

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setHeight(TILE_H + 12)  # at least one row
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # a new width can mean another number of rows (its height)


class EditorTile(QPushButton):
    """A big icon on the left; title, description and a badge ("Coming soon" when not available,
    "Install" for an add-on not downloaded yet) on the right."""

    def __init__(self, info: EditorInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("EditorTile")
        self.setAccessibleName(info.title)
        self.setFixedHeight(TILE_H)
        self.setMinimumWidth(MIN_TILE_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setEnabled(info.available)
        if info.available:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"Open the {info.title.lower()} without a file")
        else:
            self.setToolTip(f"The {info.title.lower()} is coming soon")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        self.icon = QLabel(objectName="EditorTileIcon")
        self.icon.setFixedSize(ICON_BOX, ICON_BOX)
        self.icon.setProperty("soon", not info.available)  # greyed box (style.qss)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel(info.title, objectName="EditorTileTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.badge = QLabel(COMING_SOON if not info.available else "Install", objectName="Badge")
        head.addWidget(self.badge)
        text.addLayout(head)
        self.blurb = QLabel(info.blurb, objectName="Muted")
        self.blurb.setWordWrap(True)
        text.addWidget(self.blurb, 1, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(text, 1)
        for w in (self.icon, self.title, self.blurb, self.badge):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # clicks: the button
        self.refresh_install_state()
        self.retint()

    @property
    def needs_install(self) -> bool:
        info = self.info
        return info.available and addons.is_addon(info.id) and not addons.installed(info.id)

    def refresh_install_state(self) -> None:
        if not self.info.available:
            self.badge.setVisible(True)
            return
        self.badge.setVisible(self.needs_install)
        if self.needs_install:
            size = addons.download_size(self.info.id)
            more = f" (about {size / 1e6:.0f} MB)" if size >= 1e6 else ""
            self.setToolTip(f"Install the {self.info.title.lower()}{more}, then open it")
        else:
            self.setToolTip(f"Open the {self.info.title.lower()} without a file")

    def retint(self) -> None:
        pal = theme.palette()
        color = pal["accent"] if self.info.available else pal["border_strong"]
        self.icon.setPixmap(icons.pixmap(self.info.icon, 28, color))


class EditorHub(QFrame):
    """ "Or start in an editor" + one tile per editor. ``editor_chosen(id)`` on a click."""

    editor_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorHub")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)
        head = QLabel("Or start in an editor", objectName="CardTitle")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)
        inner = QWidget(objectName="EditorHubTiles")
        self._rows = _RowsLayout(inner)
        self.tiles: dict[str, EditorTile] = {}
        for info in EDITORS:
            tile = EditorTile(info)
            tile.clicked.connect(lambda _c=False, i=info.id: self.editor_chosen.emit(i))
            self._rows.addWidget(tile)
            self.tiles[info.id] = tile
        # a short window scrolls the tiles instead of cutting the last row off
        self.scroll = _TilesScroll(inner)
        lay.addWidget(self.scroll, 1)
        # never taller than its rows: the drop zone above takes the rest of the window
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def retint(self) -> None:
        for tile in self.tiles.values():
            tile.retint()

    def refresh_install_state(self) -> None:
        for tile in self.tiles.values():
            tile.refresh_install_state()


class NewPictureDialog(QDialog):
    """Size (a preset or custom) and background of a new picture."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New picture")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItems([p[0] for p in PRESETS] + [CUSTOM])
        self.width_box = QSpinBox()
        self.height_box = QSpinBox()
        for box in (self.width_box, self.height_box):
            box.setRange(1, MAX_SIDE)
            box.setSuffix(" px")
        form.addRow("Size", self.preset)
        form.addRow("Width", self.width_box)
        form.addRow("Height", self.height_box)
        self.backgrounds = QButtonGroup(self)
        bg_row = QHBoxLayout()
        for key, (label, _fill) in BACKGROUNDS.items():
            b = QRadioButton(label)
            b.setProperty("key", key)
            self.backgrounds.addButton(b)
            bg_row.addWidget(b)
            if key == "white":
                b.setChecked(True)
        bg_row.addStretch(1)
        form.addRow("Background", bg_row)
        lay.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.preset.currentIndexChanged.connect(self._on_preset)
        for box in (self.width_box, self.height_box):
            box.valueChanged.connect(self._on_size_typed)
        self._on_preset(0)

    def _on_preset(self, index: int) -> None:
        if index >= len(PRESETS):
            return  # custom: keep what's typed
        _label, w, h = PRESETS[index]
        for box, v in ((self.width_box, w), (self.height_box, h)):
            box.blockSignals(True)
            box.setValue(v)
            box.blockSignals(False)

    def _on_size_typed(self, _value: int) -> None:
        size = (self.width_box.value(), self.height_box.value())
        match = next((i for i, p in enumerate(PRESETS) if (p[1], p[2]) == size), len(PRESETS))
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(match)
        self.preset.blockSignals(False)

    def values(self) -> tuple[int, int, str]:
        """(width, height, background id)."""
        checked = self.backgrounds.checkedButton()
        return self.width_box.value(), self.height_box.value(), checked.property("key")
