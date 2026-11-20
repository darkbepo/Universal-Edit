"""The Tracks strip above the waveform: one row per track and "+ Track". Every row has the same
fixed columns, so they line up: name (click = edit this one, double-click = rename), M / S,
volume, left / right, a ⋯ menu (also on right-click: Rename, Mute, Solo, Start time…, Duplicate,
Remove) and a lane: the track's sound as a block on a shared timeline. Dragging any block moves
that track in time without switching the track being edited; a click on it edits it. The track
being edited is highlighted. It only shows and reports; the page owns the tracks."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QLineF, QObject, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from umc.audioedit import peaks
from umc.audioedit import tracks as tr
from umc.audioedit.ops import format_time
from umc.gui import theme

PAN_STEPS = 100  # the slider runs −100 … 100
NAME_W = 180  # the name column (long names end in "…")
MAX_NAME = 60
LANE_ROOM = 1.25  # the lanes span the mix plus a quarter, so a track can be dragged later
LANE_EMPTY_SECONDS = 10  # what the lanes span while every track is empty
DRAG_PX = 3  # moving less than this is a click
SNAP_PX = 6  # a start this close to 0:00 snaps to it


def start_text(frames: int, rate: int) -> str:
    """Like 0:13.2 (tenths are enough for a start)."""
    return format_time(frames / rate)[:-2]


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


class _Lane(QWidget):
    """One track's block on the shared timeline: drag it to move the track in time."""

    def __init__(self, panel: TracksPanel, i: int) -> None:
        super().__init__(objectName="TrackLane")
        self.panel, self.i = panel, i
        self.setMinimumWidth(120)
        self.setFixedHeight(28)
        self.setMouseTracking(True)
        self._drag: tuple[float, int] | None = None  # pointer x, offset when pressed
        self._moved = False
        t = panel.tracks[i]
        self.setAccessibleName(f"{t.name} in time")
        self.setToolTip("Drag to move this track in time · click: edit it · ← / → move it")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def track(self) -> tr.Track:
        return self.panel.tracks[self.i]

    def frames_per_px(self) -> float:
        return self.panel.span() / max(1, self.width() - 2)

    def block(self) -> QRectF:
        t, fpp = self.track, self.frames_per_px()
        w = max(3.0, t.doc.frames / fpp) if t.doc.frames else 3.0
        return QRectF(1 + t.offset / fpp, 3, w, self.height() - 6)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(p["border"]), 1))
        painter.setBrush(QColor(p["surface"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        t = self.track
        box = self.block()
        heard = any(x is t for x in tr.audible(self.panel.tracks))
        accent = QColor(p["accent"])
        fill = QColor(accent)
        fill.setAlpha(55 if heard else 25)
        path = QPainterPath()
        path.addRoundedRect(box, 3, 3)
        painter.fillPath(path, fill)
        if t.doc.frames:
            mins, maxs = self.panel.lane_peaks(t, self.frames_per_px(), int(box.width()) + 1)
            wave = QColor(accent)
            wave.setAlpha(230 if heard else 90)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(wave, 1))
            loudest = max(
                float(np.max(np.abs(mins), initial=0)), float(np.max(np.abs(maxs), initial=0))
            )
            mid = box.center().y()
            half = (box.height() / 2 - 1) / max(
                loudest, 0.05
            )  # quiet tracks still show their shape
            x0 = box.left()
            painter.drawLines([
                QLineF(x0 + k + 0.5, mid - hi * half, x0 + k + 0.5, max(mid - lo * half,
                                                                        mid - hi * half + 1))
                for k, (lo, hi) in enumerate(zip(mins, maxs, strict=True))
            ])  # fmt: skip
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.i == self.panel.active or self._drag is not None:
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 3, 3)

    def event(self, event) -> bool:  # noqa: A003
        # ← / → are the page's shortcuts too: claim them while the lane has the focus
        if event.type() == QEvent.Type.ShortcutOverride and self._arrow_step(event) is not None:
            event.accept()
            return True
        return super().event(event)

    def _arrow_step(self, event) -> int | None:
        key = event.key()
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return None
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if mods not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            return None
        shift = mods == Qt.KeyboardModifier.ShiftModifier
        step = round(self.track.doc.rate * (1 if shift else 0.1))
        return -step if key == Qt.Key.Key_Left else step

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = self._arrow_step(event)
        if step is None or not self.panel.lanes_enabled:
            super().keyPressEvent(event)
            return
        offset = max(0, self.track.offset + step)
        if offset != self.track.offset:
            self.panel.move_track(self.i, offset)
            self.panel.moved.emit(self.i)
            self.panel.update_lanes()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.panel.lanes_enabled:
            event.ignore()
            return
        if self.block().adjusted(-3, 0, 3, 0).contains(event.position()):
            self._drag, self._moved = (event.position().x(), self.track.offset), False
            self.panel.freeze_span(True)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self.panel._select(self.i)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            inside = self.panel.lanes_enabled and self.block().contains(event.position())
            self.setCursor(Qt.CursorShape.OpenHandCursor if inside else Qt.CursorShape.ArrowCursor)
            return
        x0, start = self._drag
        dx = event.position().x() - x0
        if not self._moved and abs(dx) < DRAG_PX:
            return
        self._moved = True
        fpp = self.frames_per_px()
        offset = max(0, round(start + dx * fpp))
        if offset / fpp < SNAP_PX:
            offset = 0
        self.panel.move_track(self.i, offset)
        rate = self.track.doc.rate
        QToolTip.showText(
            event.globalPosition().toPoint() + QPoint(0, -36),
            f"Starts {start_text(offset, rate)}",
            self,
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            return
        _, start = self._drag
        self._drag = None
        self.panel.freeze_span(False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        QToolTip.hideText()
        if not self._moved:
            self.panel._select(self.i)
        elif self.track.offset != start:
            self.panel.moved.emit(self.i)
        self.panel.update_lanes()


class TracksPanel(QFrame):
    selected = Signal(int)
    changed = Signal()  # a volume / pan / mute / solo was changed (already written to the track)
    remove_requested = Signal(int)
    renamed = Signal(int, str)
    add_empty = Signal()
    add_file = Signal()
    moving = Signal(int)  # a lane is being dragged: the track's offset changed (live)
    moved = Signal(int)  # … and the drag ended somewhere new
    start_time_requested = Signal(int)  # "Start time…": the page asks for it
    duplicate_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent, objectName="AudioTracks")
        self.tracks: list[tr.Track] = []
        self.active = 0
        self._shown: list[tuple[tr.Track, str]] = []  # what the rows were built from
        self._renaming: int | None = None
        self._span = 0  # frames across a lane (kept while a lane is dragged)
        self._frozen = False
        # id(doc) → (doc, revision, summary); the doc is kept so its id can't be reused, and
        # set_tracks drops the docs no longer shown
        self._peaks: dict[int, tuple[object, int, peaks.Peaks]] = {}
        self.lanes_enabled = True
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
        self._peaks = {k: v for k, v in self._peaks.items() if any(v[0] is t.doc for t in tracks)}
        shown = [(t, t.name) for t in tracks]
        same = len(shown) == len(self._shown) and all(
            a is b and n == m for (a, n), (b, m) in zip(shown, self._shown, strict=True)
        )
        if not same:
            self._rebuild()
        self._shown = shown
        self.update_lanes()
        for i, row in enumerate(self.row_widgets):
            row["name"].setChecked(i == active)
            frame = row["row"]
            frame.setProperty("active", i == active)
            frame.style().unpolish(frame)
            frame.style().polish(frame)

    def set_enabled(self, enabled: bool) -> None:
        """Switching / adding / removing waits while the page is busy; mixing settings don't."""
        self.add_button.setEnabled(enabled)
        self.lanes_enabled = enabled
        for row in self.row_widgets:
            row["name"].setEnabled(enabled)
            row["more"].setEnabled(enabled)

    # ------------------------------------------------------------ lanes
    def span(self) -> int:
        return max(1, self._span)

    def update_lanes(self) -> None:
        """Repaint the lanes (after an edit changed a length, or a mute / solo)."""
        if not self._frozen and self.tracks:
            rate = self.tracks[0].doc.rate
            self._span = round(tr.extent(self.tracks) * LANE_ROOM) or rate * LANE_EMPTY_SECONDS
        for row in self.row_widgets:
            row["lane"].update()

    def freeze_span(self, on: bool) -> None:
        """Keep the scale while a lane is dragged (else the block would run from the pointer)."""
        self._frozen = on

    def move_track(self, i: int, offset: int) -> None:
        t = self.tracks[i]
        if t.offset != offset:
            t.offset = offset
            self.update_lanes()
            self.moving.emit(i)

    def lane_peaks(self, t: tr.Track, fpp: float, width: int) -> tuple[np.ndarray, np.ndarray]:
        """The track's loudest / quietest per lane pixel (all channels together), −1 … 1."""
        key = id(t.doc)
        got = self._peaks.get(key)
        if got is None or got[0] is not t.doc or got[1] != t.doc.revision:
            got = self._peaks[key] = (t.doc, t.doc.revision, peaks.build(t.doc.samples))
        mins, maxs = peaks.columns(t.doc.samples, got[2], 0, fpp, width)
        if mins.shape[1] == 0:
            return np.zeros(0), np.zeros(0)
        return np.clip(mins.min(axis=0), -1, 1), np.clip(maxs.max(axis=0), -1, 1)

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
        for key, text in (("mute", "Mute"), ("solo", "Solo")):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(getattr(self.tracks[i], key) if i < len(self.tracks) else False)
            # the row's button does the work (and keeps its look); looked up when it runs
            act.triggered.connect(lambda on, k=key: self._toggle_row(i, k, on))
        menu.addAction("Start time…", lambda: self.start_time_requested.emit(i))
        menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(i))
        menu.addSeparator()
        menu.addAction("Remove track", lambda: self.remove_requested.emit(i))
        menu.aboutToShow.connect(lambda: self._refresh_menu(menu, i))
        return menu

    def _toggle_row(self, i: int, key: str, on: bool) -> None:
        if 0 <= i < len(self.row_widgets):
            self.row_widgets[i][key].setChecked(on)

    def _refresh_menu(self, menu: QMenu, i: int) -> None:
        """Ticks as they are now; Mute / Solo work while busy (like their buttons), the rest
        waits."""
        for act in menu.actions():
            if act.text() in ("Mute", "Solo"):
                if i < len(self.tracks):
                    act.setChecked(getattr(self.tracks[i], act.text().lower()))
            else:
                act.setEnabled(self.lanes_enabled)

    def _context_menu(self, i: int, frame: QFrame, pos: QPoint) -> None:
        menu = self.row_menu(i, frame)
        menu.exec(frame.mapToGlobal(pos))
        menu.deleteLater()

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
        more.setToolTip(f"More for {t.name}: rename, start time, duplicate, remove …")
        more.setAccessibleName(f"More for {t.name}")
        more.setFixedWidth(32)
        more.setProperty("compact", True)
        lane = _Lane(self, i)
        for w in (stack, mute, solo, gain, gain_label, pan, pan_label, more):
            lay.addWidget(w)
        lay.addWidget(lane, 1)
        frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        frame.customContextMenuRequested.connect(
            lambda pos, i=i, f=frame: self._context_menu(i, f, pos)
        )
        more.setMenu(self.row_menu(i, more))  # goes with its button on a rebuild
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
            "lane": lane,
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
