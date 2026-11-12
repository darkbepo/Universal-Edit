"""Editor tiles on the empty start screen: open an editor without a file.

Tiles come from ``core.editors.EDITORS``; editors that aren't ready are greyed out with
"Coming soon". Also the "New picture" window the image editor tile opens."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from umc.core import addons
from umc.core.editors import COMING_SOON, EDITORS, EditorInfo
from umc.editor.blank import BACKGROUNDS, PRESETS
from umc.editor.document import MAX_SIDE
from umc.gui import icons, theme
from umc.gui.widgets.flow_layout import FlowLayout

CUSTOM = "Custom size"
TILE_W = 196


class EditorTile(QPushButton):
    """Icon, title and one line of description; a "Coming soon" badge when not available."""

    def __init__(self, info: EditorInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("EditorTile")
        self.setAccessibleName(info.title)
        self.setFixedSize(TILE_W, 118)
        self.setEnabled(info.available)
        if info.available:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"Open the {info.title.lower()} without a file")
        else:
            self.setToolTip(f"The {info.title.lower()} is coming soon")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.icon = QLabel()
        top.addWidget(self.icon)
        top.addStretch(1)
        # "Coming soon" (not ready) or "Install" (an add-on not downloaded yet)
        self.badge = QLabel(COMING_SOON if not info.available else "Install", objectName="Badge")
        top.addWidget(self.badge)
        lay.addLayout(top)
        self.title = QLabel(info.title, objectName="EditorTileTitle")
        self.blurb = QLabel(info.blurb, objectName="Muted")
        self.blurb.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.blurb, 1, Qt.AlignmentFlag.AlignTop)
        for w in (self.icon, self.title, self.blurb, self.badge):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # clicks: the button
        self.refresh_install_state()
        self.retint()

    @property
    def needs_install(self) -> bool:
        return self.info.available and not addons.installed(self.info.id)

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
        self.icon.setPixmap(icons.pixmap(self.info.icon, 26, color))


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
        holder = QWidget()
        self._flow = FlowLayout(holder, spacing=10)
        self.tiles: dict[str, EditorTile] = {}
        for info in EDITORS:
            tile = EditorTile(info)
            tile.clicked.connect(lambda _c=False, i=info.id: self.editor_chosen.emit(i))
            self._flow.addWidget(tile)
            self.tiles[info.id] = tile
        # as wide as one row of all tiles at most, so the row sits centred; narrower wraps
        holder.setMaximumWidth(len(EDITORS) * (TILE_W + 10))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(holder, 100)
        row.addStretch(1)
        lay.addLayout(row)

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
