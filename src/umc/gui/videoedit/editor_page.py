"""The video editor page: top bar, media list, preview, inspector and timeline."""

from __future__ import annotations

import contextlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
from umc.core.errors import ConversionError
from umc.gui.videoedit.dialogs import ExportDialog, LocateDialog
from umc.gui.videoedit.inspector import Inspector, change_label, target_of
from umc.gui.videoedit.media_list import MediaList
from umc.gui.videoedit.player import MediaScanner, Player
from umc.gui.videoedit.preview_view import FrameService, PreviewView, frame_number
from umc.gui.videoedit.timeline import TimelineView
from umc.videoedit import edits
from umc.videoedit.history import History
from umc.videoedit.media import PROJECT_SUFFIX, new_project, probe_source
from umc.videoedit.project import (
    AudioClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    find,
    layout,
    length,
    output_fps,
)
from umc.videoedit.projectfile import load_project, save_project

__all__ = ["FrameService", "VideoEditorPage"]

log = logging.getLogger(__name__)

PROJECT_FILTER = "Video project (*.umcvideo)"


def _media_filter() -> str:
    exts = sorted(VIDEO_EXTS | IMAGE_EXTS | AUDIO_EXTS)
    return f"Videos, pictures and music ({' '.join(f'*.{e}' for e in exts)});;All files (*)"


class _SaveWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, path: Path) -> None:
        super().__init__()
        self.project, self.path = project, path

    @Slot()
    def run(self) -> None:
        try:
            save_project(self.project, self.path)
            self.succeeded.emit(str(self.path))
        except ConversionError as exc:
            log.error("video project save failed: %s\n%s", exc, getattr(exc, "details", ""))
            self.failed.emit(str(exc))
        except Exception:
            log.exception("video project save failed")
            self.failed.emit(f"Couldn't save “{self.path.name}”. See the log for details.")


class _KeepKeysInside(QObject):
    """Lets keys without Ctrl / Alt reach the focused inspector widget instead of the page's
    single-key shortcuts (←/→ move a slider, Space ticks a box, Delete / S stay in a combo)."""

    def __init__(self, area: QWidget) -> None:
        super().__init__(area)
        self.area = area

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride:
            mods = event.modifiers() & ~(
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier
            )
            if mods == Qt.KeyboardModifier.NoModifier:
                focus = QApplication.focusWidget()
                if obj is focus and self.area.isAncestorOf(focus):
                    event.accept()
                    return True
        return False


class VideoEditorPage(QWidget):
    back_requested = Signal()
    save_done = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorPage")
        self.history: History | None = None
        self.project_path: Path | None = None
        self.playhead = 0.0
        self.saving = False
        self._last_save_ok = False
        self._save_thread: QThread | None = None
        self._save_worker: _SaveWorker | None = None
        self._pending: tuple[Project, str] | None = None  # inspector edit not pushed yet
        self._view_version = 0  # bumped whenever the shown project changes (still cache key)
        self._tmp: tempfile.TemporaryDirectory | None = tempfile.TemporaryDirectory(
            prefix="umc-vedit-"
        )
        self.tmp = Path(self._tmp.name)
        self.frames = FrameService(self.tmp, self)
        self.frames.ready.connect(self._frame_ready)
        self.frames.failed.connect(lambda msg: self.show_message(msg, error=True))
        self.player = Player(self.tmp, self)
        self.player.frame.connect(lambda img: self.preview.show_frame(img))
        self.player.position.connect(self._playing_position)
        self.player.finished.connect(self._play_finished)
        self.player.notice.connect(self.show_message)
        self.player.failed.connect(lambda msg: self.show_message(msg, error=True))
        self.scanner = MediaScanner(self.tmp, self)
        self.scanner.thumbnails.connect(lambda sid, imgs: self.timeline.set_thumbnails(sid, imgs))
        self.scanner.waveform.connect(lambda sid, peaks: self.timeline.set_waveform(sid, peaks))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        middle = QHBoxLayout()
        middle.setContentsMargins(12, 12, 12, 12)
        middle.setSpacing(12)
        middle.addWidget(self._build_media_column())
        self.preview = PreviewView()
        self.preview.step_requested.connect(self.step_frames)
        self.preview.play_toggled.connect(self._play_toggled)
        self.preview.loop_toggled.connect(lambda on: setattr(self.player, "loop", on))
        middle.addWidget(self.preview, 1)
        middle.addWidget(self._build_inspector())
        root.addLayout(middle, 1)

        self.timeline = TimelineView()
        self.timeline.edit.connect(self._timeline_edit)
        self.timeline.seek.connect(self.seek)
        self.timeline.selection_changed.connect(self._selection_changed)
        self.timeline.source_dropped.connect(self._source_dropped)
        scroll = QScrollArea(objectName="TimelineScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.timeline)
        scroll.setMinimumHeight(min(320, self.timeline.minimumHeight() + 4))
        root.addWidget(scroll)

        status = QFrame(objectName="EditorStatus")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        sl.addWidget(self.message, 1)
        root.addWidget(status)

        self._install_shortcuts()
        self._update_buttons()

    # ------------------------------------------------------------ layout
    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorTopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        def tb(text: str, tip: str, fn, name: str = "Small") -> QPushButton:
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False: fn())
            lay.addWidget(b)
            return b

        tb("← Back to Convert", "Leave the editor", self._back, "Link")
        lay.addSpacing(12)
        self.undo_button = tb("Undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_button = tb("Redo", "Redo (Ctrl+Y)", self.redo)
        lay.addStretch(1)
        self.save_button = tb("Save project", "Save project (Ctrl+S)", self.save)
        self.save_as_button = tb("Save project as…", "Save project as (Ctrl+Shift+S)", self.save_as)
        self.export_button = tb("Export…", "Export the video (Ctrl+E)", self.export, "Primary")
        return bar

    def _build_media_column(self) -> QWidget:
        host = QWidget()
        host.setFixedWidth(240)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(QLabel("MEDIA", objectName="Section"))
        self.media_list = MediaList()
        self.media_list.add_requested.connect(self._add_source_at_playhead)
        self.media_list.files_requested.connect(self._pick_files)
        self.media_list.text_requested.connect(self.add_text)
        lay.addWidget(self.media_list, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.media_list.add_button)
        buttons.addWidget(self.media_list.text_button)
        lay.addLayout(buttons)
        return host

    def _build_inspector(self) -> QWidget:
        scroll = QScrollArea(objectName="EditorSide")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(objectName="EditorSideContent")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 12, 14, 12)
        self.inspector = Inspector()
        self._keys = _KeepKeysInside(self.inspector)
        QApplication.instance().installEventFilter(self._keys)
        self.inspector.changed.connect(self._inspector_changed)
        self.inspector.committed.connect(self._flush_pending)
        lay.addWidget(self.inspector)
        scroll.setWidget(host)
        return scroll

    def _install_shortcuts(self) -> None:
        def sc(keys, fn) -> None:
            for k in keys if isinstance(keys, tuple) else (keys,):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                s.activated.connect(fn)

        sc("Space", self.preview.play_button.click)
        sc("S", self.split)
        sc(("Delete", "Backspace"), self.delete_selected)
        sc("Left", lambda: self.step_frames(-1))
        sc("Right", lambda: self.step_frames(1))
        sc("Shift+Left", lambda: self.seek(self.playhead - 1.0))
        sc("Shift+Right", lambda: self.seek(self.playhead + 1.0))
        sc("Home", lambda: self.seek(0.0))
        sc("End", lambda: self.seek(self._length()))
        sc("Ctrl+Z", self.undo)
        sc(("Ctrl+Y", "Ctrl+Shift+Z"), self.redo)
        sc("Ctrl+S", self.save)
        sc("Ctrl+Shift+S", self.save_as)
        sc("Ctrl+E", self.export)
        sc(("+", "="), self.timeline.zoom_in)
        sc("-", self.timeline.zoom_out)
        sc("L", lambda: setattr(self.preview, "loop", not self.preview.loop))

    # ------------------------------------------------------------ opening
    def open_paths(self, paths: list[Path]) -> bool:
        """Start a project from files, or open a ``.umcvideo``. False when nothing usable opened."""
        paths = [Path(p) for p in paths]
        is_project = any(p.suffix.lower() == PROJECT_SUFFIX for p in paths)
        try:
            if is_project and len(paths) == 1:
                return self._open_project_file(paths[0])
            project, messages = new_project(paths)
        except ConversionError as exc:
            self.show_message(str(exc), error=True)
            return False
        if not project.sources:
            self.show_message(" ".join(messages) or "Nothing to edit.", error=True)
            return False
        self.set_project(project, None)
        if messages:
            self.show_message(" ".join(messages), error=True)
        return True

    def open_empty(self) -> bool:
        """An empty project (the start-screen tile). False only while a save is running."""
        if self.saving:
            return False
        self.set_project(Project(ProjectSettings(), ()), None)
        return True

    def _open_project_file(self, path: Path) -> bool:
        project, missing = load_project(path)
        fixed, messages = project, []
        if missing:
            dlg = LocateDialog(missing, self)
            if not dlg.exec():
                self.show_message("Opening the project was cancelled.")
                return False
            for m in missing:
                found = dlg.choices.get(m.source_id)
                if found is not None:
                    try:
                        fixed = edits.relink(fixed, m.source_id, found, probe_source(found, "x"))
                        continue
                    except ConversionError:
                        messages.append(f"Couldn't open “{found.name}”.")
                fixed = edits.remove_source(fixed, m.source_id)
                messages.append(f"Removed “{m.path.name}” from the project.")
        self.set_project(project, path)
        if fixed is not project:
            self._push(fixed, "Fix missing files")
        if messages:
            self.show_message(" ".join(messages), error=True)
        return True

    def set_project(self, project: Project, path: Path | None) -> None:
        self._pending = None
        self.history = History(project)
        self.project_path = path
        self.playhead = 0.0
        self.timeline.set_selected(None)
        self.show_message("")
        self._after_change()

    def add_paths(self, paths: list[Path]) -> None:
        """Dropped files go into the media list (one undo step)."""
        if self.history is None:
            self.open_paths(paths)
            return
        self._flush_pending()
        project = self.history.project
        taken = {s.id for s in project.sources}
        sources: list[Source] = []
        messages: list[str] = []
        n = len(taken) + 1
        for path in (Path(p) for p in paths):
            if path.suffix.lower() == PROJECT_SUFFIX:
                messages.append(f"Open “{path.name}” on its own to edit that project.")
                continue
            while f"s{n}" in taken:
                n += 1
            try:
                sources.append(probe_source(path, f"s{n}"))
                taken.add(f"s{n}")
            except ConversionError as exc:
                messages.append(str(exc))
        if sources:
            self._apply(lambda p: edits.add_sources(p, sources), "Add files")
        if messages:
            self.show_message(" ".join(messages), error=True)

    def _pick_files(self) -> None:
        folder = self._first_source_folder()
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files", str(folder), _media_filter())
        if paths:
            self.add_paths([Path(p) for p in paths])

    # ------------------------------------------------------------ history
    def _push(self, project: Project, label: str, same_view: bool = False) -> None:
        if self.history is None:
            return
        self.history.push(project, label)
        self._after_change(new_view=not same_view)

    def _apply(self, fn, label: str) -> Any:
        """Run an edit on the current project and push it; EditError → inline message."""
        if self.history is None:
            return None
        self._flush_pending()
        try:
            result = fn(self.history.project)
        except ConversionError as exc:
            self.show_message(str(exc), error=True)
            return None
        project, extra = result if isinstance(result, tuple) else (result, None)
        self.show_message("")
        self._push(project, label)
        return extra

    def undo(self) -> None:
        self._flush_pending()
        if self.history is not None and self.history.can_undo:
            self.history.undo()
            self._after_change()

    def redo(self) -> None:
        self._flush_pending()
        if self.history is not None and self.history.can_redo:
            self.history.redo()
            self._after_change()

    def _back(self) -> None:
        self._stop_playback()
        self.back_requested.emit()

    def _after_change(self, new_view: bool = True) -> None:
        h = self.history
        if h is None:
            return
        self._stop_playback(still=False)
        self.scanner.want(h.project.sources)
        if new_view:
            self._view_version += 1
        self.timeline.set_project(h.project)
        self.media_list.set_sources(h.project.sources)
        self.inspector.show_item(h.project, self.timeline.selected_id)
        self._update_buttons()
        self._update_time()
        self._request_still()

    def _update_buttons(self) -> None:
        h = self.history
        self.undo_button.setEnabled(bool(h and h.can_undo))
        self.redo_button.setEnabled(bool(h and h.can_redo))
        self.undo_button.setToolTip(f"Undo {h.undo_label} (Ctrl+Z)" if h and h.can_undo else "Undo")
        self.redo_button.setToolTip(f"Redo {h.redo_label} (Ctrl+Y)" if h and h.can_redo else "Redo")
        enabled = h is not None and not self.saving
        self.save_button.setEnabled(enabled)
        self.save_as_button.setEnabled(enabled)
        self.export_button.setEnabled(h is not None)

    # ------------------------------------------------------------ inspector
    def _inspector_changed(self, changes: dict) -> None:
        """A setting is moving: preview the result; it's pushed once the edit is finished."""
        if self.history is None:
            return
        self._stop_playback(still=False)
        base = self.history.project
        item_id = self.inspector.item_id
        if not changes:
            self._pending = None
            self._view_version += 1
            self._request_still()
            return
        try:
            project = edits.apply_changes(base, item_id, changes)
            target = target_of(base, item_id)
        except (ConversionError, KeyError) as exc:
            self._pending = None
            msg = str(exc) if isinstance(exc, ConversionError) else "That item no longer exists."
            self.show_message(msg, error=True)
            self.inspector.show_item(base, self.timeline.selected_id)
            return
        self._pending = (project, change_label(target, changes))
        self._view_version += 1
        self._request_still()

    def _flush_pending(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None and self.history is not None:
            self.show_message("")
            # The preview already shows this exact project: keep its version (no new still).
            self._push(*pending, same_view=True)

    def _selection_changed(self, item_id: object) -> None:
        self._flush_pending()
        if self.history is not None:
            self.inspector.show_item(self.history.project, item_id)  # type: ignore[arg-type]

    # ------------------------------------------------------------ timeline edits
    def _timeline_edit(self, project: Project, label: str) -> None:
        self._stop_playback(still=False)
        self._pending = None  # the timeline's project doesn't include an unpushed setting
        self.show_message("")
        self._push(project, label)

    def _source_dropped(self, source_id: str, t: float, kind: str, row: int) -> None:
        self._add_source(source_id, t, row if kind == "music" else 0)

    def _add_source_at_playhead(self, source_id: str) -> None:
        self._add_source(source_id, self.playhead, 0)

    def _add_source(self, source_id: str, t: float, row: int) -> None:
        if self.history is None:
            return
        try:
            audio = self.history.project.source(source_id).kind == "audio"
        except KeyError:
            return
        new_id = self._apply(
            lambda p: edits.add_at(p, source_id, t, row), "Add music" if audio else "Add clip"
        )
        if new_id:
            self.timeline.set_selected(new_id)

    def add_text(self) -> None:
        new_id = self._apply(lambda p: edits.add_text(p, self.playhead), "Add text")
        if new_id:
            self.timeline.set_selected(new_id)

    def _split_target(self) -> str | None:
        sel = self.timeline.selected_id
        if sel is not None and not sel.startswith("tr:"):
            return sel
        if self.history is None:
            return None
        for item_id, span in layout(self.history.project).items():
            if span.row_kind == "main" and span.start < self.playhead < span.end:
                return item_id
        return None

    def split(self) -> None:
        target = self._split_target()
        if target is None:
            self.show_message("Put the playhead inside a clip to split it.")
            return
        self._apply(lambda p: edits.split(p, target, self.playhead), "Split clip")

    def delete_selected(self) -> None:
        sel = self.timeline.selected_id
        if sel is None or self.history is None:
            return
        if sel.startswith("tr:"):
            self._apply(
                lambda p: edits.update(p, sel[3:], transition_in=Transition()), "Remove transition"
            )
            return
        try:
            item = find(self.history.project, sel)[3]
        except KeyError:
            return
        label = (
            "Delete text"
            if isinstance(item, TextItem)
            else "Delete music"
            if isinstance(item, AudioClip)
            else "Delete clip"
        )
        self._apply(lambda p: edits.remove(p, sel), label)

    # ------------------------------------------------------------ preview / playhead
    def _shown_project(self) -> Project | None:
        if self._pending is not None:
            return self._pending[0]
        return self.history.project if self.history else None

    def _length(self) -> float:
        p = self._shown_project()
        return length(p) if p is not None else 0.0

    def seek(self, t: float) -> None:
        self._stop_playback(still=False)
        self.playhead = max(0.0, min(float(t), self._length()))
        self.timeline.set_playhead(self.playhead)
        self._update_time()
        self._request_still()

    def step_frames(self, n: int) -> None:
        p = self._shown_project()
        if p is None:
            return
        fps = output_fps(p)
        self.seek((frame_number(p, self.playhead) + n) / fps)

    def _update_time(self) -> None:
        self.preview.set_time(self.playhead, self._length())

    def _request_still(self) -> None:
        p = self._shown_project()
        if p is not None:
            self.frames.request(p, self._view_version, self.playhead)

    def _frame_ready(self, version: int, _t: float, image: QImage) -> None:
        if version == self._view_version:
            self.preview.show_frame(image)

    def _stop_playback(self, still: bool = True) -> None:
        was_playing = self.player.is_playing
        self.player.stop()
        self.preview.set_playing(False)
        if was_playing and still:
            self._request_still()

    def _play_toggled(self, on: bool) -> None:
        if not on:
            self._stop_playback()
            return
        self._flush_pending()
        project = self._shown_project()
        if project is None or length(project) <= 0:
            self.preview.set_playing(False)
            return
        start = self.playhead if self.playhead < length(project) - 0.02 else 0.0
        self.player.play(project, start)
        if not self.player.is_playing:
            self.preview.set_playing(False)

    def _playing_position(self, t: float) -> None:
        self.playhead = t
        self.timeline.set_playhead(t)
        self.preview.set_time(t, self.player.total)  # no layout work per tick

    def _play_finished(self) -> None:
        self.preview.set_playing(False)
        self.playhead = self._length()
        self.timeline.set_playhead(self.playhead)
        self._update_time()
        self._request_still()

    def export(self) -> None:
        """Export… opens the export window for the current project."""
        if self.history is None:
            return
        self.preview.set_playing(False)
        ExportDialog(self.history.project, self).exec()

    def on_shown(self) -> None:
        self._request_still()
        self.timeline.setFocus()

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.setObjectName("Error" if error else "Muted")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    # ------------------------------------------------------------ saving
    def _first_source_folder(self) -> Path:
        if self.history is not None and self.history.project.sources:
            return self.history.project.sources[0].path.parent
        return Path.home()

    def _default_project_path(self) -> Path:
        if self.project_path is not None:
            return self.project_path
        sources = self.history.project.sources if self.history else ()
        stem = sources[0].path.stem if sources else "video"
        return self._first_source_folder() / f"{stem}{PROJECT_SUFFIX}"

    def _ask_save_path(self, default: Path) -> Path | None:
        """The "Save project as" dialog (asks before replacing a file); None = cancelled."""
        dlg = QFileDialog(self, "Save project as", str(default.parent), PROJECT_FILTER)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setFileMode(QFileDialog.FileMode.AnyFile)
        dlg.setDefaultSuffix(PROJECT_SUFFIX.lstrip("."))
        dlg.selectFile(default.name)
        if not dlg.exec() or not dlg.selectedFiles():
            return None
        return Path(dlg.selectedFiles()[0])

    def save_as(self) -> bool:
        """Ask for a file name and start saving. False if the user cancelled."""
        if self.history is None or self.saving:
            return False
        self._flush_pending()
        path = self._ask_save_path(self._default_project_path())
        if path is None:
            return False
        self._start_save(path)
        return True

    def save(self) -> bool:
        if self.project_path is None:
            return self.save_as()
        if self.history is None or self.saving:
            return False
        self._flush_pending()
        self._start_save(self.project_path)
        return True

    def _start_save(self, path: Path) -> None:
        history = self.history
        snapshot = history.project
        self.saving = True
        self._last_save_ok = False
        self._update_buttons()
        self.show_message(f"Saving “{path.name}”…")
        thread = QThread(self)
        worker = _SaveWorker(snapshot, path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def ok(p: str) -> None:
            self._last_save_ok = True
            if self.history is history:
                self.project_path = path
                if history.project is snapshot:
                    history.mark_saved()
            self.show_message(f"Saved “{Path(p).name}”.")

        worker.succeeded.connect(ok)
        worker.failed.connect(lambda msg: self.show_message(msg, error=True))
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._save_finished)  # drops the references first…
        thread.finished.connect(worker.deleteLater)  # …then Qt deletes both
        thread.finished.connect(thread.deleteLater)
        self._save_thread, self._save_worker = thread, worker
        thread.start()

    def _save_finished(self) -> None:
        # References are kept until QThread.finished (see CLAUDE.md).
        self._save_thread, self._save_worker = None, None
        self.saving = False
        self._update_buttons()
        self.save_done.emit()

    def _wait_for_save(self) -> bool:
        if self.saving:
            loop = QEventLoop()
            self.save_done.connect(loop.quit)  # connected first, then checked: no missed signal
            if self.saving:
                loop.exec()
            self.save_done.disconnect(loop.quit)
        return self._last_save_ok

    # ------------------------------------------------------------ leaving
    def _ask_save_changes(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save project?")
        box.setInformativeText("Your edits will be lost if you don't save them.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        return {
            QMessageBox.StandardButton.Save: "save",
            QMessageBox.StandardButton.Discard: "discard",
        }.get(QMessageBox.StandardButton(answer), "cancel")

    def maybe_leave(self) -> bool:
        """True when it's fine to leave the editor (saved, discarded, or nothing changed)."""
        self._flush_pending()
        if self.saving:
            self._wait_for_save()
        if self.history is None or not self.history.dirty:
            return True
        answer = self._ask_save_changes()
        if answer == "discard":
            return True
        if answer == "save":
            started = self.save()
            return started and self._wait_for_save() and not self.history.dirty
        return False

    def shutdown(self) -> None:
        """Stop the preview thread and delete the temporary folder (safe to call twice)."""
        if self.saving:
            self._wait_for_save()
        QApplication.instance().removeEventFilter(self._keys)
        self.player.stop()
        self.preview.set_playing(False)
        self.scanner.close()
        self.frames.close()
        tmp, self._tmp = self._tmp, None
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.cleanup()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(e)
