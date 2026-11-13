"""The audio editor page, laid out like a simple Audacity: top bar (open, undo, save), a tool
row (play / stop, cut / copy / paste, effects, zoom), the waveform with a time ruler, and a
status line with the selection's start, end and length."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

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
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.audioedit import ops
from umc.audioedit.document import AudioDoc
from umc.audioedit.io import BITRATES, DEFAULT_BITRATE, SAVE_FORMATS, open_audio, save_audio
from umc.converters.audio import LOSSY_EXTS
from umc.core.detect import AUDIO_EXTS, MediaFamily, detect
from umc.core.errors import ConversionCancelled, ConversionError
from umc.gui.audioeditor.player import DEFAULT_VOLUME, Player
from umc.gui.audioeditor.wave_view import WaveView
from umc.gui.widgets.flow_layout import FlowLayout

log = logging.getLogger(__name__)

EMPTY_TEXT = "Open a sound with “Open…”, or drop audio files here (MP3, WAV, FLAC, M4A, OGG, …)."
HINT = "Click to place the cursor · drag to select · Ctrl+wheel to zoom · Space plays"
SCROLL_STEPS = 1000  # the scroll bar works in 1/1000 of a screen
VOLUME_KEY = "audioeditor/volume"  # QSettings: the volume slider, 0…100
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
    ),
    (
        ("zoom_in", "Zoom in", "Zoom in (+ or Ctrl+1, or Ctrl+wheel)"),
        ("zoom_out", "Zoom out", "Zoom out (− or Ctrl+3)"),
        ("zoom_sel", "Selection", "Zoom to the selection (Ctrl+E)"),
        ("fit", "Fit", "Show the whole sound (Ctrl+F)"),
    ),
)


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AudioEditorPage")
        self.doc: AudioDoc | None = None
        self.sources: list[Path] = []
        self.clipboard: np.ndarray | None = None
        self.opening = False
        self.saving = False
        self._cancel = threading.Event()
        self._job: tuple[QThread, _Worker] | None = None
        self._save_target: tuple[Path, str] | None = None
        self._bitrate = DEFAULT_BITRATE
        self._saving_rev = -1
        self._last_save_ok = False
        self._leaving = False
        self._play_end = 0
        self._open_request: list[Path] = []
        self._save_request: tuple[Path, str] | None = None

        self.player = Player(self)
        self.player.position_changed.connect(self._on_play_position)
        self.player.finished.connect(self._on_play_finished)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_tools())

        self.view = WaveView()
        self.view.selection_changed.connect(self._update_state)
        self.view.view_changed.connect(self._sync_scrollbar)
        self.view.seek_requested.connect(lambda f: self.play_from(f))
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
        self.selection_label = QLabel(objectName="Muted")
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        self.info_label = QLabel(objectName="Muted")
        sl.addWidget(self.selection_label)
        sl.addSpacing(16)
        sl.addWidget(self.message, 1)
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
        sc("Ctrl+Z", self.undo)
        sc(("Ctrl+Y", "Ctrl+Shift+Z"), self.redo)
        sc("Ctrl+S", self.save)
        sc("Ctrl+Shift+S", self.save_as)

    # ------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return self.opening or self.saving or self._job is not None

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _show_placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.center.setCurrentWidget(self.placeholder)

    def _update_state(self) -> None:
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
            "zoom_in": ready,
            "zoom_out": ready,
            "zoom_sel": sel,
            "fit": ready,
        }
        for key, btn in self.edit_buttons.items():
            btn.setEnabled(bool(enabled[key]))
        for btn in (self.home_button, self.end_button, self.play_button):
            btn.setEnabled(ready and doc.frames > 0)
        self.stop_button.setEnabled(self.player.active)
        self.play_button.setText("❚❚ Pause" if self.player.active and not self.player.paused
                                 else "▶ Play")  # fmt: skip
        self.undo_button.setEnabled(ready and doc.can_undo())
        self.redo_button.setEnabled(ready and doc.can_redo())
        self.undo_button.setToolTip(f"Undo {doc.undo_label()} (Ctrl+Z)" if ready else "Undo")
        self.redo_button.setToolTip(f"Redo {doc.redo_label()} (Ctrl+Y)" if ready else "Redo")
        self.save_button.setEnabled(ready and doc.frames > 0)
        self.save_as_button.setEnabled(ready and doc.frames > 0)
        self.open_button.setEnabled(not self.busy)
        if doc is None:
            self.selection_label.setText("")
            self.info_label.setText("")
            self.title.setText("")
            return
        a, b = doc.selection
        t = ops.format_time
        if b > a:
            self.selection_label.setText(
                f"Selection {t(a / doc.rate)} – {t(b / doc.rate)} · Length {t((b - a) / doc.rate)}"
            )
        else:
            self.selection_label.setText(f"Cursor {t(a / doc.rate)}")
        layout = {1: "Mono", 2: "Stereo"}.get(doc.channels, f"{doc.channels} channels")
        info = f"{doc.rate / 1000:g} kHz · {layout} · {t(doc.duration)}"
        if doc.dirty:
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
        if doc is None or self.busy:
            return False
        view = self.view
        if key == "zoom_in":
            view.zoom(2.0)
        elif key == "zoom_out":
            view.zoom(0.5)
        elif key == "fit":
            view.fit()
        elif key == "zoom_sel":
            view.zoom_to(*doc.selection)
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
        elif key in ("delete", "trim", "silence", "fade_in", "fade_out", "normalize", "reverse"):
            changed = getattr(doc, key)()
        else:
            raise KeyError(key)
        if key == "normalize" and not changed:
            self.show_message("Already at −1 dB (or silent): nothing to change.")
        self._after_edit()
        return changed

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

    def go_to(self, frame: int) -> None:
        if self.doc is None:
            return
        self.stop()
        self.doc.set_selection(frame)
        self.view.ensure_visible(self.doc.selection[0])
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
        if b > a:
            self._play(a, b)
        else:
            self._play(a if a < self.doc.frames else 0, self.doc.frames)

    def play_from(self, frame: int) -> None:
        if self.doc is not None:
            self._play(frame, self.doc.frames)

    def _play(self, start: int, end: int) -> None:
        why = self.player.play(self.doc, start, end)
        if why:
            self.show_message(why, error=True)
        self.view.playing = self.player.active
        self._play_end = end
        self._update_state()

    def stop(self) -> None:
        self.player.stop()

    def _show_volume(self) -> None:
        value = self.volume_slider.value()
        self.volume_label.setText(f"{value} %")
        self.volume_slider.setToolTip(f"Playback volume ({value} %); saving is not affected")
        self.player.set_volume(value / 100)

    def _on_volume(self, value: int) -> None:
        self._show_volume()
        QSettings().setValue(VOLUME_KEY, value)

    def _on_play_position(self, frame: int) -> None:
        self.view.play_head = min(frame, self._play_end)
        self.view.ensure_visible(self.view.play_head)
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
            self._saved(*self._save_request)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self.opening:
            self._open_failed(message)
        else:
            self.show_message(message, error=True)

    @Slot()
    def _job_finished(self) -> None:
        thread, worker = self._job
        self._job = None
        worker.deleteLater()
        thread.deleteLater()
        if self.opening:
            self.opening = False
            self.opened.emit()
        if self.saving:
            self.saving = False
            self.save_done.emit()
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

    def _opened(self, doc: AudioDoc, paths: list[Path]) -> None:
        self.doc, self.sources, self._save_target = doc, paths, None
        self.center.setCurrentIndex(0)
        self.view.set_document(doc)
        self.show_message("")
        self.view.setFocus()

    def _open_failed(self, message: str) -> None:
        self._show_placeholder(EMPTY_TEXT if self.doc is None else "")
        if self.doc is not None:
            self.center.setCurrentIndex(0)
        self.show_message(message, error=bool(message))

    def choose_files(self) -> None:
        if self.busy:
            return
        exts = " ".join(f"*.{e}" for e in sorted(AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open audio", str(self._folder()), f"Audio ({exts});;All files (*)"
        )
        if paths:
            self.drop_paths([Path(p) for p in paths])

    def drop_paths(self, paths: list[Path]) -> None:
        """Dropped / chosen files: the audio ones replace the current sound (joined)."""
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

    def save(self) -> bool:
        if self._save_target is None:
            return self.save_as()
        if self.doc is None or self.busy:
            return False
        self._start_save(*self._save_target)
        return True

    def save_as(self) -> bool:
        """Ask for a file name and format and start saving. False if the user cancelled."""
        if self.doc is None or self.busy or self.doc.frames == 0:
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
        self.show_message(f"Saved “{path.name}”.")

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
            if self.opening:
                self._cancel.set()
                self._wait(self.opened)
            if self.saving:
                self._wait(self.save_done)
            if self.doc is None or not self.doc.dirty:
                return True
            answer = self._ask_save_changes()
            if answer == "discard":
                return True
            if answer == "save" and self.save():
                self._wait(self.save_done)
                return self._last_save_ok and not self.doc.dirty
            return False
        finally:
            self._leaving = False
