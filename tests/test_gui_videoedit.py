"""Video editor GUI (offscreen): the timeline widget."""

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402

from umc.videoedit.project import (  # noqa: E402
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    VideoClip,
    clip_duration,
    layout,
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


def test_layout_unchanged_by_widget(make):
    p = _project()
    t = make(p)
    assert layout(t.project) == layout(p)
    assert PictureClip  # imported for completeness
