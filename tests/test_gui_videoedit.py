"""Video editor GUI (offscreen): the timeline widget."""

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QSlider  # noqa: E402

from umc.videoedit.project import (  # noqa: E402
    AudioClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    VideoClip,
    clip_duration,
)


def _src(sid, kind="video", duration=10.0):
    return Source(sid, Path(f"{sid}.mp4"), kind, duration, 640, 360, 30.0, kind != "image")


def _project(main=None, text=None, music=None):
    return Project(
        ProjectSettings(),
        (_src("v"), _src("p", "image", 0.0), _src("a", "audio", 20.0)),
        main=tuple(main if main is not None else [VideoClip("c1", "v", 0.0, 2.0)]),
        text_rows=text if text is not None else ((),),
        music_rows=music if music is not None else ((),),
    )


@pytest.fixture
def make(qtbot):
    from umc.gui.videoedit.timeline import TimelineView

    def build(project):
        t = TimelineView()
        qtbot.addWidget(t)
        t.resize(900, t.minimumHeight() + 20)
        t.set_project(project)
        t.show()
        return t

    return build


def _y(t, kind, row=0):
    return t.row_y(kind, row) + 5


def _drag(qtbot, t, a, b, modifier=Qt.KeyboardModifier.NoModifier):
    qtbot.mousePress(t, Qt.MouseButton.LeftButton, modifier, pos=a)
    qtbot.mouseMove(t, pos=(a + b) / 2)
    qtbot.mouseMove(t, pos=b)
    qtbot.mouseRelease(t, Qt.MouseButton.LeftButton, modifier, pos=b)


def test_timeline_hit_test_edges(make):
    t = make(_project())
    y = _y(t, "main")
    assert t.hit_test(QPoint(t.x_for(0) + 2, y)).part == "left"
    assert t.hit_test(QPoint(t.x_for(0) + 80, y)).part == "body"
    r = t.hit_test(QPoint(t.x_for(2) - 2, y))
    assert r.part == "right" and r.item_id == "c1"
    assert t.hit_test(QPoint(t.x_for(5), y)) is None


def test_transition_handle_hit(make):
    main = [VideoClip("c1", "v", 0.0, 2.0), VideoClip("c2", "v", 0.0, 2.0)]
    t = make(_project(main))
    r = t.hit_test(QPoint(t.x_for(2), t.row_y("main") + 6))
    assert r.part == "transition" and r.item_id == "c2"
    t.select_at(QPoint(t.x_for(2), t.row_y("main") + 6))
    assert t.selected_id == "tr:c2"


def test_drag_right_edge_trims(make, qtbot):
    t = make(_project())
    y = _y(t, "main")
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, QPoint(t.x_for(2) - 2, y), QPoint(t.x_for(2) - 2 - 40, y))
    project, label = sig.args
    assert label == "Trim clip"
    assert clip_duration(project.main[0]) == pytest.approx(1.5)


def test_drag_main_reorders(make, qtbot):
    main = [VideoClip("c1", "v", 0.0, 2.0), VideoClip("c2", "v", 0.0, 2.0)]
    t = make(_project(main))
    y = _y(t, "main")
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, QPoint(t.x_for(1), y), QPoint(t.x_for(3.6), y))
    project, label = sig.args
    assert label == "Move clip"
    assert [m.id for m in project.main] == ["c2", "c1"]


def test_drag_overlapping_music_cancelled(make, qtbot):
    music = ((AudioClip("m1", "a", 0.0, 0.0, 2.0), AudioClip("m2", "a", 3.0, 0.0, 2.0)),)
    t = make(_project(music=music))
    y = _y(t, "music")
    with qtbot.assertNotEmitted(t.edit):
        _drag(qtbot, t, QPoint(t.x_for(1), y), QPoint(t.x_for(3.5), y))


def test_click_ruler_seeks(make, qtbot):
    t = make(_project())
    with qtbot.waitSignal(t.seek) as sig:
        qtbot.mouseClick(t, Qt.MouseButton.LeftButton, pos=QPoint(t.x_for(1.5), 10))
    assert sig.args[0] == pytest.approx(1.5, abs=0.02)


def test_click_selects_and_empty_deselects(make, qtbot):
    t = make(_project())
    y = _y(t, "main")
    with qtbot.waitSignal(t.selection_changed):
        qtbot.mouseClick(t, Qt.MouseButton.LeftButton, pos=QPoint(t.x_for(1), y))
    assert t.selected_id == "c1"
    with qtbot.waitSignal(t.seek):
        qtbot.mouseClick(t, Qt.MouseButton.LeftButton, pos=QPoint(t.x_for(6), y))
    assert t.selected_id is None


def _text_project():
    return _project(text=((TextItem("t1", "Hi", 3.0, 5.0),),))


def test_snapping_to_playhead(make, qtbot):
    t = make(_text_project())
    t.set_playhead(1.0)
    y = _y(t, "text")
    a = QPoint(t.x_for(4), y)
    b = QPoint(t.x_for(4) - 157, y)  # start lands ~1.04 s, within 8 px of the playhead
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, a, b)
    assert sig.args[0].text_rows[0][0].start == pytest.approx(1.0)
    assert sig.args[1] == "Move text"
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, a, b, Qt.KeyboardModifier.AltModifier)
    assert sig.args[0].text_rows[0][0].start == pytest.approx(3.0 - 157 / 80)


def test_zoom_limits(make):
    t = make(_project())
    assert t.pixels_per_second == 80
    for _ in range(40):
        t.zoom_in()
    assert t.pixels_per_second == 800
    for _ in range(80):
        t.zoom_out()
    assert t.pixels_per_second == 5


def test_add_row_button(make, qtbot):
    t = make(_project())
    with qtbot.waitSignal(t.edit) as sig:
        qtbot.mouseClick(t, Qt.MouseButton.LeftButton, pos=t.add_row_rect("text").center())
    assert len(sig.args[0].text_rows) == 2 and sig.args[1] == "Add row"


def test_source_drop_signal(make, qtbot):
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDropEvent

    t = make(_project())
    md = QMimeData()
    md.setData("application/x-umc-source", b"p")
    pos = QPointF(t.x_for(2), _y(t, "main"))
    with qtbot.waitSignal(t.source_dropped) as sig:
        t.dropEvent(
            QDropEvent(
                pos,
                Qt.DropAction.CopyAction,
                md,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
    assert sig.args[0] == "p" and sig.args[1] == pytest.approx(2.0)
    assert sig.args[2] == "main"


def _three():
    return [VideoClip(f"c{i}", "v", 0.0, 2.0) for i in (1, 2, 3)]


def test_set_project_keeps_or_drops_selection(make, qtbot):
    t = make(_project(_three()))
    t.set_selected("c2")
    t.set_project(_project(_three()))
    assert t.selected_id == "c2"
    with qtbot.waitSignal(t.selection_changed) as sig:
        t.set_project(_project([VideoClip("c1", "v", 0.0, 2.0)]))
    assert t.selected_id is None and sig.args[0] is None


def test_drag_main_left_to_front(make, qtbot):
    t = make(_project(_three()))
    y = _y(t, "main")
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, QPoint(t.x_for(5), y), QPoint(t.x_for(0.4), y))
    assert [m.id for m in sig.args[0].main] == ["c3", "c1", "c2"]


def test_drag_music_to_other_row(make, qtbot):
    music = ((AudioClip("m1", "a", 0.0, 0.0, 2.0),), ())
    t = make(_project(music=music))
    with qtbot.waitSignal(t.edit) as sig:
        _drag(
            qtbot,
            t,
            QPoint(t.x_for(1), _y(t, "music", 0)),
            QPoint(t.x_for(1), _y(t, "music", 1)),
        )
    project, label = sig.args
    assert (
        label == "Move music" and not project.music_rows[0] and project.music_rows[1][0].id == "m1"
    )


def test_text_drag_onto_other_kind_stays_on_row(make, qtbot):
    t = make(_text_project())
    with qtbot.waitSignal(t.edit) as sig:
        _drag(
            qtbot,
            t,
            QPoint(t.x_for(4), _y(t, "text")),
            QPoint(t.x_for(6), _y(t, "music")),
        )
    row = sig.args[0].text_rows[0]
    assert row[0].id == "t1" and row[0].start == pytest.approx(5.0)


def test_boundary_end_edge_reachable(make):
    plain = [VideoClip("c1", "v", 0.0, 2.0), VideoClip("c2", "v", 0.0, 2.0)]
    t = make(_project(plain))
    y = t.row_y("main") + 40
    r = t.hit_test(QPoint(t.x_for(2) - 3, y))
    assert (r.item_id, r.part) == ("c1", "right")
    r = t.hit_test(QPoint(t.x_for(2) + 3, y))
    assert (r.item_id, r.part) == ("c2", "left")
    fade = [
        VideoClip("c1", "v", 0.0, 2.0),
        VideoClip("c2", "v", 0.0, 2.0, transition_in=Transition("fade", 0.5)),
    ]
    t = make(_project(fade))
    r = t.hit_test(QPoint(t.x_for(2) - 2, y))  # c2 starts at 1.5, c1 ends at 2.0
    assert (r.item_id, r.part) == ("c1", "right")
    r = t.hit_test(QPoint(t.x_for(1.5) + 2, y))
    assert (r.item_id, r.part) == ("c2", "left")


def test_snapping_to_end_edge(make, qtbot):
    t = make(_text_project())
    t.set_playhead(8.0)
    y = _y(t, "text")
    # end of the item (5 s) dragged to ~7.95 s: its end snaps to the playhead
    with qtbot.waitSignal(t.edit) as sig:
        _drag(qtbot, t, QPoint(t.x_for(4), y), QPoint(t.x_for(4) + round(2.95 * 80), y))
    assert sig.args[0].text_rows[0][0].end == pytest.approx(8.0)


def test_noop_drag_not_emitted(make, qtbot):
    t = make(_project(_three()))
    y = _y(t, "main")
    with qtbot.assertNotEmitted(t.edit):
        _drag(qtbot, t, QPoint(t.x_for(1), y), QPoint(t.x_for(1.5), y))  # same index


def test_thumbnails_clamped_to_visible_area(make):
    from PySide6.QtGui import QImage

    t = make(_project([VideoClip("c1", "v", 0.0, 10.0)]))  # 800 px/s -> 8000 px wide
    t.pixels_per_second = 800.0
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    t.set_thumbnails("v", [img])
    calls = []
    t._draw_thumb = lambda p, i, r: calls.append(r)
    t.grab()
    tile_w = calls[0].width()
    assert 0 < len(calls) <= t.width() // tile_w + 2
    assert max(r.left() for r in calls) <= t.width()


# ------------------------------------------------------------------ editor page

from tests.conftest import needs_ffmpeg  # noqa: E402


@pytest.fixture
def vpage(qtbot, vmedia):
    from umc.gui.videoedit.editor_page import VideoEditorPage

    pg = VideoEditorPage()
    qtbot.addWidget(pg)
    pg.resize(1200, 760)
    pg.show()
    assert pg.open_paths([vmedia / "red.mp4", vmedia / "pic.png", vmedia / "music.wav"])
    yield pg
    pg._ask_save_changes = lambda: "discard"
    pg.shutdown()


def _main_ids(pg):
    return [m.id for m in pg.history.project.main]


@needs_ffmpeg
def test_page_open_puts_clips_on_rows(vpage):
    p = vpage.history.project
    assert len(p.main) == 2 and len(p.music_rows[0]) == 1
    assert vpage.media_list.count() == 3
    assert vpage.timeline.project is p


@needs_ffmpeg
def test_split_shortcut_and_undo(vpage, qtbot):
    vpage.seek(1.0)
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    vpage.timeline.setFocus()
    qtbot.keyClick(vpage.timeline, Qt.Key.Key_S)
    assert len(vpage.history.project.main) == 3
    assert vpage.undo_button.isEnabled()
    qtbot.keyClick(vpage.timeline, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(vpage.history.project.main) == 2


@needs_ffmpeg
def test_inspector_change_is_one_undo_step(vpage):
    from umc.videoedit.project import length

    before = vpage.history.project
    vpage.timeline.set_selected(before.main[0].id)
    spin = vpage.inspector.panel.field_widget("speed")
    spin.setValue(1.5)
    spin.setValue(2.0)
    assert vpage.history.project is before  # still moving: nothing pushed yet
    spin.editingFinished.emit()
    assert length(vpage.history.project) < length(before)
    assert vpage.history.project.main[0].speed == 2.0
    assert vpage.history.undo_label == "Change speed"
    vpage.undo_button.click()
    assert vpage.history.project is before
    assert not vpage.history.can_undo


@needs_ffmpeg
def test_scrub_newest_request_wins(qtbot, vmedia, tmp_path):
    from umc.gui.videoedit.editor_page import FrameService
    from umc.videoedit.media import new_project

    project, _ = new_project([vmedia / "red.mp4"])
    fs = FrameService(tmp_path)
    got = []
    fs.ready.connect(lambda v, t, img: got.append((v, t, img)))
    try:
        for v, t in ((1, 0.2), (2, 0.5), (3, 1.0)):
            fs.request(project, v, t)
        qtbot.waitUntil(lambda: bool(got), timeout=15_000)
        qtbot.wait(300)
        assert [g[0] for g in got] == [3]
        assert got[0][1] == pytest.approx(1.0) and not got[0][2].isNull()
    finally:
        fs.close()


def _patch_save_dialog(monkeypatch, path):
    from umc.gui.videoedit.editor_page import VideoEditorPage

    monkeypatch.setattr(VideoEditorPage, "_ask_save_path", lambda self, default: path)


@needs_ffmpeg
def test_save_and_reopen_project(vpage, qtbot, tmp_path, monkeypatch):
    out = tmp_path / "proj.umcvideo"
    _patch_save_dialog(monkeypatch, out)
    vpage.seek(1.0)
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    vpage.split()
    assert vpage.history.dirty
    assert vpage.save_as()
    qtbot.waitUntil(lambda: out.exists() and not vpage.saving, timeout=10_000)
    assert not vpage.history.dirty
    saved = vpage.history.project
    assert vpage.open_paths([out])
    assert vpage.history.project == saved
    assert not vpage.history.dirty


@needs_ffmpeg
def test_open_project_with_missing_source_asks(vpage, qtbot, tmp_path, monkeypatch, vmedia):
    import shutil

    from umc.gui.videoedit.dialogs import LocateDialog

    gone = tmp_path / "gone.mp4"
    shutil.copy(vmedia / "red.mp4", gone)
    out = tmp_path / "proj.umcvideo"
    _patch_save_dialog(monkeypatch, out)
    assert vpage.open_paths([gone, vmedia / "pic.png"])
    vpage.save_as()
    qtbot.waitUntil(lambda: out.exists() and not vpage.saving, timeout=10_000)
    gone.unlink()
    asked = []
    monkeypatch.setattr(LocateDialog, "exec", lambda self: asked.append(self) or 1)
    assert vpage.open_paths([out])
    assert asked
    p = vpage.history.project
    assert len(p.main) == 1 and all(s.path.name != "gone.mp4" for s in p.sources)
    assert "gone.mp4" in vpage.message.text()


@needs_ffmpeg
def test_leave_with_unsaved_changes_asks(vpage, monkeypatch):
    vpage.seek(1.0)
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    vpage.split()
    monkeypatch.setattr(vpage, "_ask_save_changes", lambda: "cancel")
    assert not vpage.maybe_leave()
    monkeypatch.setattr(vpage, "_ask_save_changes", lambda: "discard")
    assert vpage.maybe_leave()


def test_textarea_kind_edits_multiline_text(qtbot):
    from PySide6.QtWidgets import QPlainTextEdit

    from umc.core.options import option_specs
    from umc.gui.widgets.settings_panel import SettingsPanel

    assert {s.name: s.kind for s in option_specs(TextItem)}["text"] == "textarea"
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.set_schema(TextItem, "", {"text": "one\ntwo"})
    w = panel.field_widget("text")
    assert isinstance(w, QPlainTextEdit) and panel.values()["text"] == "one\ntwo"
    with qtbot.waitSignal(panel.committed):
        w.setPlainText("three")
        w.editingFinished.emit()
    assert panel.values()["text"] == "three"


# ------------------------------------------------------------------ fix round 1


def _focus(qtbot, pg, w):
    pg.activateWindow()
    w.setFocus()
    qtbot.waitUntil(w.hasFocus, timeout=2000)


@needs_ffmpeg
def test_focused_slider_gets_arrow_keys(vpage, qtbot):
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    slider = vpage.inspector.panel.field_widget("volume").findChild(QSlider)
    _focus(qtbot, vpage, slider)
    vpage.seek(1.0)
    before = slider.value()
    qtbot.keyClick(slider, Qt.Key.Key_Left)
    assert slider.value() == before - 1
    assert vpage.playhead == pytest.approx(1.0)
    qtbot.keyClick(slider, Qt.Key.Key_Home)
    assert vpage.playhead == pytest.approx(1.0)


@needs_ffmpeg
def test_focused_checkbox_gets_space(vpage, qtbot):
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    box = vpage.inspector.panel.field_widget("mute")
    _focus(qtbot, vpage, box)
    qtbot.keyClick(box, Qt.Key.Key_Space)
    assert not vpage.preview.playing
    assert vpage.history.project.main[0].mute


@needs_ffmpeg
def test_focused_combo_keeps_delete_and_s(vpage, qtbot):
    vpage.seek(1.0)
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    combo = vpage.inspector.panel.field_widget("fit")
    _focus(qtbot, vpage, combo)
    qtbot.keyClick(combo, Qt.Key.Key_Delete)
    qtbot.keyClick(combo, Qt.Key.Key_S)
    assert len(vpage.history.project.main) == 2


@needs_ffmpeg
def test_commit_renders_one_still(vpage, qtbot, monkeypatch):
    from umc.videoedit import preview

    calls = []
    real = preview.still
    monkeypatch.setattr(preview, "still", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    vpage.timeline.set_selected(_main_ids(vpage)[0])
    qtbot.wait(200)
    calls.clear()
    spin = vpage.inspector.panel.field_widget("speed")
    spin.setValue(2.0)  # still moving: the preview renders the unpushed result
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=10_000)
    spin.editingFinished.emit()  # pushes exactly that project: no second render
    qtbot.wait(400)
    assert vpage.history.project.main[0].speed == 2.0
    assert len(calls) == 1


@needs_ffmpeg
def test_save_dialog_default_suffix(vpage, qtbot, tmp_path, monkeypatch):
    seen = {}

    def fake(default):
        seen["default"] = default
        return tmp_path / "named.umcvideo"

    monkeypatch.setattr(vpage, "_ask_save_path", fake)
    assert vpage.save_as()
    qtbot.waitUntil(lambda: not vpage.saving, timeout=10_000)
    assert (tmp_path / "named.umcvideo").exists()
    assert seen["default"].name == "red.umcvideo"


@needs_ffmpeg
def test_save_error_shows_conversion_message(vpage, qtbot, tmp_path, monkeypatch):
    from umc.core.errors import ConversionError
    from umc.gui.videoedit import editor_page

    def boom(project, path):
        raise ConversionError("Disk says no.")

    monkeypatch.setattr(editor_page, "save_project", boom)
    monkeypatch.setattr(vpage, "_ask_save_path", lambda default: tmp_path / "x.umcvideo")
    vpage.save_as()
    qtbot.waitUntil(lambda: not vpage.saving, timeout=10_000)
    assert vpage.message.text() == "Disk says no."


@needs_ffmpeg
def test_shutdown_removes_temp_and_stops_thread(vpage):
    tmp = vpage.tmp
    assert tmp.exists()
    vpage.shutdown()
    assert not tmp.exists()
    assert vpage.frames._thread.isFinished()


# ---------------------------------------------------------------- playback


@pytest.fixture
def silent(monkeypatch):
    monkeypatch.setenv("UMC_NO_AUDIO", "1")


def _count_frames(pg):
    got = []
    pg.player.frame.connect(lambda _img: got.append(1))
    return got


@needs_ffmpeg
def test_play_advances_playhead(vpage, qtbot, silent):
    got = _count_frames(vpage)
    vpage.preview.play_button.click()
    assert vpage.player.is_playing
    qtbot.wait(700)
    assert 0.3 < vpage.playhead < 1.2
    assert got
    vpage.preview.play_button.click()
    assert not vpage.player.is_playing


@needs_ffmpeg
def test_edit_stops_playback(vpage, qtbot, silent):
    got = _count_frames(vpage)
    vpage.preview.play_button.click()
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    vpage.timeline.edit.emit(vpage.history.project, "Nothing")
    assert not vpage.player.is_playing
    assert not vpage.preview.playing
    qtbot.wait(100)
    got.clear()
    qtbot.wait(300)
    assert not got


@needs_ffmpeg
def test_play_reaches_end_and_stops(vpage, qtbot, silent):
    from umc.videoedit.project import length

    total = length(vpage.history.project)
    vpage.seek(total - 0.3)
    with qtbot.waitSignal(vpage.player.finished, timeout=5000):
        vpage.preview.play_button.click()
    assert vpage.playhead == pytest.approx(total, abs=0.05)
    assert not vpage.player.is_playing and not vpage.preview.playing


@needs_ffmpeg
def test_loop_restarts(vpage, qtbot, silent):
    from umc.videoedit.project import length

    total = length(vpage.history.project)
    vpage.preview.loop = True
    vpage.seek(total - 0.3)
    vpage.preview.play_button.click()
    qtbot.waitUntil(lambda: vpage.player.is_playing and vpage.playhead < 1.0, timeout=5000)
    qtbot.wait(300)
    assert vpage.player.is_playing
    vpage.preview.play_button.click()


@needs_ffmpeg
def test_thumbnails_arrive(vpage, qtbot):
    red = next(s.id for s in vpage.history.project.sources if s.path.name == "red.mp4")
    qtbot.waitUntil(lambda: bool(vpage.timeline._thumbs.get(red)), timeout=15_000)
    qtbot.waitUntil(lambda: bool(vpage.timeline._waves.get(red)), timeout=15_000)


@needs_ffmpeg
def test_shutdown_stops_playback(vpage, qtbot, silent):
    vpage.preview.play_button.click()
    qtbot.wait(200)
    vpage.shutdown()
    assert not vpage.player.is_playing


# ------------------------------------------------ real-device clock, errors, scans


class _LatencyOut:
    """Like a real device: the clock is what was heard (latency 100 ms) and stops when the
    queued sound has all been played."""

    silent = False

    def __init__(self, latency=0.1):
        import time

        self._time = time
        self._latency = latency
        self._written = 0
        self._t0 = None
        self._closed = False

    def write(self, chunk):
        from umc.videoedit.audio_out import FRAME_BYTES, SAMPLE_RATE

        if self._t0 is None:
            self._t0 = self._time.monotonic()
        self._written += len(chunk) // FRAME_BYTES
        while not self._closed and self._written / SAMPLE_RATE - self._heard() > 0.5:
            self._time.sleep(0.01)

    def _heard(self):
        from umc.videoedit.audio_out import SAMPLE_RATE

        if self._t0 is None:
            return 0.0
        return min(self._written / SAMPLE_RATE, self._time.monotonic() - self._t0)

    def played_seconds(self):
        return max(0.0, self._heard() - self._latency)

    def close(self):
        self._closed = True


@pytest.fixture
def device(monkeypatch):
    from umc.gui.videoedit import qt_audio_out

    monkeypatch.setattr(qt_audio_out, "open_qt_output", lambda: _LatencyOut())


@needs_ffmpeg
def test_play_ends_with_device_latency(vpage, qtbot, device):
    from umc.videoedit.project import length

    total = length(vpage.history.project)
    vpage.seek(total - 0.3)
    with qtbot.waitSignal(vpage.player.finished, timeout=5000):
        vpage.preview.play_button.click()
    assert vpage.playhead == pytest.approx(total, abs=0.05)


@needs_ffmpeg
def test_loop_with_device_latency(vpage, qtbot, device):
    from umc.videoedit.project import length

    total = length(vpage.history.project)
    vpage.preview.loop = True
    vpage.seek(total - 0.3)
    vpage.preview.play_button.click()
    qtbot.waitUntil(lambda: vpage.player.is_playing and vpage.playhead < 0.6, timeout=5000)
    qtbot.wait(200)
    assert vpage.player.is_playing
    vpage.preview.play_button.click()


@needs_ffmpeg
def test_failing_feeder_still_ends_and_reports(vpage, qtbot, silent, monkeypatch):
    from umc.videoedit import preview
    from umc.videoedit.project import length

    def broken(self):
        raise RuntimeError("sound broke")
        yield b""

    monkeypatch.setattr(preview.PlaybackStreams, "audio", broken)
    errors = []
    vpage.player.failed.connect(errors.append)
    vpage.seek(length(vpage.history.project) - 0.3)
    with qtbot.waitSignal(vpage.player.finished, timeout=5000):
        vpage.preview.play_button.click()
    assert errors


@needs_ffmpeg
def test_playback_failure_is_an_error_message(vpage, qtbot, silent, monkeypatch):
    from umc.videoedit import preview

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(preview, "PlaybackStreams", boom)
    vpage.preview.play_button.click()
    assert vpage.message.objectName() == "Error"
    assert not vpage.preview.playing


@needs_ffmpeg
def test_shutdown_during_scan_is_quick_and_leaves_nothing(qtbot, vmedia, monkeypatch):
    import time

    from umc.gui.videoedit.editor_page import VideoEditorPage
    from umc.videoedit import preview

    started = []

    def slow(*a, **k):
        started.append(1)
        time.sleep(2.0)
        return []

    monkeypatch.setattr(preview, "thumbnails", slow)
    pg = VideoEditorPage()
    qtbot.addWidget(pg)
    assert pg.open_paths([vmedia / "red.mp4"])
    qtbot.waitUntil(lambda: bool(started), timeout=5000)
    tmp = pg.tmp
    t0 = time.monotonic()
    pg.shutdown()
    assert time.monotonic() - t0 < 1.0
    assert not tmp.exists()
    time.sleep(2.2)  # the abandoned scan ends: it must not bring the folder back
    assert not tmp.exists()


@needs_ffmpeg
def test_position_signals_are_throttled(vpage, qtbot, silent):
    seen = []
    vpage.player.position.connect(seen.append)
    vpage.preview.play_button.click()
    qtbot.wait(500)
    vpage.preview.play_button.click()
    assert all(b - a >= 1 / 30 - 1e-6 for a, b in zip(seen, seen[1:], strict=False))


# ------------------------------------------------------------------ Qt sound output


def test_feed_reads_what_was_pushed():
    from umc.gui.videoedit.qt_audio_out import _Feed

    feed = _Feed()
    assert feed.readData(100) == b""  # underrun: nothing, and nothing counted
    data = bytes(range(256)) * 750  # 192000 bytes = 1 s of 48 kHz stereo s16
    feed.push(data[:100000])
    feed.push(data[100000:])
    got = feed.readData(70000) + feed.readData(70000) + feed.readData(1_000_000)
    assert got == data
    assert feed.readData(10) == b""


def test_feed_write_blocks_and_close_wakes_it():
    import threading
    import time

    from umc.gui.videoedit.qt_audio_out import QtAudioOut, _Feed

    feed = _Feed()
    out = QtAudioOut._for_test(feed)
    done = threading.Event()

    def writer():
        out.write(bytes(48000 * 4))  # 1 s: over the 0.5 s limit
        out.write(bytes(4800))  # blocks: nothing drains
        done.set()

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    time.sleep(0.2)
    assert not done.is_set()
    started = time.monotonic()
    out.close()
    assert done.wait(0.2)
    assert time.monotonic() - started < 0.2
    th.join(1)
    out.close()  # idempotent
    out.write(bytes(100))  # returns at once after close


def test_open_qt_output_falls_back_without_device(monkeypatch, qtbot):
    from PySide6.QtMultimedia import QMediaDevices

    from umc.gui.videoedit import qt_audio_out
    from umc.videoedit.audio_out import NullAudioOut

    monkeypatch.setenv("UMC_NO_AUDIO", "1")
    out = qt_audio_out.open_qt_output()
    assert isinstance(out, NullAudioOut) and out.silent
    monkeypatch.delenv("UMC_NO_AUDIO")

    class _Null:
        def isNull(self):  # noqa: N802
            return True

    monkeypatch.setattr(QMediaDevices, "defaultAudioOutput", staticmethod(lambda: _Null()))
    out = qt_audio_out.open_qt_output()
    assert isinstance(out, NullAudioOut) and out.silent


def test_played_seconds_uses_heard_position():
    from umc.gui.videoedit.qt_audio_out import QtAudioOut, _Feed

    class Sink:
        def __init__(self, processed, size, free):
            self._p, self._s, self._f = processed, size, free

        def processedUSecs(self):  # noqa: N802
            return self._p

        def bufferSize(self):  # noqa: N802
            return self._s

        def bytesFree(self):  # noqa: N802
            return self._f

    feed = _Feed()
    out = QtAudioOut._for_test(feed)
    # 1 s processed, 0.25 s (48000 bytes) still in the sink's buffer, 0.1 s in our own queue
    out._sink = Sink(1_000_000, 100000, 52000)
    feed.push(bytes(4800 * 4))
    assert out.played_seconds() == pytest.approx(0.75, abs=1e-6)
    out._sink = Sink(100_000, 100000, 0)  # more queued than processed: never negative
    assert out.played_seconds() == 0.0
    out._sink = None
    assert out.played_seconds() == 0.0


def test_sounddevice_is_gone():
    import re

    from umc.videoedit import audio_out

    root = Path(__file__).resolve().parents[1]
    assert "sounddevice" not in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert not hasattr(audio_out, "SoundDeviceOut")
    assert not re.search(r"import sounddevice", Path(audio_out.__file__).read_text("utf-8"))


def test_sink_enums_match_the_ones_the_module_uses(qtbot):
    from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSink

    from umc.gui.videoedit import qt_audio_out as m

    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    sink = QAudioSink(QAudioDevice(), fmt)
    assert sink.error() == m.QtAudio.Error.NoError
    assert sink.state() in (m.QtAudio.State.StoppedState, m.QtAudio.State.IdleState)
    sink.deleteLater()


def test_feed_signals_ready_after_an_exact_drain(qtbot):
    from umc.gui.videoedit.qt_audio_out import _Feed

    feed = _Feed()
    feed.push(bytes(4000))
    qtbot.wait(20)
    assert len(feed.readData(4000)) == 4000  # drained exactly; no short read follows
    with qtbot.waitSignal(feed.readyRead, timeout=1000):
        feed.push(bytes(400))


def test_sink_error_marks_output_dead_and_frees_write(qtbot):
    import threading
    import time

    from umc.gui.videoedit.qt_audio_out import QtAudio, QtAudioOut, _Feed

    class Sink:
        def error(self):
            return QtAudio.Error.IOError

    feed = _Feed()
    out = QtAudioOut._for_test(feed)
    out._sink = Sink()
    out.write(bytes(48000 * 4))
    done = threading.Event()
    th = threading.Thread(target=lambda: (out.write(bytes(400)), done.set()), daemon=True)
    th.start()
    time.sleep(0.15)
    assert not done.is_set()
    out._on_state(QtAudio.State.StoppedState)
    assert out._dead and done.wait(0.5)
    out._sink = None


def test_nothing_is_queued_after_close():
    from umc.gui.videoedit.qt_audio_out import QtAudioOut, _Feed

    feed = _Feed()
    out = QtAudioOut._for_test(feed)
    out.close()
    feed.push(bytes(400))
    out.write(bytes(400))
    assert feed.queued_bytes() == 0
