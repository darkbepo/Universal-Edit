"""The 3D viewer's Arrange and Check side panels. They only show and collect choices; the page
changes the scene (one undo step per change)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from umc.editor3d.arrange import BEDS, DEFAULT_BED

CUSTOM = "Custom size"
PANEL_W = 320


def _spin(lo: float, hi: float, value: float, step: float, suffix: str = " mm") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)  # acts on Enter / leaving, not on every key
    return s


def _button(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text, objectName="Small")
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _heading(text: str) -> QLabel:
    return QLabel(text, objectName="Muted")


class ArrangePanel(QFrame):
    rotate = Signal(str, float)  # axis, degrees
    mirror = Signal(str)
    scale_by = Signal(float, float, float)
    size_to = Signal(str, float, bool)  # axis, mm, keep proportions
    move_to = Signal(float, float)  # X / Y of the centre
    centre = Signal()
    units = Signal(str)  # arrange.UNIT_FIXES key
    lay_flat = Signal(bool)  # waiting for a face click
    bed_changed = Signal(object)  # (name, w, d, h)
    done = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(PANEL_W)
        self._quiet = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Arrange", objectName="Title"))

        lay.addWidget(_heading("Printer"))
        self.bed_box = QComboBox()
        for name, w, d, h in BEDS:
            self.bed_box.addItem(f"{name} ({w:g} × {d:g} × {h:g})", (name, w, d, h))
        self.bed_box.addItem(CUSTOM, None)
        lay.addWidget(self.bed_box)
        self.custom = QWidget()
        cl = QHBoxLayout(self.custom)
        cl.setContentsMargins(0, 0, 0, 0)
        self.bed_w, self.bed_d, self.bed_h = (_spin(10, 2000, v, 10) for v in DEFAULT_BED[1:])
        for s in (self.bed_w, self.bed_d, self.bed_h):
            cl.addWidget(s)
            s.valueChanged.connect(self._bed_edited)
        lay.addWidget(self.custom)
        self.fit = QLabel("", objectName="Muted")
        self.fit.setWordWrap(True)
        lay.addWidget(self.fit)
        self.bed_box.currentIndexChanged.connect(self._bed_edited)

        lay.addWidget(_heading("Size"))
        size = QFormLayout()
        size.setVerticalSpacing(4)
        self.size_boxes: dict[str, QDoubleSpinBox] = {}
        for axis, label in (("x", "Width (X)"), ("y", "Depth (Y)"), ("z", "Height (Z)")):
            box = _spin(0.1, 100000, 10, 1)
            box.valueChanged.connect(lambda v, a=axis: self._size_typed(a, v))
            self.size_boxes[axis] = box
            size.addRow(label, box)
        self.keep = QCheckBox("Keep proportions")
        self.keep.setChecked(True)
        size.addRow("", self.keep)
        self.percent = _spin(1, 10000, 100, 10, " %")
        apply_pc = _button("Scale", "Scale by this percentage")
        apply_pc.clicked.connect(self._percent)
        row = QHBoxLayout()
        row.addWidget(self.percent)
        row.addWidget(apply_pc)
        size.addRow("Scale", row)
        lay.addLayout(size)
        units = QHBoxLayout()
        for key, text, tip in (
            ("inch", "Made in inches", "Make it 25.4× bigger (the file was drawn in inches)"),
            ("cm", "Made in cm", "Make it 10× bigger (the file was drawn in centimetres)"),
        ):
            b = _button(text, tip)
            b.clicked.connect(lambda _c=False, k=key: self.units.emit(k))
            units.addWidget(b)
        lay.addLayout(units)

        lay.addWidget(_heading("Turn"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        for r, axis in enumerate("xyz"):
            grid.addWidget(QLabel(axis.upper()), r, 0)
            for c, (deg, text) in enumerate(((-90, "−90°"), (90, "+90°"))):
                b = _button(text, f"Turn {deg:+d}° about {axis.upper()}")
                b.clicked.connect(lambda _c=False, a=axis, d=deg: self.rotate.emit(a, float(d)))
                grid.addWidget(b, r, c + 1)
            m = _button("Mirror", f"Mirror along {axis.upper()}")
            m.clicked.connect(lambda _c=False, a=axis: self.mirror.emit(a))
            grid.addWidget(m, r, 3)
        lay.addLayout(grid)
        turn = QHBoxLayout()
        self.angle = _spin(-360, 360, 15, 5, "°")
        self.angle_axis = QComboBox()
        self.angle_axis.addItems(["Z", "X", "Y"])
        go = _button("Turn", "Turn by this angle")
        go.clicked.connect(
            lambda: self.rotate.emit(self.angle_axis.currentText().lower(), self.angle.value())
        )
        turn.addWidget(self.angle)
        turn.addWidget(self.angle_axis)
        turn.addWidget(go)
        lay.addLayout(turn)
        self.flat_button = _button("Lay flat on a face…", "Then click the face to put on the bed")
        self.flat_button.setCheckable(True)
        self.flat_button.toggled.connect(self.lay_flat.emit)
        lay.addWidget(self.flat_button)

        lay.addWidget(_heading("Position (centre)"))
        pos = QHBoxLayout()
        self.pos_x = _spin(-5000, 5000, 0, 1)
        self.pos_y = _spin(-5000, 5000, 0, 1)
        for s in (self.pos_x, self.pos_y):
            s.valueChanged.connect(self._moved)
            pos.addWidget(s)
        centre = _button("Centre", "Put it in the middle of the bed")
        centre.clicked.connect(self.centre.emit)
        pos.addWidget(centre)
        lay.addLayout(pos)

        self.note = QLabel("", objectName="Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        lay.addStretch(1)
        done = QPushButton("Done", objectName="Primary")
        done.clicked.connect(self.done.emit)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(done)
        lay.addLayout(row)
        self._sync_custom()

    # ------------------------------------------------------------ bed
    def bed(self) -> tuple[str, float, float, float]:
        data = self.bed_box.currentData()
        if data is not None:
            return data
        return (CUSTOM, self.bed_w.value(), self.bed_d.value(), self.bed_h.value())

    def set_bed(self, bed) -> None:
        self._quiet = True
        try:
            name = bed[0]
            i = next((k for k in range(len(BEDS)) if BEDS[k][0] == name), -1)
            if i < 0:
                self.bed_box.setCurrentIndex(self.bed_box.count() - 1)
                for s, v in zip((self.bed_w, self.bed_d, self.bed_h), bed[1:], strict=True):
                    s.setValue(float(v))
            else:
                self.bed_box.setCurrentIndex(i)
        finally:
            self._quiet = False
        self._sync_custom()

    def _sync_custom(self) -> None:
        self.custom.setVisible(self.bed_box.currentData() is None)

    def _bed_edited(self, *_a) -> None:
        self._sync_custom()
        if not self._quiet:
            self.bed_changed.emit(self.bed())

    # ------------------------------------------------------------ the model's numbers
    def show_model(self, size, centre, fit_note: str) -> None:
        """Size (mm) and X / Y centre of the model now (no signals)."""
        self._quiet = True
        try:
            for axis, v in zip("xyz", size, strict=True):
                self.size_boxes[axis].setValue(max(float(v), 0.1))
            self.pos_x.setValue(float(centre[0]))
            self.pos_y.setValue(float(centre[1]))
        finally:
            self._quiet = False
        self.fit.setText(fit_note)

    def _size_typed(self, axis: str, value: float) -> None:
        if not self._quiet:
            self.size_to.emit(axis, value, self.keep.isChecked())

    def _percent(self) -> None:
        k = self.percent.value() / 100
        if k != 1:
            self.scale_by.emit(k, k, k)

    def _moved(self, *_a) -> None:
        if not self._quiet:
            self.move_to.emit(self.pos_x.value(), self.pos_y.value())

    def stop_lay_flat(self) -> None:
        self.flat_button.blockSignals(True)
        self.flat_button.setChecked(False)
        self.flat_button.blockSignals(False)


class CheckPanel(QFrame):
    repair = Signal()
    overhangs = Signal(bool, float)  # show, angle
    done = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(PANEL_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Check for printing", objectName="Title"))
        self.result = QLabel("Checking…")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.result)
        self.repair_button = QPushButton("Repair", objectName="Primary")
        self.repair_button.setToolTip(
            "Close holes, turn triangles to face outwards, remove empty, repeated and tiny bits"
        )
        self.repair_button.clicked.connect(self.repair.emit)
        lay.addWidget(self.repair_button)
        self.repaired = QLabel("", objectName="Muted")
        self.repaired.setWordWrap(True)
        lay.addWidget(self.repaired)

        lay.addWidget(_heading("Overhangs (need supports)"))
        row = QHBoxLayout()
        self.show_overhangs = QCheckBox("Show in red")
        self.show_overhangs.setChecked(True)
        self.angle = _spin(10, 89, 45, 5, "°")
        self.angle.setToolTip("Faces leaning further than this from upright need supports")
        row.addWidget(self.show_overhangs)
        row.addWidget(QLabel("steeper than"))
        row.addWidget(self.angle)
        lay.addLayout(row)
        self.overhang = QLabel("", objectName="Muted")
        self.overhang.setWordWrap(True)
        lay.addWidget(self.overhang)
        self.show_overhangs.toggled.connect(self._overhangs)
        self.angle.valueChanged.connect(self._overhangs)
        lay.addStretch(1)
        done = QPushButton("Done", objectName="Primary")
        done.clicked.connect(self.done.emit)
        r2 = QHBoxLayout()
        r2.addStretch(1)
        r2.addWidget(done)
        lay.addLayout(r2)

    def _overhangs(self, *_a) -> None:
        self.overhangs.emit(self.show_overhangs.isChecked(), self.angle.value())

    def show_report(self, lines: list[str], ok: bool, overhang_area: float) -> None:
        self.result.setText("\n".join(f"• {x}" for x in lines))
        self.repair_button.setEnabled(not ok)
        self.overhang.setText(
            f"{overhang_area / 100:.1f} cm² of overhang — mostly underneath: turn the view to "
            "see it in red."
            if overhang_area
            else "No overhangs."
        )
