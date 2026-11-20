"""The Tracks strip above the waveform: one row per track (name = edit this one, M / S, volume,
left / right, ×) and "+ Track". It only shows and reports; the page owns the tracks."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from umc.audioedit import tracks as tr

PAN_STEPS = 100  # the slider runs −100 … 100


def pan_text(pan: float) -> str:
    if abs(pan) < 0.005:
        return "Centre"
    return f"{'L' if pan < 0 else 'R'} {round(abs(pan) * 100)}"


def gain_text(db: float) -> str:
    return "0 dB" if db == 0 else f"{db:+g} dB"


class TracksPanel(QFrame):
    selected = Signal(int)
    changed = Signal()  # a volume / pan / mute / solo was changed (already written to the track)
    remove_requested = Signal(int)
    add_empty = Signal()
    add_file = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="AudioTracks")
        self.tracks: list[tr.Track] = []
        self.active = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 0)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(QLabel("Tracks", objectName="Muted"))
        head.addStretch(1)
        self.add_button = QPushButton("+ Track", objectName="Small")
        self.add_button.setToolTip("Add a track to mix with the others")
        menu = QMenu(self.add_button)
        menu.addAction("Empty track (then Record or Paste)", self.add_empty.emit)
        menu.addAction("From a file…", self.add_file.emit)
        self.add_button.setMenu(menu)
        head.addWidget(self.add_button)
        outer.addLayout(head)
        self.rows = QWidget()
        self.grid = QGridLayout(self.rows)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(2)
        outer.addWidget(self.rows)
        self.row_widgets: list[dict[str, QWidget]] = []

    def set_tracks(self, tracks: list[tr.Track], active: int) -> None:
        """Rebuild the rows (adding / removing / switching tracks)."""
        self.tracks, self.active = tracks, active
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.row_widgets = []
        for i, t in enumerate(tracks):
            self.row_widgets.append(self._row(i, t))

    def set_enabled(self, enabled: bool) -> None:
        """Switching / adding / removing waits while the page is busy; mixing settings don't."""
        self.add_button.setEnabled(enabled)
        for row in self.row_widgets:
            row["name"].setEnabled(enabled)
            row["remove"].setEnabled(enabled)

    def _row(self, i: int, t: tr.Track) -> dict[str, QWidget]:
        name = QPushButton(t.name, objectName="Small")
        name.setCheckable(True)
        name.setChecked(i == self.active)
        name.setToolTip("Edit this track")
        name.setMinimumWidth(90)
        name.clicked.connect(lambda _c=False, i=i: self._select(i))
        mute = self._toggle("M", f"Mute {t.name}", t.mute)
        mute.toggled.connect(lambda on, t=t: self._set(t, "mute", on))
        solo = self._toggle("S", f"Solo {t.name} (hear only the soloed tracks)", t.solo)
        solo.toggled.connect(lambda on, t=t: self._set(t, "solo", on))
        gain = QSlider(Qt.Orientation.Horizontal, objectName="TrackGain")
        gain.setRange(int(tr.MIN_GAIN_DB), int(tr.MAX_GAIN_DB))
        gain.setValue(round(t.gain_db))
        gain.setFixedWidth(110)
        gain.setAccessibleName(f"{t.name} volume")
        gain_label = QLabel(gain_text(t.gain_db), objectName="Muted")
        gain_label.setMinimumWidth(48)
        pan = QSlider(Qt.Orientation.Horizontal, objectName="TrackPan")
        pan.setRange(-PAN_STEPS, PAN_STEPS)
        pan.setValue(round(t.pan * PAN_STEPS))
        pan.setFixedWidth(90)
        pan.setAccessibleName(f"{t.name} left / right")
        pan.setToolTip("Left / right (double-click: centre)")
        pan_label = QLabel(pan_text(t.pan), objectName="Muted")
        pan_label.setMinimumWidth(48)

        def on_gain(v: int, t=t, label=gain_label) -> None:
            label.setText(gain_text(v))
            self._set(t, "gain_db", float(v))

        def on_pan(v: int, t=t, label=pan_label) -> None:
            label.setText(pan_text(v / PAN_STEPS))
            self._set(t, "pan", v / PAN_STEPS)

        gain.valueChanged.connect(on_gain)
        pan.valueChanged.connect(on_pan)
        pan.mouseDoubleClickEvent = lambda _e, s=pan: s.setValue(0)
        remove = QPushButton("×", objectName="Small")
        remove.setToolTip(f"Remove {t.name}")
        remove.setAccessibleName(f"Remove {t.name}")
        remove.setFixedWidth(30)
        remove.clicked.connect(lambda _c=False, i=i: self.remove_requested.emit(i))
        widgets = (name, mute, solo, gain, gain_label, pan, pan_label, remove)
        for col, w in enumerate(widgets):
            self.grid.addWidget(w, i, col)
        self.grid.setColumnStretch(len(widgets), 1)
        keys = ("name", "mute", "solo", "gain", "gain_label", "pan", "pan_label", "remove")
        return dict(zip(keys, widgets, strict=True))

    @staticmethod
    def _toggle(text: str, tip: str, on: bool) -> QPushButton:
        b = QPushButton(text, objectName="Small")
        b.setCheckable(True)
        b.setChecked(on)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFixedWidth(30)
        return b

    def _select(self, i: int) -> None:
        for j, row in enumerate(self.row_widgets):
            row["name"].setChecked(j == self.active)  # the page decides; set_tracks follows
        self.selected.emit(i)

    def _set(self, t: tr.Track, key: str, value) -> None:
        if getattr(t, key) != value:
            setattr(t, key, value)
            self.changed.emit()
