"""The Tracks strip above the waveform: one row per track and "+ Track". Every row has the same
fixed columns, so they line up: name (click = edit this one, double-click = rename), M / S,
volume, left / right and a ⋯ menu (Rename, Remove). The track being edited is highlighted.
It only shows and reports; the page owns the tracks."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.audioedit import tracks as tr

PAN_STEPS = 100  # the slider runs −100 … 100
NAME_W = 180  # the name column (long names end in "…")
MAX_NAME = 60


def pan_text(pan: float) -> str:
    if abs(pan) < 0.005:
        return "Centre"
    return f"{'L' if pan < 0 else 'R'} {round(abs(pan) * 100)}"


def gain_text(db: float) -> str:
    return "0 dB" if db == 0 else f"{db:+g} dB"


def clean_name(text: str) -> str:
    """What a typed name becomes ("" = keep the old one)."""
    return " ".join(text.split())[:MAX_NAME]


class _NameButton(QPushButton):
    """The track's name, cut to fit with "…"; a double-click asks to rename it."""

    rename = Signal()

    def __init__(self, name: str) -> None:
        super().__init__(objectName="TrackName")
        self.setCheckable(True)
        self.setFixedWidth(NAME_W)
        self.full = name
        self._elide()
        self.setToolTip(f"{name}\nClick: edit this track · double-click: rename")
        self.setAccessibleName(name)

    def _elide(self) -> None:
        # the stylesheet's font arrives after construction: cut again when it does
        room = self.width() - 26  # padding + border
        self.setText(self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, room))

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.rename.emit()
        else:
            super().mouseDoubleClickEvent(event)


class _EscCancels(QObject):
    """Esc in the rename box puts the old name back (Enter / leaving the box keeps the new one)."""

    def __init__(self, panel: TracksPanel, i: int) -> None:
        super().__init__(panel)
        self.panel, self.i = panel, i

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()  # the box gets Esc, not the page's "deselect" shortcut
            return True
        if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.panel.finish_rename(self.i, keep=False)
            return True
        return False


class TracksPanel(QFrame):
    selected = Signal(int)
    changed = Signal()  # a volume / pan / mute / solo was changed (already written to the track)
    remove_requested = Signal(int)
    renamed = Signal(int, str)
    add_empty = Signal()
    add_file = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="AudioTracks")
        self.tracks: list[tr.Track] = []
        self.active = 0
        self._shown: list[tuple[tr.Track, str]] = []  # what the rows were built from
        self._renaming: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 0)
        outer.setSpacing(3)
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
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        outer.addWidget(self.rows)
        self.row_widgets: list[dict[str, QWidget]] = []

    def set_tracks(self, tracks: list[tr.Track], active: int) -> None:
        """Show these tracks. Rows are rebuilt only when tracks or names changed; switching the
        track being edited just moves the highlight (so a double-click on a name still lands)."""
        self.tracks, self.active = tracks, active
        shown = [(t, t.name) for t in tracks]
        same = len(shown) == len(self._shown) and all(
            a is b and n == m for (a, n), (b, m) in zip(shown, self._shown, strict=True)
        )
        if not same:
            self._rebuild()
        self._shown = shown
        for i, row in enumerate(self.row_widgets):
            row["name"].setChecked(i == active)
            frame = row["row"]
            frame.setProperty("active", i == active)
            frame.style().unpolish(frame)
            frame.style().polish(frame)

    def set_enabled(self, enabled: bool) -> None:
        """Switching / adding / removing waits while the page is busy; mixing settings don't."""
        self.add_button.setEnabled(enabled)
        for row in self.row_widgets:
            row["name"].setEnabled(enabled)
            row["more"].setEnabled(enabled)

    # ------------------------------------------------------------ renaming
    def start_rename(self, i: int) -> None:
        if not 0 <= i < len(self.row_widgets):
            return
        if self._renaming is not None and self._renaming != i:
            self.finish_rename(self._renaming)
        row = self.row_widgets[i]
        self._renaming = i
        edit: QLineEdit = row["edit"]
        edit.setText(self.tracks[i].name)
        row["name_stack"].setCurrentWidget(edit)
        edit.setFocus()
        edit.selectAll()

    def finish_rename(self, i: int, keep: bool = True) -> None:
        if self._renaming != i or not 0 <= i < len(self.row_widgets):
            return
        self._renaming = None
        row = self.row_widgets[i]
        row["name_stack"].setCurrentWidget(row["name"])
        name = clean_name(row["edit"].text())
        if keep and name and name != self.tracks[i].name:
            self.renamed.emit(i, name)  # the page renames it; set_tracks rebuilds the rows

    def row_menu(self, i: int, parent: QWidget | None = None) -> QMenu:
        menu = QMenu(parent or self)
        menu.addAction("Rename…", lambda: self.start_rename(i))
        menu.addAction("Remove track", lambda: self.remove_requested.emit(i))
        return menu

    # ------------------------------------------------------------ rows
    def _rebuild(self) -> None:
        self._renaming = None
        while self.rows_layout.count():
            w = self.rows_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.row_widgets = [self._row(i, t) for i, t in enumerate(self.tracks)]
        for row in self.row_widgets:
            self.rows_layout.addWidget(row["row"])

    def _row(self, i: int, t: tr.Track) -> dict[str, QWidget]:
        frame = QFrame(objectName="TrackRow")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        name = _NameButton(t.name)
        name.clicked.connect(lambda _c=False, i=i: self._select(i))
        name.rename.connect(lambda i=i: self.start_rename(i))
        edit = QLineEdit(objectName="TrackRename")
        edit.setMaxLength(MAX_NAME)
        edit.setFixedWidth(NAME_W)
        edit.setAccessibleName("Track name")
        edit.editingFinished.connect(lambda i=i: self.finish_rename(i))
        edit.installEventFilter(_EscCancels(self, i))
        stack = QStackedWidget()
        stack.setFixedWidth(NAME_W)
        stack.addWidget(name)
        stack.addWidget(edit)
        stack.setCurrentWidget(name)
        mute = self._toggle("M", f"Mute {t.name}", t.mute)
        mute.toggled.connect(lambda on, t=t: self._set(t, "mute", on))
        solo = self._toggle("S", f"Solo {t.name} (hear only the soloed tracks)", t.solo)
        solo.toggled.connect(lambda on, t=t: self._set(t, "solo", on))
        gain = QSlider(Qt.Orientation.Horizontal, objectName="TrackGain")
        gain.setRange(int(tr.MIN_GAIN_DB), int(tr.MAX_GAIN_DB))
        gain.setValue(round(t.gain_db))
        gain.setFixedWidth(110)
        gain.setAccessibleName(f"{t.name} volume")
        gain.setToolTip("Volume of this track in the mix")
        gain_label = QLabel(gain_text(t.gain_db), objectName="Muted")
        gain_label.setFixedWidth(52)
        pan = QSlider(Qt.Orientation.Horizontal, objectName="TrackPan")
        pan.setRange(-PAN_STEPS, PAN_STEPS)
        pan.setValue(round(t.pan * PAN_STEPS))
        pan.setFixedWidth(90)
        pan.setAccessibleName(f"{t.name} left / right")
        pan.setToolTip("Left / right (double-click: centre)")
        pan_label = QLabel(pan_text(t.pan), objectName="Muted")
        pan_label.setFixedWidth(52)

        def on_gain(v: int, t=t, label=gain_label) -> None:
            label.setText(gain_text(v))
            self._set(t, "gain_db", float(v))

        def on_pan(v: int, t=t, label=pan_label) -> None:
            label.setText(pan_text(v / PAN_STEPS))
            self._set(t, "pan", v / PAN_STEPS)

        gain.valueChanged.connect(on_gain)
        pan.valueChanged.connect(on_pan)
        pan.mouseDoubleClickEvent = lambda _e, s=pan: s.setValue(0)
        more = QPushButton("⋯", objectName="Small")
        more.setToolTip(f"More for {t.name}: rename, remove")
        more.setAccessibleName(f"More for {t.name}")
        more.setFixedWidth(32)
        more.setProperty("compact", True)
        more.setMenu(self.row_menu(i, more))  # goes with its button on a rebuild
        for w in (stack, mute, solo, gain, gain_label, pan, pan_label, more):
            lay.addWidget(w)
        lay.addStretch(1)
        return {
            "row": frame,
            "name_stack": stack,
            "name": name,
            "edit": edit,
            "mute": mute,
            "solo": solo,
            "gain": gain,
            "gain_label": gain_label,
            "pan": pan,
            "pan_label": pan_label,
            "more": more,
        }

    @staticmethod
    def _toggle(text: str, tip: str, on: bool) -> QPushButton:
        b = QPushButton(text, objectName="Small")
        b.setCheckable(True)
        b.setChecked(on)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFixedWidth(32)
        b.setProperty("compact", True)  # no side padding: the letter must fit
        return b

    def _select(self, i: int) -> None:
        for j, row in enumerate(self.row_widgets):
            row["name"].setChecked(j == self.active)  # the page decides; set_tracks follows
        self.selected.emit(i)

    def _set(self, t: tr.Track, key: str, value) -> None:
        if getattr(t, key) != value:
            setattr(t, key, value)
            self.changed.emit()
