"""The "Add logo" side panel of the 3D viewer: picture, how its shape is found, size, where it
goes. It only shows and collects settings; the page builds and places the logo."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from umc.editor3d.scene import LogoSettings

MODES = (("alpha", "Transparent background"), ("dark", "Dark parts"), ("light", "Light parts"))
DETAILS = (("smooth", "Smooth"), ("normal", "Normal"), ("fine", "Fine"))
WHERE = (("face", "On a face of the model"), ("badge", "As a separate badge"))
PLATES = (("none", "No plate"), ("rectangle", "Rectangle"), ("rounded", "Rounded rectangle"))
PREVIEW_PX = 200
FACE_HINT = "Click a flat face of the model to place the logo, then drag the logo to move it."


def _combo(items) -> QComboBox:
    box = QComboBox()
    for key, label in items:
        box.addItem(label, key)
    return box


def _spin(lo: float, hi: float, value: float, step: float, suffix: str = " mm") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)  # changes when Enter / leaving, not on every key
    return s


class LogoPanel(QFrame):
    changed = Signal()  # a setting changed
    picture_requested = Signal()
    apply_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(320)
        self._quiet = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title = QLabel("Add a logo", objectName="Title")
        lay.addWidget(self.title)

        row = QHBoxLayout()
        self.picture_button = QPushButton("Choose picture…", objectName="Small")
        self.picture_button.setToolTip("PNG with a transparent background works best")
        self.picture_button.clicked.connect(self.picture_requested.emit)
        row.addWidget(self.picture_button)
        self.picture_name = QLabel("", objectName="Muted")
        self.picture_name.setWordWrap(True)
        row.addWidget(self.picture_name, 1)
        lay.addLayout(row)
        self.preview = QLabel(objectName="LogoPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(PREVIEW_PX // 2)
        self.preview.setToolTip("The shape that will be raised")
        lay.addWidget(self.preview)

        form = QFormLayout()
        form.setVerticalSpacing(6)
        self.mode = _combo(MODES)
        form.addRow("Shape from", self.mode)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(1, 254)
        self.threshold.setValue(128)
        self.threshold.setToolTip("Which pixels count as dark / light")
        form.addRow("Threshold", self.threshold)
        self.invert = QCheckBox("Invert")
        form.addRow("", self.invert)
        self.detail = _combo(DETAILS)
        self.detail.setCurrentIndex(1)
        form.addRow("Detail", self.detail)
        self.width_box = _spin(2, 500, 40, 1)
        form.addRow("Width", self.width_box)
        self.height_label = QLabel("", objectName="Muted")
        form.addRow("Height", self.height_label)
        self.thickness = _spin(0.2, 50, 2, 0.2)
        self.thickness.setToolTip("How far the logo stands out")
        form.addRow("Thickness", self.thickness)
        self.where = _combo(WHERE)
        form.addRow("Place", self.where)
        self.rotation = _spin(-180, 180, 0, 15, "°")
        form.addRow("Rotation", self.rotation)
        self.sink = _spin(0, 2, 0.2, 0.1)
        self.sink.setToolTip("How far the logo goes into the surface, so the slicer joins them")
        form.addRow("Sink into surface", self.sink)
        self.plate = _combo(PLATES)
        form.addRow("Base plate", self.plate)
        self.margin = _spin(0, 50, 3, 0.5)
        form.addRow("Margin", self.margin)
        self.plate_thickness = _spin(0.4, 20, 2, 0.2)
        form.addRow("Plate thickness", self.plate_thickness)
        self.hole = QCheckBox("Keychain hole")
        form.addRow("", self.hole)
        self.form = form
        lay.addLayout(form)

        self.note = QLabel("", objectName="Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        lay.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", objectName="Small")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        buttons.addWidget(self.cancel_button)
        self.apply_button = QPushButton("Apply", objectName="Primary")
        self.apply_button.clicked.connect(self.apply_requested.emit)
        buttons.addWidget(self.apply_button)
        lay.addLayout(buttons)

        for box in (self.mode, self.detail, self.where, self.plate):
            box.currentIndexChanged.connect(self._changed)
        for spin in (self.width_box, self.thickness, self.rotation, self.sink, self.margin,
                     self.plate_thickness):  # fmt: skip
            spin.valueChanged.connect(self._changed)
        self.threshold.valueChanged.connect(self._changed)
        self.invert.toggled.connect(self._changed)
        self.hole.toggled.connect(self._changed)
        self._has_model = True
        self._sync()

    # ------------------------------------------------------------ settings
    def settings(self) -> LogoSettings:
        where = self.where.currentData() if self._has_model else "badge"
        return LogoSettings(
            mode=self.mode.currentData(),
            threshold=self.threshold.value(),
            invert=self.invert.isChecked(),
            detail=self.detail.currentData(),
            width=self.width_box.value(),
            thickness=self.thickness.value(),
            where=where,
            rotation=self.rotation.value(),
            sink=self.sink.value(),
            plate=self.plate.currentData(),
            margin=self.margin.value(),
            plate_thickness=self.plate_thickness.value(),
            hole=self.hole.isChecked(),
        )

    def set_settings(self, s: LogoSettings) -> None:
        self._quiet = True
        try:
            for box, key in ((self.mode, s.mode), (self.detail, s.detail),
                             (self.where, s.where), (self.plate, s.plate)):  # fmt: skip
                box.setCurrentIndex(max(0, box.findData(key)))
            self.threshold.setValue(s.threshold)
            self.invert.setChecked(s.invert)
            self.width_box.setValue(s.width)
            self.thickness.setValue(s.thickness)
            self.rotation.setValue(s.rotation)
            self.sink.setValue(s.sink)
            self.margin.setValue(s.margin)
            self.plate_thickness.setValue(s.plate_thickness)
            self.hole.setChecked(s.hole)
        finally:
            self._quiet = False
        self._sync()

    def set_rotation(self, degrees: float) -> None:
        s = self.settings()
        self.set_settings(replace(s, rotation=degrees))
        self.changed.emit()

    def set_has_model(self, has: bool) -> None:
        """Without a model there's no face to put a logo on: a badge only."""
        self._has_model = has
        self._sync()

    def _changed(self, *_a) -> None:
        self._sync()
        if not self._quiet:
            self.changed.emit()

    def _sync(self) -> None:
        s = self.settings()
        rows = self.form.setRowVisible
        rows(self.where, self._has_model)
        rows(self.threshold, s.mode != "alpha")
        rows(self.sink, s.where == "face")
        badge, plate = s.where == "badge", s.plate != "none"
        rows(self.plate, badge)
        for w in (self.margin, self.plate_thickness, self.hole):
            rows(w, badge and plate)

    # ------------------------------------------------------------ feedback
    def set_picture(self, name: str) -> None:
        self.picture_name.setText(name)

    def set_preview(self, mask_rgba: bytes | None, w: int = 0, h: int = 0) -> None:
        """The silhouette (text colour on transparent), scaled into the preview box."""
        if not mask_rgba:
            self.preview.clear()
            return
        img = QImage(mask_rgba, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        pix = QPixmap.fromImage(img).scaled(
            PREVIEW_PX, self.preview.height(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )  # fmt: skip
        self.preview.setPixmap(pix)

    def set_size(self, width: float, height: float) -> None:
        self.height_label.setText(f"{height:.1f} mm" if width else "")

    def set_note(self, text: str) -> None:
        self.note.setText(text)
