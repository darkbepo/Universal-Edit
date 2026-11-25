"""The audio editor page, laid out like a simple Audacity: top bar (open, undo, save), a tool
row (play / stop, cut / copy / paste, effects, zoom), the waveform with a time ruler, and a
status line with the selection's start, end and length."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QEventLoop, QObject, QSettings, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.audioedit import noise, ops, record, speed
from umc.audioedit import project as aproj
from umc.audioedit import tracks as tr
from umc.audioedit.document import AudioDoc
from umc.audioedit.io import (
    BITRATES,
    DEFAULT_BITRATE,
    MAX_SAMPLES,
    SAVE_FORMATS,
    change_speed,
    decode,
    open_audio,
    resample,
    save_audio,
)
from umc.converters.audio import LOSSY_EXTS
from umc.core.detect import AUDIO_EXTS, MediaFamily, detect
from umc.core.errors import ConversionCancelled, ConversionError
from umc.core.filemode import match_mode
from umc.gui.audioeditor.player import DEFAULT_VOLUME, Player
from umc.gui.audioeditor.recorder import Recorder
from umc.gui.audioeditor.time_box import TimeBox
from umc.gui.audioeditor.tracks_panel import TracksPanel, clean_name
from umc.gui.audioeditor.wave_view import WaveView
from umc.gui.widgets.flow_layout import FlowLayout

log = logging.getLogger(__name__)

EMPTY_TEXT = "Open a sound with “Open…”, or drop audio files here (MP3, WAV, FLAC, M4A, OGG, …)."
HINT = (
    "Click to place the cursor · drag to select · middle-click to play from there · "
    "right-click for more · Ctrl+wheel to zoom · Space plays"
)
# the waveform's right-click menu on a selection: (key for run(), label); None = a line
SELECTION_MENU = (
    ("play_sel", "Play selection"),
    None,
    ("cut", "Cut"),
    ("copy", "Copy"),
    ("paste", "Paste"),
    ("delete", "Delete"),
    ("trim", "Trim"),
    ("silence", "Silence"),
    None,
    ("fade_in", "Fade in"),
    ("fade_out", "Fade out"),
    ("amplify", "Amplify…"),
    ("normalize", "Normalize"),
    ("reverse", "Reverse"),
    ("speed", "Speed / pitch…"),
    ("reduce_noise", "Reduce noise…"),
    None,
    ("zoom_sel", "Zoom to selection"),
)
START_HINT = "Type when the track starts in the mix, like 1:05.5 or 12.3 (seconds)."
SCROLL_STEPS = 1000  # the scroll bar works in 1/1000 of a screen
VOLUME_KEY = "audioeditor/volume"  # QSettings: the volume slider, 0…100
SPECTROGRAM_KEY = "audioeditor/spectrogram"  # QSettings: show the spectrogram (bool)
TOOL_GROUPS = (
    (
        ("cut", "Cut", "Cut the selection (Ctrl+X)"),
        ("copy", "Copy", "Copy the selection (Ctrl+C)"),
        ("paste", "Paste", "Paste at the cursor, or over the selection (Ctrl+V)"),
        ("delete", "Delete", "Delete the selection (Delete)"),
        ("trim", "Trim", "Keep only the selection (Ctrl+T)"),
        ("silence", "Silence", "Make the selection silent (Ctrl+L)"),
    ),
    (
        ("fade_in", "Fade in", "Fade the selection in"),
        ("fade_out", "Fade out", "Fade the selection out"),
        ("amplify", "Amplify…", "Make the selection (or everything) louder or quieter"),
        ("normalize", "Normalize", "Make the loudest peak −1 dB (selection or everything)"),
        ("reverse", "Reverse", "Play the selection (or everything) backwards"),
        (
            "speed",
            "Speed / pitch…",
            "Change the speed, tempo or pitch of the selection (or everything)",
        ),
    ),
    (
        ("learn_noise", "Learn noise", "Select a part with only background noise, then press this"),
        (
            "reduce_noise",
            "Reduce noise…",
            "Turn down the learned background noise in the selection (or everything)",
        ),
    ),
    (
        ("zoom_in", "Zoom in", "Zoom in (+ or Ctrl+1, or Ctrl+wheel)"),
        ("zoom_out", "Zoom out", "Zoom out (− or Ctrl+3)"),
        ("zoom_sel", "Selection", "Zoom to the selection (Ctrl+E)"),
        ("fit", "Fit", "Show the whole sound (Ctrl+F)"),
        ("spectrogram", "Spectrogram", "Show how loud each pitch is, instead of the waveform"),
    ),
)


TRACK_NO_ROOM = (
    "There isn't room for another track: the tracks together would be longer than the audio "
    "editor allows."
)


def _write_file(path: Path, data: bytes) -> None:
    """Temp file next to ``path`` + os.replace: a failed save never leaves half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".umc-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        match_mode(tmp, path)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise ConversionError(f"“{path.name}” couldn't be saved: {exc.strerror or exc}") from exc


class _Worker(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)

    def __init__(self, fn: Callable, fallback: str) -> None:
        super().__init__()
        self.fn, self.fallback = fn, fallback

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(lambda f, m: self.progress.emit(f, m))
        except ConversionCancelled:
            self.failed.emit("")
        except ConversionError as exc:
            log.error("audio editor: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("audio editor job failed")
            self.failed.emit(self.fallback)
        else:
            self.done.emit(result)


class AmplifyDialog(QDialog):
    """Amplify by a number of dB; shows the most it can go up without clipping."""

    def __init__(self, peak_db: float | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Amplify")
        self.db = QDoubleSpinBox()
        self.db.setRange(ops.GAIN_MIN_DB, ops.GAIN_MAX_DB)
        self.db.setDecimals(1)
        self.db.setSingleStep(1.0)
        self.db.setSuffix(" dB")
        headroom = -peak_db if peak_db is not None else 0.0
        self.db.setValue(round(max(ops.GAIN_MIN_DB, min(ops.GAIN_MAX_DB, headroom)), 1))
        form = QFormLayout(self)
        form.addRow("Change the volume by", self.db)
        note = "silent" if peak_db is None else f"{headroom:.1f} dB louder at most without clipping"
        form.addRow(QLabel(note, objectName="Muted"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


SPEED_LABELS = {"speed": "Change speed", "tempo": "Change tempo", "pitch": "Change pitch"}
SPEED_MODES = (
    ("speed", "Speed", "Faster and higher, or slower and lower (like a record player)"),
    ("tempo", "Tempo", "Faster or slower, the pitch stays"),
    ("pitch", "Pitch", "Higher or lower, the length stays"),
)
TOO_LONG_RESULT = (
    "The result would be too long for the audio editor (about 45 minutes of stereo sound). "
    "Change a shorter part."
)


class SpeedDialog(QDialog):
    """Speed (%), tempo (%) or pitch (semitones) of ``seconds`` of sound."""

    def __init__(self, seconds: float, rate: int, mode: str, value: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Speed / pitch")
        self.seconds, self.rate = seconds, rate
        self.values = {"speed": 100.0, "tempo": 100.0, "pitch": 0.0}
        self.values[mode] = value
        self.mode = QComboBox()
        for key, label, _about in SPEED_MODES:
            self.mode.addItem(label, key)
        self.about = QLabel(objectName="Muted")
        self.amount = QDoubleSpinBox()
        self.amount.setAccessibleName("Amount")
        self.result = QLabel(objectName="Muted")
        form = QFormLayout(self)
        form.addRow("Change", self.mode)
        form.addRow(self.about)
        form.addRow("By", self.amount)
        form.addRow(self.result)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._shown = mode
        self.mode.setCurrentIndex([k for k, _l, _a in SPEED_MODES].index(mode))
        self._show()
        self.mode.currentIndexChanged.connect(self._show)
        self.amount.valueChanged.connect(self._sync)

    def choice(self) -> tuple[str, float]:
        mode = self.mode.currentData()
        return mode, speed.clamp(mode, self.amount.value())

    def _show(self) -> None:
        mode = self.mode.currentData()
        if mode != self._shown:
            self.values[self._shown] = self.amount.value()
            self._shown = mode
        pitch = mode == "pitch"
        self.amount.blockSignals(True)
        if pitch:
            self.amount.setRange(speed.SEMITONES_MIN, speed.SEMITONES_MAX)
            self.amount.setDecimals(1)
            self.amount.setSingleStep(0.5)
            self.amount.setSuffix(" semitones")
        else:
            self.amount.setRange(speed.PERCENT_MIN, speed.PERCENT_MAX)
            self.amount.setDecimals(0)
            self.amount.setSingleStep(5)
            self.amount.setSuffix(" %")
        self.amount.setValue(self.values[mode])
        self.amount.blockSignals(False)
        self.about.setText(next(a for k, _l, a in SPEED_MODES if k == mode))
        self._sync()

    def _sync(self) -> None:
        mode, value = self.choice()
        after = speed.new_seconds(mode, value, self.seconds, self.rate)
        self.result.setText(f"{ops.format_time(self.seconds)} → {ops.format_time(after)}")


class NoiseDialog(QDialog):
    """How much to reduce the learned noise: amount, sensitivity and smoothing."""

    def __init__(self, reduce_db: float, sensitivity_db: float, smoothing: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reduce noise")
        self.reduce_db = QDoubleSpinBox()
        self.reduce_db.setRange(noise.REDUCE_MIN, noise.REDUCE_MAX)
        self.reduce_db.setDecimals(0)
        self.reduce_db.setSuffix(" dB")
        self.reduce_db.setValue(reduce_db)
        self.sensitivity = QDoubleSpinBox()
        self.sensitivity.setRange(noise.SENSITIVITY_MIN, noise.SENSITIVITY_MAX)
        self.sensitivity.setDecimals(1)
        self.sensitivity.setSuffix(" dB")
        self.sensitivity.setValue(sensitivity_db)
        self.smoothing = QSpinBox()
        self.smoothing.setRange(noise.SMOOTHING_MIN, noise.SMOOTHING_MAX)
        self.smoothing.setSuffix(" bands")
        self.smoothing.setValue(smoothing)
        form = QFormLayout(self)
        form.addRow("Reduce by", self.reduce_db)
        form.addRow("Sensitivity", self.sensitivity)
        form.addRow("Smoothing", self.smoothing)
        form.addRow(
            QLabel(
                "More sensitivity removes more noise but can make music sound watery;\n"
                "smoothing softens that.",
                objectName="Muted",
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[float, float, int]:
        return self.reduce_db.value(), self.sensitivity.value(), self.smoothing.value()


class BitrateDialog(QDialog):
    def __init__(self, fmt: str, kbps: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{fmt.upper()} options")
        self.box = QComboBox()
        for b in BITRATES:
            self.box.addItem(f"{b} kbps", b)
        self.box.setCurrentIndex(BITRATES.index(kbps) if kbps in BITRATES else 4)
        form = QFormLayout(self)
        form.addRow("Quality (bitrate)", self.box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self) -> int:
        return int(self.box.currentData())


class AudioEditorPage(QWidget):
    back_requested = Signal()
    opened = Signal()  # an open finished (also when it failed): for tests
    save_done = Signal()
    change_done = Signal()  # a speed / pitch change finished (also cancelled / failed)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AudioEditorPage")
        self.doc: AudioDoc | None = None  # the track being edited (tracks[active_track].doc)
        self.tracks: list[tr.Track] = []
        self.active_track = 0
        self._tracks_rev = 0  # bumped by every track change (add, remove, volume, mute …)
        self._tracks_saved_rev = 0
        self._open_as_track = False  # the file being opened becomes a new track
        self._saving_state: list[tuple[AudioDoc, int]] = []
        self._save_peak = -float("inf")
        self.sources: list[Path] = []
        self.clipboard: np.ndarray | None = None
        self.opening = False
        self.saving = False
        self._cancel = threading.Event()
        self._job: tuple[QThread, _Worker] | None = None
        self.changing = False  # a speed / pitch change runs on the worker thread
        self._change_request: tuple[str, int, int] = ("", 0, 0)
        self._last_speed: tuple[str, float] = ("tempo", 100.0)
        self.noise: tuple[int, np.ndarray] | None = None  # (rate, profile) from Learn noise
        self._last_noise = (
            noise.REDUCE_DEFAULT,
            noise.SENSITIVITY_DEFAULT,
            noise.SMOOTHING_DEFAULT,
        )
        self._save_target: tuple[Path, str] | None = None
        self._bitrate = DEFAULT_BITRATE
        self._saving_rev = -1
        self._saving_tracks_rev = -1
        self._last_save_ok = False
        self._leaving = False
        self._play_end = 0
        self._open_request: list[Path] = []
        self._save_request: tuple[Path, str] | None = None

        self.player = Player(self)
        self.player.position_changed.connect(self._on_play_position)
        self.player.finished.connect(self._on_play_finished)
        self.take: record.Take | None = None  # set while recording
        self._record_at = (0, 0)  # the selection when recording started: the take goes there
        self._record_stopping = False
        self.recorder = Recorder(self)
        self.recorder.chunk.connect(self._on_record_chunk)
        self.recorder.failed.connect(self._on_record_failed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_tools())
        self.tracks_panel = TracksPanel()
        self.tracks_panel.selected.connect(self.select_track)
        self.tracks_panel.changed.connect(self._on_tracks_changed)
        self.tracks_panel.remove_requested.connect(self.remove_track)
        self.tracks_panel.renamed.connect(self.rename_track)
        self.tracks_panel.add_empty.connect(self.add_empty_track)
        self.tracks_panel.add_file.connect(self.choose_track_file)
        self.tracks_panel.moving.connect(self._on_track_moving)
        self.tracks_panel.moved.connect(self._on_track_moved)
        self.tracks_panel.start_time_requested.connect(self.ask_track_start)
        self.tracks_panel.duplicate_requested.connect(self.duplicate_track)
        self.tracks_panel.hide()
        root.addWidget(self.tracks_panel)

        self.view = WaveView()
        self.view.set_spectrogram(self.edit_buttons["spectrogram"].isChecked())
        self.view.selection_changed.connect(self._update_state)
        self.view.view_changed.connect(self._sync_scrollbar)
        self.view.seek_requested.connect(self.seek)
        self.view.menu_requested.connect(self.show_wave_menu)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll)
        wave = QWidget()
        wl = QVBoxLayout(wave)
        wl.setContentsMargins(12, 8, 12, 4)
        wl.setSpacing(2)
        wl.addWidget(self.view, 1)
        wl.addWidget(self.scroll)
        self.placeholder = QLabel(EMPTY_TEXT, objectName="Muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.center = QStackedWidget()
        self.center.addWidget(wave)
        self.center.addWidget(self.placeholder)
        root.addWidget(self.center, 1)

        status = QFrame(objectName="EditorStatus")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        self.times = QWidget()
        tl = QHBoxLayout(self.times)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        self.time_boxes: dict[str, TimeBox] = {}
        for key, name in (("start", "Start"), ("end", "End"), ("length", "Length")):
            tl.addWidget(QLabel(name, objectName="Muted"))
            box = TimeBox(name)
            box.committed.connect(lambda text, k=key: self.type_time(k, text))
            tl.addWidget(box)
            self.time_boxes[key] = box
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.info_label = QLabel(objectName="Muted")
        sl.addWidget(self.times)
        sl.addSpacing(16)
        sl.addWidget(self.message, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop changing the sound")
        self.cancel_button.clicked.connect(self.cancel_change)
        self.cancel_button.hide()
        sl.addWidget(self.cancel_button)
        sl.addWidget(self.info_label)
        root.addWidget(status)

        self._install_shortcuts()
        self.setAcceptDrops(True)
        self._show_placeholder(EMPTY_TEXT)
        self._update_state()

    # ------------------------------------------------------------ layout
    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorTopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        self.back_button = self._button(lay, "← Back to Convert", "Leave the audio editor", "Link")
        self.back_button.clicked.connect(self.back_requested.emit)
        lay.addSpacing(12)
        self.open_button = self._button(lay, "Open…", "Open audio files (several are joined)")
        self.open_button.clicked.connect(self.choose_files)
        self.undo_button = self._button(lay, "Undo", "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = self._button(lay, "Redo", "Redo (Ctrl+Y)")
        self.redo_button.clicked.connect(self.redo)
        self.title = QLabel("", objectName="Muted")
        lay.addSpacing(12)
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.project_button = self._button(
            lay,
            "Save project…",
            "Keep every track separate in a project file (.umcaudio) to go on working later",
        )
        self.project_button.clicked.connect(self.save_project)
        self.save_as_button = self._button(lay, "Save as…", "Save as (Ctrl+Shift+S)")
        self.save_as_button.clicked.connect(self.save_as)
        self.save_button = self._button(lay, "Save", "Save (Ctrl+S)", "Primary")
        self.save_button.clicked.connect(self.save)
        return bar

    def _build_tools(self) -> QWidget:
        """Groups of buttons that wrap onto a second row in a narrow window."""
        bar = QFrame(objectName="AudioTools")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(12, 8, 12, 0)
        inner = QWidget()
        outer.addWidget(inner)
        flow = FlowLayout(inner, spacing=14)

        def group() -> QHBoxLayout:
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            flow.addWidget(w)
            return lay

        b = self._button
        lay = group()
        self.home_button = b(lay, "|◀", "Go to the start (Home)")
        self.home_button.clicked.connect(lambda: self.go_to(0))
        self.play_button = b(lay, "▶ Play", "Play the selection, or from the cursor (Space)")
        self.play_button.clicked.connect(self.toggle_play)
        self.stop_button = b(lay, "■ Stop", "Stop playing")
        self.stop_button.clicked.connect(self.stop)
        self.end_button = b(lay, "▶|", "Go to the end (End)")
        self.end_button.clicked.connect(lambda: self.go_to(self.doc.frames if self.doc else 0))
        self.record_button = b(
            lay, "● Record", "Record from the microphone at the cursor, or over the selection (R)"
        )
        self.record_button.clicked.connect(self.toggle_record)
        self.record_level = QProgressBar(objectName="RecordLevel")
        self.record_level.setRange(0, 100)
        self.record_level.setTextVisible(False)
        self.record_level.setFixedSize(70, 8)
        self.record_level.setAccessibleName("Recording level")
        self.record_level.hide()
        lay.addWidget(self.record_level)
        lay.addSpacing(8)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, objectName="AudioVolume")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setAccessibleName("Volume")
        saved = QSettings().value(VOLUME_KEY, round(DEFAULT_VOLUME * 100), type=int)
        self.volume_slider.setValue(saved)
        self.volume_slider.valueChanged.connect(self._on_volume)
        self.volume_label = QLabel(objectName="Muted")
        self.volume_label.setMinimumWidth(36)
        lay.addWidget(QLabel("Volume", objectName="Muted"))
        lay.addWidget(self.volume_slider)
        lay.addWidget(self.volume_label)
        self._show_volume()
        self.edit_buttons: dict[str, QPushButton] = {}
        for items in TOOL_GROUPS:
            lay = group()
            for key, label, tip in items:
                btn = b(lay, label, tip)
                btn.clicked.connect(lambda _c=False, k=key: self.run(k))
                self.edit_buttons[key] = btn
        spectro = self.edit_buttons["spectrogram"]
        spectro.setCheckable(True)
        spectro.setChecked(QSettings().value(SPECTROGRAM_KEY, False, type=bool))
        return bar

    @staticmethod
    def _button(lay, text: str, tip: str, name: str = "Small") -> QPushButton:
        btn = QPushButton(text, objectName=name)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(btn)
        return btn

    def _install_shortcuts(self) -> None:
        def sc(keys, fn) -> None:
            for k in keys if isinstance(keys, tuple) else (keys,):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                s.activated.connect(lambda f=fn: f())

        sc("Space", self.toggle_play)
        sc("Home", lambda: self.go_to(0))
        sc("End", lambda: self.go_to(self.doc.frames if self.doc else 0))
        sc("Ctrl+X", lambda: self.run("cut"))
        sc("Ctrl+C", lambda: self.run("copy"))
        sc("Ctrl+V", lambda: self.run("paste"))
        sc(("Delete", "Backspace"), lambda: self.run("delete"))
        sc("Ctrl+T", lambda: self.run("trim"))
        sc("Ctrl+L", lambda: self.run("silence"))
        sc("Ctrl+A", lambda: self.run("select_all"))
        sc("Escape", lambda: self.run("deselect"))
        sc(("Ctrl+1", "+", "="), lambda: self.run("zoom_in"))  # Ctrl+1 / 3 as in Audacity,
        sc(("Ctrl+3", "-"), lambda: self.run("zoom_out"))  # + / − as on the website
        sc("Ctrl+E", lambda: self.run("zoom_sel"))
        sc("Ctrl+F", lambda: self.run("fit"))
        sc("R", self.toggle_record)
        sc("Ctrl+Z", self.undo)
        sc(("Ctrl+Y", "Ctrl+Shift+Z"), self.redo)
        sc("Ctrl+S", self.save)
        sc("Ctrl+Shift+S", self.save_as)

    # ------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return (
            self.opening or self.saving or self.changing or self.recording or self._job is not None
        )

    @property
    def recording(self) -> bool:
        return self.take is not None

    @property
    def dirty(self) -> bool:
        """Unsaved changes in any track, or in the tracks themselves."""
        if any(t.doc.dirty for t in self.tracks):
            return True
        return self._tracks_rev != self._tracks_saved_rev or (
            self.doc is not None and self.doc.dirty
        )

    @property
    def mixing(self) -> bool:
        """Play and save the mix: two or more tracks, or one with a volume / pan / mute / solo."""
        return len(self.tracks) > 1 or any(tr.changed(t) for t in self.tracks)

    @property
    def offset(self) -> int:
        """Where the track being edited starts in the mix (playback runs in mix time)."""
        if self.doc is None or not self.tracks:
            return 0
        return self.tracks[self.active_track].offset

    def _sync_timing(self) -> None:
        self.view.set_timing(self.offset, tr.extent(self.tracks) if self.tracks else 0)

    def _length(self) -> int:
        """Frames that play / save: the mix's, or the sound's."""
        if self.mixing:
            return tr.length(self.tracks)
        return self.doc.frames if self.doc is not None else 0

    def _used_samples(self) -> int:
        return tr.total_samples(self.tracks) if self.tracks else (
            self.doc.samples.size if self.doc is not None else 0)  # fmt: skip

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _show_placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.center.setCurrentWidget(self.placeholder)

    def _update_state(self) -> None:
        self._sync_timing()
        self.tracks_panel.update_lanes()
        doc = self.doc
        ready = doc is not None and not self.busy
        sel = ready and doc.has_selection
        enabled = {
            "cut": sel,
            "copy": sel,
            "paste": ready and self.clipboard is not None,
            "delete": sel,
            "trim": sel,
            "silence": sel,
            "fade_in": sel,
            "fade_out": sel,
            "amplify": ready and doc.frames > 0,
            "normalize": ready and doc.frames > 0,
            "reverse": ready and doc.frames > 0,
            "speed": ready and doc.frames > 0,
            "learn_noise": sel,
            "reduce_noise": ready and doc.frames > 0 and self._noise_profile() is not None,
            "zoom_in": ready,
            "zoom_out": ready,
            "zoom_sel": sel,
            "fit": ready,
            "spectrogram": doc is not None,
        }
        for key, btn in self.edit_buttons.items():
            btn.setEnabled(bool(enabled[key]))
        length = self._length()
        for btn in (self.home_button, self.end_button, self.play_button):
            btn.setEnabled(ready and length > 0)
        self.stop_button.setEnabled(self.player.active)
        self.record_button.setEnabled(self.recording or not self.busy)
        self.record_button.setText("■ Stop recording" if self.recording else "● Record")
        self.play_button.setText("❚❚ Pause" if self.player.active and not self.player.paused
                                 else "▶ Play")  # fmt: skip
        self.undo_button.setEnabled(ready and doc.can_undo())
        self.redo_button.setEnabled(ready and doc.can_redo())
        self.undo_button.setToolTip(f"Undo {doc.undo_label()} (Ctrl+Z)" if ready else "Undo")
        self.redo_button.setToolTip(f"Redo {doc.redo_label()} (Ctrl+Y)" if ready else "Redo")
        self.save_button.setEnabled(ready and length > 0)
        self.save_as_button.setEnabled(ready and length > 0)
        self.tracks_panel.setVisible(doc is not None)
        self.tracks_panel.set_enabled(not self.busy)
        self.open_button.setEnabled(not self.busy)
        self.times.setVisible(doc is not None)
        for box in self.time_boxes.values():
            box.setEnabled(ready)
        if doc is None:
            self.info_label.setText("")
            self.title.setText("")
            return
        a, b = doc.selection
        t = ops.format_time
        off = self.offset  # the boxes show mix time, like the ruler
        for key, frames in (("start", a + off), ("end", b + off), ("length", b - a)):
            self.time_boxes[key].show_time(t(frames / doc.rate))
        layout = {1: "Mono", 2: "Stereo"}.get(doc.channels, f"{doc.channels} channels")
        info = f"{doc.rate / 1000:g} kHz · {layout} · {t(doc.duration)}"
        if len(self.tracks) > 1:
            info += f" · mix of {len(self.tracks)} tracks: {t(length / doc.rate)}"
        if self.dirty:
            info += " · Unsaved changes"
        self.info_label.setText(info)
        names = [p.name for p in self.sources]
        self.title.setText(names[0] + (f" + {len(names) - 1} more" if len(names) > 1 else "")
                           if names else "")  # fmt: skip

    def _sync_scrollbar(self) -> None:
        v = self.view
        total = v.frames
        page = v.visible_frames()
        self.scroll.blockSignals(True)
        if total <= page or total == 0:
            self.scroll.setRange(0, 0)
        else:
            unit = page / SCROLL_STEPS
            self.scroll.setRange(0, int((total - page) / unit))
            self.scroll.setPageStep(SCROLL_STEPS)
            self.scroll.setSingleStep(SCROLL_STEPS // 10)
            self.scroll.setValue(int(v.start / unit))
        self.scroll.blockSignals(False)

    def _on_scroll(self, value: int) -> None:
        self.view.set_start(value * self.view.visible_frames() / SCROLL_STEPS)

    # ------------------------------------------------------------ editing
    def run(self, key: str) -> bool:
        """One tool-row action by name. True if it did something."""
        doc = self.doc
        if key == "spectrogram":  # only changes the view: fine while busy
            return doc is not None and self.set_spectrogram(not self.view.spectrogram)
        if doc is None or self.busy:
            return False
        view = self.view
        if key == "learn_noise":
            return self.learn_noise()
        if key == "zoom_in":
            view.zoom(2.0)
        elif key == "zoom_out":
            view.zoom(0.5)
        elif key == "fit":
            view.fit()
        elif key == "zoom_sel":
            a, b = doc.selection
            view.zoom_to(a + self.offset, b + self.offset)
        elif key == "select_all":
            doc.select_all()
        elif key == "deselect":
            doc.set_selection(doc.selection[0])
        elif key == "copy":
            clip = doc.copy()
            if clip is None:
                return False
            self.clipboard = clip
        else:
            return self._edit(key)
        view.update()
        self._update_state()
        return True

    def _edit(self, key: str) -> bool:
        doc = self.doc
        self.stop()
        if key == "cut":
            clip = doc.cut()
            changed = clip is not None
            if changed:
                self.clipboard = clip
        elif key == "paste":
            changed = self.clipboard is not None and doc.paste(self.clipboard)
        elif key == "amplify":
            a, b = doc.span()
            dlg = AmplifyDialog(ops.peak_db(doc.samples[:, a:b]), self)
            if not dlg.exec():
                return False
            changed = doc.amplify(dlg.db.value())
        elif key == "speed":
            return self._start_speed()
        elif key == "reduce_noise":
            return self._start_noise()
        elif key in ("delete", "trim", "silence", "fade_in", "fade_out", "normalize", "reverse"):
            changed = getattr(doc, key)()
        else:
            raise KeyError(key)
        if key == "normalize" and not changed:
            self.show_message("Already at −1 dB (or silent): nothing to change.")
        self._after_edit()
        return changed

    def _start_speed(self) -> bool:
        """Ask how, then change the speed / tempo / pitch on the worker thread (False: not
        started). The result replaces the part in ``_changed_part``."""
        doc = self.doc
        a, b = doc.span()
        seconds = (b - a) / doc.rate
        if seconds < speed.MIN_SECONDS:
            self.show_message(speed.TOO_SHORT, error=True)
            return False
        dlg = SpeedDialog(seconds, doc.rate, *self._last_speed, parent=self)
        if not dlg.exec():
            return False
        mode, value = self._last_speed = dlg.choice()
        if speed.is_identity(mode, value):
            return False
        grows = (speed.new_seconds(mode, value, seconds, doc.rate) - seconds) * doc.rate
        if self._used_samples() + grows * doc.channels > MAX_SAMPLES:
            self.show_message(TOO_LONG_RESULT, error=True)
            return False
        rate = doc.rate
        return self._start_change(
            SPEED_LABELS[mode],
            f"Changing the {mode}…",
            a,
            b,
            lambda part, cancel, progress: change_speed(part, rate, mode, value, cancel, progress),
        )

    def _start_change(self, label: str, message: str, a: int, b: int, fn: Callable) -> bool:
        """Run ``fn(part, cancel_event, progress)`` on the worker thread for ``[a, b)``; its
        result replaces that part in ``_changed_part`` (one undo step called ``label``).
        Returns False: nothing has changed yet."""
        part = self.doc.samples[:, a:b].copy()
        self.changing = True
        self._change_request = (label, a, b)
        self._cancel = cancel = threading.Event()
        self.cancel_button.show()
        self.show_message(message)
        self._update_state()
        self._start(
            lambda progress: fn(part, cancel, lambda f: progress(f, message)),
            f"Couldn't do “{label}”. See the log for details.",
        )
        return False

    def _start_noise(self) -> bool:
        """Ask how much, then reduce the learned noise in the selection (or everything)."""
        doc = self.doc
        profile = self._noise_profile()
        if profile is None:
            self.show_message(noise.NO_PROFILE, error=True)
            return False
        a, b = doc.span()
        dlg = NoiseDialog(*self._last_noise, parent=self)
        if not dlg.exec():
            return False
        self._last_noise = settings = dlg.values()
        rate = doc.rate
        return self._start_change(
            "Reduce noise",
            "Reducing noise…",
            a,
            b,
            lambda part, cancel, progress: noise.reduce(
                part, rate, profile, *settings, cancel_event=cancel, progress=progress
            ),
        )

    def learn_noise(self) -> bool:
        """The selection is background noise only: remember its profile for Reduce noise."""
        doc = self.doc
        if doc is None or self.busy or not doc.has_selection:
            return False
        a, b = doc.selection
        try:
            profile = noise.learn(doc.samples[:, a:b], doc.rate)
        except ValueError as exc:
            self.show_message(str(exc), error=True)
            return False
        self.noise = (doc.rate, profile)
        seconds = min((b - a) / doc.rate, noise.LEARN_MAX_SECONDS)
        self.show_message(
            f"Noise learned from {ops.format_time(seconds)}. Now select what to clean "
            "(or nothing, for everything) and press “Reduce noise…”."
        )
        self._update_state()
        return True

    def _noise_profile(self) -> np.ndarray | None:
        """The learned profile, if it was learned at this sound's sample rate."""
        if self.noise is None or self.doc is None or self.noise[0] != self.doc.rate:
            return None
        return self.noise[1]

    def cancel_change(self) -> None:
        if self.changing:
            self._cancel.set()

    def _changed_part(self, samples: np.ndarray) -> None:
        label, a, b = self._change_request
        if self.doc is None:
            return
        if a == b:  # an insertion at the cursor (a recording)
            self.doc.set_selection(a)
            done = self.doc.paste(samples, label)
        else:
            done = self.doc.replace_part(label, a, b, samples)
        if done:
            self.show_message("")
            self._after_edit()

    def _after_edit(self) -> None:
        self.view._clamp()
        self.view.update()
        self.view.view_changed.emit()
        self._update_state()

    def undo(self) -> None:
        if self.doc is not None and not self.busy:
            self.stop()
            if self.doc.undo():
                self._after_edit()

    def redo(self) -> None:
        if self.doc is not None and not self.busy:
            self.stop()
            if self.doc.redo():
                self._after_edit()

    def type_time(self, which: str, text: str) -> bool:
        """A time typed into the Start / End / Length box. False (and a hint) if it isn't one."""
        box = self.time_boxes[which]
        seconds = ops.parse_time(text)
        if seconds is None:
            box.set_error(True)
            self.show_message(ops.TIME_HINT, error=True)
            return False
        box.set_error(False)
        self.show_message("")
        if self.doc is None or self.busy:
            return False
        self.stop()
        doc = self.doc
        if which != "length":  # typed in mix time
            seconds -= self.offset / doc.rate
        sel = ops.edited_selection(which, seconds, doc.selection, doc.rate, doc.frames)
        doc.set_selection(*sel)
        box.setModified(False)  # so the box shows the real (maybe clamped) time again
        self.view.ensure_visible(doc.selection[0] + self.offset)
        self.view.update()
        self.view.selection_changed.emit()
        return True

    def go_to(self, frame: int) -> None:
        if self.doc is None:
            return
        self.stop()
        self.doc.set_selection(frame)
        self.view.ensure_visible(self.doc.selection[0] + self.offset)
        self.view.update()
        self._update_state()

    # ------------------------------------------------------------ playback
    def toggle_play(self) -> None:
        if self.doc is None or self.busy:
            return
        if self.player.active:
            if self.player.paused:
                self.player.resume()
            else:
                self.player.pause()
            self._update_state()
            return
        a, b = self.doc.selection
        off, n = self.offset, self._length()
        if b > a:
            self._play(a + off, b + off)
        else:
            self._play(a + off if a + off < n else 0, n)

    def play_from(self, frame: int) -> None:
        """Play from ``frame`` (mix time) to the end."""
        if self.doc is not None and not self.busy:
            self.stop()
            self._play(min(frame, max(0, self._length() - 1)), self._length())

    def seek(self, frame: int) -> None:
        """Middle click: playback moves there (stays paused if it was); the cursor stays put."""
        if self.doc is None or self.busy:
            return
        paused = self.player.active and self.player.paused
        frame = min(frame, max(0, self._length() - 1))
        self._play(frame, self._length(), paused=paused)
        if paused:
            self.view.play_head = frame
            self.view.update()

    def _play(self, start: int, end: int, paused: bool = False) -> None:
        if self.tracks:
            # mixed bit by bit as the player reads it, from the tracks as they are at that
            # moment: a volume / pan / mute / solo change is heard at once. Always stereo, so
            # panning a mono track works while it plays (a mono track sounds the same).
            samples = tr.LazyMix(list(self.tracks), end, channels=2)
            mixed = SimpleNamespace(rate=self.doc.rate, channels=samples.shape[0], samples=samples)
            why = self.player.play(mixed, start, end, paused)
        else:
            why = self.player.play(self.doc, start, end, paused)
        if why:
            self.show_message(why, error=True)
        self.view.playing = self.player.active
        self._play_end = end
        self._update_state()

    def stop(self) -> None:
        """Stop playing (a recording goes on: it has its own Stop)."""
        self.player.stop()

    # ------------------------------------------------------------ recording
    def toggle_record(self) -> None:
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> bool:
        """Record from the microphone; Stop recording puts the take at the cursor."""
        if self.busy:
            return False
        self.stop()
        doc = self.doc
        rate = doc.rate if doc is not None else record.NEW_RATE
        channels = doc.channels if doc is not None else record.NEW_CHANNELS
        why = self.recorder.start(rate, channels)
        if why:
            self.show_message(why, error=True)
            return False
        a, b = doc.selection if doc is not None else (0, 0)
        frames = doc.frames if doc is not None else 0
        width = doc.channels if doc is not None else 0
        others = self._used_samples() - frames * width  # the other tracks share the limit
        room = record.room(MAX_SAMPLES - others, frames, width, b - a, self.recorder.channels)
        if doc is not None and doc.rate != self.recorder.rate:  # the take is resampled after
            room = room * self.recorder.rate // doc.rate
        if room <= 0:
            self.recorder.stop()
            self.show_message(record.NO_ROOM, error=True)
            return False
        self.take = record.Take(self.recorder.rate, self.recorder.channels, room)
        self._record_at = (a, b)
        self.record_level.setValue(0)
        self.record_level.show()
        self._show_recording()
        self._update_state()
        return True

    def _show_recording(self) -> None:
        self.show_message(
            f"Recording… {ops.format_time(self.take.seconds)} — press “■ Stop recording” to finish."
        )

    def _on_record_chunk(self, chunk: np.ndarray) -> None:
        take = self.take
        if take is None:
            return
        more = take.add(chunk)
        self.record_level.setValue(round(take.level * 100))
        self._show_recording()
        if not more and not self._record_stopping:
            self.stop_recording(record.FULL)

    def _on_record_failed(self, message: str) -> None:
        if self.take is not None:
            self.stop_recording(message)

    def stop_recording(self, note: str = "") -> bool:
        """End the recording and put the take in (one undo step). False if nothing came."""
        take = self.take
        if take is None or self._record_stopping:
            return False
        self._record_stopping = True  # the last chunk read below must not stop it again
        try:
            self.recorder.stop()
        finally:
            self._record_stopping = False
        self.take = None
        self.record_level.hide()
        samples = take.samples()
        self._update_state()
        if samples.shape[1] == 0:
            self.show_message(note or "Nothing was recorded.", error=bool(note))
            return False
        if self.doc is None:
            self._opened(AudioDoc(take.rate, np.zeros((take.channels, 0), np.float32)), [])
        doc = self.doc
        samples = ops.fit_channels(samples, doc.channels)
        a, b = self._record_at if doc.frames else (0, 0)
        if take.rate == doc.rate:
            doc.set_selection(a, b)  # where recording started, wherever the cursor went since
            doc.paste(samples, record.LABEL)
            self.show_message(note)
            self._after_edit()
            return True
        source_rate, rate = take.rate, doc.rate
        self._start_change(
            record.LABEL,
            "Converting the recording to the sound's sample rate…",
            a,
            b,
            lambda _part, cancel, progress: resample(samples, source_rate, rate, cancel, progress),
        )
        return True

    def set_spectrogram(self, on: bool) -> bool:
        """Spectrogram (True) or waveform lanes; remembered for next time."""
        self.view.set_spectrogram(on)
        self.edit_buttons["spectrogram"].setChecked(on)
        QSettings().setValue(SPECTROGRAM_KEY, on)
        return True

    def _show_volume(self) -> None:
        value = self.volume_slider.value()
        self.volume_label.setText(f"{value} %")
        self.volume_slider.setToolTip(f"Playback volume ({value} %); saving is not affected")
        self.player.set_volume(value / 100)

    def _on_volume(self, value: int) -> None:
        self._show_volume()
        QSettings().setValue(VOLUME_KEY, value)

    def _on_play_position(self, frame: int) -> None:
        before, self.view.play_head = self.view.play_head, min(frame, self._play_end)
        self.view.follow(before, self.view.play_head)
        self.view.update()

    def _on_play_finished(self) -> None:
        self.view.play_head = None
        self.view.playing = False
        self.view.update()
        self._update_state()

    # ------------------------------------------------------------ workers
    def _start(self, fn: Callable, fallback: str) -> None:
        """Run ``fn(progress)`` on a thread; the result goes to ``_on_done`` / ``_on_failed``."""
        thread = QThread(self)
        worker = _Worker(fn, fallback)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Bound methods of this page (never lambdas): Qt queues them onto the GUI thread.
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.progress.connect(self._on_progress)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._job_finished)
        self._job = (thread, worker)  # kept until QThread.finished (CLAUDE.md)
        thread.start()

    @Slot(float, str)
    def _on_progress(self, fraction: float, message: str) -> None:
        self.show_message(f"{message} {round(fraction * 100)} %")

    @Slot(object)
    def _on_done(self, result: object) -> None:
        if self.opening:
            self._opened(result, self._open_request)
        elif self.saving:
            self._save_peak = result if isinstance(result, float) else -float("inf")
            self._saved(*self._save_request)
        elif self.changing:
            self._changed_part(result)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self.opening:
            self._open_failed(message)
        elif self.changing and not message:
            self.show_message("Cancelled: nothing was changed.")
        else:
            self.show_message(message, error=True)

    @Slot()
    def _job_finished(self) -> None:
        thread, worker = self._job
        self._job = None
        worker.deleteLater()
        thread.deleteLater()
        if self.opening:
            self.opening = self._open_as_track = False
            self.opened.emit()
        if self.saving:
            self.saving = False
            self.save_done.emit()
        if self.changing:
            self.changing = False
            self.cancel_button.hide()
            self.change_done.emit()
        self._update_state()

    def _wait(self, signal) -> None:
        if self._job is not None:
            loop = QEventLoop()
            signal.connect(loop.quit)  # connected first, then checked: no missed signal
            if self._job is not None:
                loop.exec()
            signal.disconnect(loop.quit)

    # ------------------------------------------------------------ opening
    def open_empty(self) -> None:
        if not self.busy:
            self.stop()
            self.doc, self.sources, self._save_target = None, [], None
            self._set_tracks([], 0)
            self.view.set_document(None)
            self._show_placeholder(EMPTY_TEXT)
            self.show_message("")
            self._update_state()

    def open_paths(self, paths: list[Path]) -> bool:
        """Open (and join) ``paths`` on a worker thread; ``opened`` is emitted when done."""
        paths = [Path(p) for p in paths]
        if not paths or self.busy:
            return False
        self.stop()
        self.opening = True
        self._cancel = cancel = threading.Event()
        name = paths[0].name if len(paths) == 1 else f"{len(paths)} files"
        self._show_placeholder(f"Opening {name}…")
        self.show_message("")
        self._update_state()
        self._open_request = paths
        self._start(
            lambda progress: open_audio(paths, cancel, progress),
            f"Couldn't open {name}. See the log for details.",
        )
        return True

    def open_project(self, path: Path) -> bool:
        """Open a .umcaudio project (every track, its settings and timing) on a worker thread."""
        path = Path(path)
        if self.busy:
            return False
        self.stop()
        self.opening = True
        self._cancel = threading.Event()
        self._show_placeholder(f"Opening {path.name}…")
        self.show_message("")
        self._update_state()
        self._open_request = [path]
        self._start(
            lambda _progress: aproj.from_bytes(path.read_bytes()),
            f"Couldn't open {path.name}. See the log for details.",
        )
        return True

    def _project_opened(self, tracks: list[tr.Track], active: int, path: Path) -> None:
        self.sources, self._save_target = [path], (path, aproj.EXT)
        self.doc = tracks[active].doc
        self._set_tracks(tracks, active)
        self._tracks_saved_rev = self._tracks_rev
        self.center.setCurrentIndex(0)
        self._sync_timing()
        self.view.set_document(self.doc)
        n = len(tracks)
        self.show_message(f"Opened the project “{path.name}”: {n} track{'' if n == 1 else 's'}.")
        self.view.setFocus()

    def _opened(self, doc: AudioDoc, paths: list[Path]) -> None:
        if isinstance(doc, tuple):  # a project: (tracks, chosen track)
            self._project_opened(doc[0], doc[1], paths[0])
            return
        if self._open_as_track:
            self._track_opened(doc, paths)
            return
        self.doc, self.sources, self._save_target = doc, paths, None
        name = paths[0].stem if len(paths) == 1 else "Track 1"
        self._set_tracks([tr.Track(doc, name)], 0)
        self._tracks_saved_rev = self._tracks_rev
        self.center.setCurrentIndex(0)
        self._sync_timing()
        self.view.set_document(doc)
        self.show_message("")
        self.view.setFocus()

    # ------------------------------------------------------------ tracks
    def _set_tracks(self, tracks: list[tr.Track], active: int) -> None:
        self.tracks, self.active_track = tracks, active
        self._tracks_rev += 1
        self.tracks_panel.set_tracks(tracks, active)

    def _on_tracks_changed(self) -> None:
        """A volume / pan / mute / solo changed (allowed while busy; heard at once while playing:
        the player mixes from the live tracks)."""
        self._tracks_rev += 1
        self._update_state()

    def select_track(self, i: int) -> bool:
        """Edit track ``i`` (its own selection and undo)."""
        if self.busy or not 0 <= i < len(self.tracks):
            self.tracks_panel.set_tracks(self.tracks, self.active_track)
            self.tracks_panel.set_enabled(not self.busy)
            return False
        self._show_track(i)
        return True

    def _show_track(self, i: int) -> None:
        if i != self.active_track or self.doc is not self.tracks[i].doc:
            self.stop()
            self.active_track = i
            self.doc = self.tracks[i].doc
            self._sync_timing()
            self.view.set_document(self.doc)
        self.tracks_panel.set_tracks(self.tracks, i)
        self._update_state()  # also the strip's enabled state

    def rename_track(self, i: int, name: str) -> bool:
        """Give track ``i`` a new name (only a label here: the saved mix doesn't carry it)."""
        name = clean_name(name)
        if not name or not 0 <= i < len(self.tracks):
            return False
        self.tracks[i].name = name
        self.tracks_panel.set_tracks(self.tracks, self.active_track)
        self._update_state()
        return True

    def _on_track_moving(self, _i: int) -> None:
        """A lane is being dragged: the waveform follows at once (heard from the next Play)."""
        self._sync_timing()
        self.view.update()

    def _on_track_moved(self, _i: int) -> None:
        self._tracks_rev += 1
        self._update_state()

    def set_track_start(self, i: int, frames: int) -> bool:
        """Start track ``i`` at ``frames`` into the mix (not an undo step, like its volume)."""
        if self.busy or not 0 <= i < len(self.tracks):
            return False
        frames = max(0, int(frames))
        if self.tracks[i].offset == frames:
            return False
        self.stop()
        self.tracks[i].offset = frames
        self._on_track_moved(i)
        return True

    def ask_text(self, title: str, label: str, text: str) -> str | None:
        """One line of text, or None if cancelled (tests replace it)."""
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value if ok else None

    def ask_track_start(self, i: int) -> bool:
        """ "Start time…": ask when track ``i`` starts. False if cancelled or not a time."""
        if self.busy or not 0 <= i < len(self.tracks):
            return False
        t = self.tracks[i]
        rate = t.doc.rate
        text = self.ask_text("Start time", f"When does “{t.name}” start?",
                             ops.format_time(t.offset / rate))  # fmt: skip
        if text is None:
            return False
        seconds = ops.parse_time(text)
        if seconds is None:
            self.show_message(START_HINT, error=True)
            return False
        self.show_message("")
        return self.set_track_start(i, round(seconds * rate)) or True

    def duplicate_track(self, i: int) -> bool:
        """A copy of track ``i`` right below it (its sound, settings and start), then edit it."""
        if self.busy or not 0 <= i < len(self.tracks):
            return False
        t = self.tracks[i]
        if self._used_samples() + t.doc.samples.size > MAX_SAMPLES:
            self.show_message(TRACK_NO_ROOM, error=True)
            return False
        self.stop()
        copy = tr.duplicate(t, tr.copy_name(t.name, self.tracks))
        tracks = [*self.tracks[: i + 1], copy, *self.tracks[i + 1 :]]
        active = self.active_track + (1 if self.active_track > i else 0)
        self._set_tracks(tracks, active)
        self._show_track(i + 1)
        return True

    # ------------------------------------------------------------ waveform menu
    def wave_menu(self, frame: int) -> QMenu | None:
        """The right-click menu at ``frame`` (mix time): what to do with the selection when the
        click is on it, else Play from here / Paste here / Select all."""
        doc = self.doc
        if doc is None:
            return None
        menu = QMenu(self.view)
        a, b = doc.selection
        off = self.offset
        if doc.has_selection and a + off <= frame <= b + off:
            for item in SELECTION_MENU:
                if item is None:
                    menu.addSeparator()
                    continue
                key, label = item
                if key == "play_sel":
                    act = menu.addAction(label, lambda: self._play(a + off, b + off))
                    act.setEnabled(not self.busy)
                else:
                    act = menu.addAction(label, lambda k=key: self.run(k))
                    act.setEnabled(self.edit_buttons[key].isEnabled())
            return menu
        here = max(0, min(doc.frames, frame - off))
        act = menu.addAction("Play from here", lambda: self.play_from(frame))
        act.setEnabled(not self.busy and frame < self._length())
        act = menu.addAction("Paste here", lambda: self._paste_at(here))
        # before the track starts there's nowhere to paste (after its end = add to the end)
        act.setEnabled(not self.busy and self.clipboard is not None and frame >= off)
        act = menu.addAction("Select all", lambda: self.run("select_all"))
        act.setEnabled(not self.busy and doc.frames > 0)
        return menu

    def _paste_at(self, frame: int) -> bool:
        if self.doc is None or self.busy or self.clipboard is None:
            return False
        self.doc.set_selection(frame)
        return self.run("paste")

    def show_wave_menu(self, frame: int, pos) -> None:
        menu = self.wave_menu(frame)
        if menu is not None:
            self.exec_menu(menu, pos)
            menu.deleteLater()

    def exec_menu(self, menu: QMenu, pos) -> None:
        """Show a menu until it is closed (tests replace it)."""
        menu.exec(pos)

    def _add_track(self, doc: AudioDoc, name: str) -> None:
        self._set_tracks([*self.tracks, tr.Track(doc, name)], self.active_track)
        self._show_track(len(self.tracks) - 1)  # also right after a file job (still busy)

    def add_empty_track(self) -> bool:
        """A silent track (no frames) to Record or Paste into."""
        if self.busy or self.doc is None:
            return False
        rate, channels = self.tracks[0].doc.rate, tr.out_channels(self.tracks)
        empty = AudioDoc(rate, np.zeros((channels, 0), np.float32))
        self._add_track(empty, tr.next_name(self.tracks))
        self.show_message("New empty track: press “● Record” or paste sound into it.")
        return True

    def choose_track_file(self) -> None:
        if self.busy or self.doc is None:
            return
        exts = " ".join(f"*.{e}" for e in sorted(AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, "Add a track", str(self._folder()), f"Audio ({exts});;All files (*)"
        )
        if path:
            self.add_track_file(Path(path))

    def add_track_file(self, path: Path) -> bool:
        """Read ``path`` (at the tracks' sample rate) on a worker thread into a new track."""
        if self.busy or self.doc is None:
            return False
        self.stop()
        path = Path(path)
        rate = self.tracks[0].doc.rate
        self.opening = self._open_as_track = True
        self._cancel = cancel = threading.Event()
        self.show_message(f"Adding “{path.name}”…")
        self._update_state()
        self._open_request = [path]

        def job(progress) -> AudioDoc:
            got, x = decode(path, rate, None, cancel, lambda f: progress(f, "Adding the track…"))
            return AudioDoc(got, x)

        self._start(job, f"Couldn't add “{path.name}”. See the log for details.")
        return True

    def _track_opened(self, doc: AudioDoc, paths: list[Path]) -> None:
        if self._used_samples() + doc.samples.size > MAX_SAMPLES:
            self.show_message(TRACK_NO_ROOM, error=True)
            return
        self._add_track(doc, paths[0].stem if paths else tr.next_name(self.tracks))
        self.show_message("")

    def confirm(self, text: str) -> bool:
        """Yes / No question (tests replace it)."""
        answer = QMessageBox.question(self, "Audio editor", text)
        return answer == QMessageBox.StandardButton.Yes

    def remove_track(self, i: int) -> bool:
        if self.busy or not 0 <= i < len(self.tracks):
            return False
        t = self.tracks[i]
        if t.doc.frames and not self.confirm(f"Remove “{t.name}”? Its sound is lost."):
            return False
        if len(self.tracks) == 1:
            self.open_empty()
            self._tracks_saved_rev = self._tracks_rev  # nothing left to save
            return True
        self.stop()
        rest = [x for j, x in enumerate(self.tracks) if j != i]
        active = self.active_track - (1 if i < self.active_track else 0)
        active = min(active, len(rest) - 1)
        self._set_tracks(rest, active)
        self._show_track(active)
        return True

    def _open_failed(self, message: str) -> None:
        if self._open_as_track:
            self.show_message(message, error=bool(message))
            return
        self._show_placeholder(EMPTY_TEXT if self.doc is None else "")
        if self.doc is not None:
            self.center.setCurrentIndex(0)
        self.show_message(message, error=bool(message))

    def choose_files(self) -> None:
        if self.busy:
            return
        exts = " ".join(f"*.{e}" for e in sorted(AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open audio",
            str(self._folder()),
            f"Audio and projects ({exts} *.{aproj.EXT});;All files (*)",
        )
        if paths:
            self.drop_paths([Path(p) for p in paths])

    def drop_paths(self, paths: list[Path]) -> None:
        """Dropped / chosen files: the audio ones replace the current sound (joined); a project
        (.umcaudio) replaces it with its tracks."""
        project = next((p for p in paths if p.suffix.lower() == f".{aproj.EXT}"), None)
        if project is not None:
            if self.maybe_leave():
                self.open_project(project)
            return
        found = [f for f in (detect(p) for p in paths) if f is not None]
        audio = [f.path for f in found if f.family == MediaFamily.AUDIO]
        if not audio:
            self.show_message("Only audio files can be opened here.", error=True)
            return
        if self.maybe_leave():
            self.open_paths(audio)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and not self.busy:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.drop_paths(paths)

    # ------------------------------------------------------------ saving
    def _folder(self) -> Path:
        return self.sources[0].parent if self.sources else Path.home()

    def _default_path(self) -> Path:
        if self._save_target is not None:
            return self._save_target[0]
        stem = self.sources[0].stem if self.sources else "sound"
        ext = self.sources[0].suffix.lstrip(".").lower() if self.sources else "wav"
        ids = [e for e, _ in SAVE_FORMATS]
        return self._folder() / f"{stem} edited.{ext if ext in ids else 'wav'}"

    def choose_project_path(self, default: Path) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", str(default), f"Audio editor project (*.{aproj.EXT})"
        )
        return Path(path) if path else None

    def save_project(self) -> bool:
        """Save every track separately (with its volume, balance and start) to a project file."""
        if self.doc is None or self.busy or not self.tracks:
            return False
        stem = self.sources[0].stem if self.sources else "sound"
        if self._save_target is not None and self._save_target[1] == aproj.EXT:
            default = self._save_target[0]
        else:
            default = self._folder() / f"{stem}.{aproj.EXT}"
        path = self.choose_project_path(default)
        if path is None:
            return False
        if path.suffix.lower() != f".{aproj.EXT}":
            path = path.with_name(f"{path.name}.{aproj.EXT}")
        self._start_save(path, aproj.EXT)
        return True

    def save(self) -> bool:
        if self._save_target is None:
            return self.save_as()
        if self.doc is None or self.busy:
            return False
        self._start_save(*self._save_target)
        return True

    def save_as(self) -> bool:
        """Ask for a file name and format and start saving. False if the user cancelled."""
        if self.doc is None or self.busy or self._length() == 0:
            return False
        default = self._default_path()
        ids = [e for e, _ in SAVE_FORMATS]
        filters = [f"{label} (*.{e})" for e, label in SAVE_FORMATS]
        ext = default.suffix.lstrip(".").lower()
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save audio as", str(default), ";;".join(filters), filters[ids.index(ext)]
        )
        if not path:
            return False
        path = Path(path)
        ext = path.suffix.lstrip(".").lower()
        if ext not in ids:
            ext = ids[filters.index(chosen)] if chosen in filters else "wav"
            path = path.with_name(f"{path.name}.{ext}")
        if ext in LOSSY_EXTS:
            dlg = BitrateDialog(ext, self._bitrate, self)
            if not dlg.exec():
                return False
            self._bitrate = dlg.value()
        self._start_save(path, ext)
        return True

    def _start_save(self, path: Path, fmt: str) -> None:
        self.stop()
        doc, kbps = self.doc, self._bitrate
        self.saving = True
        self._last_save_ok = False
        self._saving_rev = doc.revision
        self._saving_state = [(t.doc, t.doc.revision) for t in self.tracks]
        self._saving_tracks_rev = self._tracks_rev
        if fmt == aproj.EXT:  # the tracks, separately: copies, so editing on can't race the save
            snapshot = [
                replace(t, doc=AudioDoc(t.doc.rate, t.doc.samples.copy())) for t in self.tracks
            ]
            active = self.active_track

            def write_project(_progress):
                _write_file(path, aproj.to_bytes(snapshot, active))

            self.show_message(f"Saving the project “{path.name}”…")
            self._save_request = (path, fmt)
            self._start(write_project, f"Couldn't save “{path.name}”. See the log for details.")
            return
        if self.mixing:  # mixed on the worker thread from a copy of the settings
            snapshot = [replace(t) for t in self.tracks]
            tags = self.tracks[0].doc.tags

            def job(progress):
                mixed = AudioDoc(doc.rate, tr.mix(snapshot))
                mixed.tags = tags
                save_audio(mixed, path, fmt, kbps, lambda pct, _m: progress(pct / 100, "Saving…"))
                return tr.peak_db(mixed.samples)

            self.show_message(f"Saving the mix of {len(snapshot)} tracks as “{path.name}”…")
            self._save_request = (path, fmt)
            self._start(job, f"Couldn't save “{path.name}”. See the log for details.")
            return
        self._update_state()
        self.show_message(f"Saving “{path.name}”…")
        self._save_request = (path, fmt)
        self._start(
            lambda progress: save_audio(
                doc, path, fmt, kbps, lambda pct, _m: progress(pct / 100, "Saving…")
            ),
            f"Couldn't save “{path.name}”. See the log for details.",
        )

    def _saved(self, path: Path, fmt: str) -> None:
        self._save_target = (path, fmt)
        self._last_save_ok = True
        if self.doc is not None and self.doc.revision == self._saving_rev:
            self.doc.mark_saved()
        for doc, rev in self._saving_state:
            if doc.revision == rev:
                doc.mark_saved()
        if self._tracks_rev == self._saving_tracks_rev:
            self._tracks_saved_rev = self._tracks_rev
        if self._save_peak > 0:
            peak = tr.LOUD.format(db=f"+{self._save_peak:.1f} dB")
            self.show_message(f"Saved “{path.name}”. {peak}", error=True)
        else:
            self.show_message(f"Saved “{path.name}”.")
        self._save_peak = -float("inf")

    # ------------------------------------------------------------ leaving
    def _ask_save_changes(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes to this sound?")
        box.setInformativeText("Your edits will be lost if you don't save them.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = QMessageBox.StandardButton(box.exec())
        return {
            QMessageBox.StandardButton.Save: "save",
            QMessageBox.StandardButton.Discard: "discard",
        }.get(answer, "cancel")

    def maybe_leave(self) -> bool:
        """True when it's fine to close the editor (saved, discarded, or nothing changed)."""
        if self._leaving:
            return False
        self._leaving = True
        try:
            self.stop()
            if self.recording:  # keep what was recorded; it's then asked about like any edit
                self.stop_recording()
                if self.changing:  # converting the take's sample rate: let it finish
                    self._wait(self.change_done)
            if self.opening:
                self._cancel.set()
                self._wait(self.opened)
            if self.saving:
                self._wait(self.save_done)
            if self.changing:
                self._cancel.set()
                self._wait(self.change_done)
            if self.doc is None or not self.dirty:
                return True
            answer = self._ask_save_changes()
            if answer == "discard":
                return True
            if answer == "save" and self.save():
                self._wait(self.save_done)
                return self._last_save_ok and not self.dirty
            return False
        finally:
            self._leaving = False
