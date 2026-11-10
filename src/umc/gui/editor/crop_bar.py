"""Floating bar next to a crop rectangle or a selection: size, Apply / Cancel.

The canvas shows it only once a rectangle exists and the mouse button is up, next to the corner
where the mouse was released (``place_bar``), and moves it along when zooming or panning.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

GAP = 8  # between the rectangle and the bar
MARGIN = 4  # keep this far from the canvas edges


def nearest_corner(
    rect: tuple[float, float, float, float], x: float, y: float
) -> tuple[bool, bool]:
    """(right, bottom): the corner of ``rect`` (x0, y0, x1, y1) nearest to ``(x, y)``."""
    x0, y0, x1, y1 = rect
    return abs(x - x1) < abs(x - x0), abs(y - y1) < abs(y - y0)


def place_bar(
    rect: tuple[float, float, float, float],
    corner: tuple[bool, bool],
    size: tuple[float, float],
    area: tuple[float, float],
) -> tuple[int, int]:
    """Top-left position for a bar of ``size`` next to ``corner`` of ``rect``, inside ``area``.

    The bar sits outside the rectangle (below a bottom corner, above a top one); if there is no
    room there it goes to the other side, and as a last resort inside the rectangle. It lines up
    with the corner's side horizontally. Everything is clamped to ``area``.
    """
    x0, y0, x1, y1 = rect
    right, bottom = corner
    w, h = size
    aw, ah = area
    x = x1 - w if right else x0
    below, above = y1 + GAP, y0 - GAP - h
    fits_below = below + h <= ah - MARGIN
    fits_above = above >= MARGIN
    if bottom:
        y = below if fits_below else above if fits_above else y1 - GAP - h
    else:
        y = above if fits_above else below if fits_below else y0 + GAP
    x = max(MARGIN, min(x, aw - w - MARGIN))
    y = max(MARGIN, min(y, ah - h - MARGIN))
    return round(x), round(y)


class CropBar(QFrame):
    """``mode`` "crop": Apply crop / Cancel. ``mode`` "select": Crop to selection / Deselect."""

    applied = Signal(str)  # mode
    cancelled = Signal(str)

    LABELS = {"crop": ("Apply crop", "Cancel"), "select": ("Crop to selection", "Deselect")}
    TIPS = {
        "crop": ("Crop to the rectangle (Enter)", "Discard the rectangle (Esc)"),
        "select": ("Crop the image to the selection", "Remove the selection (Ctrl+D)"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CropBar")
        self.mode = "crop"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(6)
        self.size_label = QLabel(objectName="Muted")
        self.apply_button = QPushButton(objectName="CropApply")
        self.cancel_button = QPushButton(objectName="Small")
        for b in (self.apply_button, self.cancel_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Enter / Esc keep going to the canvas
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.clicked.connect(lambda: self.applied.emit(self.mode))
        self.cancel_button.clicked.connect(lambda: self.cancelled.emit(self.mode))
        lay.addWidget(self.size_label)
        lay.addWidget(self.apply_button)
        lay.addWidget(self.cancel_button)
        self.hide()

    def show_for(self, mode: str, width: int, height: int) -> None:
        if mode != self.mode or not self.apply_button.text():
            self.mode = mode
            apply_text, cancel_text = self.LABELS[mode]
            self.apply_button.setText(apply_text)
            self.cancel_button.setText(cancel_text)
            self.apply_button.setToolTip(self.TIPS[mode][0])
            self.cancel_button.setToolTip(self.TIPS[mode][1])
        text = f"{width} × {height} px"
        if self.size_label.text() != text:
            self.size_label.setText(text)
        self.adjustSize()
        if not self.isVisible():
            self.show()
            self.raise_()
