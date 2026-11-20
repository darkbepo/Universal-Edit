"""Which editor Edit opens for subtitle files (alone or with a video), and the hub tile."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHi\n"


@pytest.fixture
def window(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1280, 800)
    w.show()
    yield w
    for page in (w.subtitle_editor, w.code_editor):
        if page is not None:
            page.wait_idle()
    if w.subtitle_editor is not None:
        w.subtitle_editor.history.mark_saved()


def test_one_subtitle_file_opens_in_the_subtitle_editor(qtbot, window, tmp_path):
    f = tmp_path / "a.srt"
    f.write_text(SRT, encoding="utf-8")
    window.add_files([str(f)])
    assert window.edit_kind() == "subtitle"
    assert window.edit_choices() == ["subtitle", "code"]  # ▾ offers the code editor too
    assert not window.edit_more.isHidden()
    window.enter_edit()
    page = window.subtitle_editor
    assert window.stack.currentWidget() is page
    qtbot.waitUntil(lambda: not page.busy and page.model.rowCount() == 1, timeout=10000)
    assert page.source == f and not page.preview.has_video


def test_a_subtitle_with_its_video_opens_both(qtbot, window, tmp_path, media):
    clip = media / "clip.mp4"
    if not clip.exists():
        pytest.skip("ffmpeg not installed")
    f = tmp_path / "clip.srt"
    f.write_text(SRT, encoding="utf-8")
    window.add_files([str(clip), str(f)])
    assert window.edit_choices() == ["subtitle"]
    window.enter_edit()
    page = window.subtitle_editor
    qtbot.waitUntil(lambda: not page.busy and page.model.rowCount() == 1, timeout=10000)
    assert page.preview.has_video and page.preview.path == clip


def test_two_subtitle_files_go_to_the_code_editor(window, tmp_path):
    paths = []
    for name in ("a.srt", "b.vtt"):
        (tmp_path / name).write_text(SRT, encoding="utf-8")
        paths.append(str(tmp_path / name))
    window.add_files(paths)
    assert window.edit_choices() == ["code"]


def test_the_hub_tile_opens_an_empty_editor(window):
    assert window.open_editor("subtitle")
    page = window.subtitle_editor
    assert window.stack.currentWidget() is page
    assert page.model.rowCount() == 0 and page.path is None


def test_the_viewer_sends_subtitles_to_the_subtitle_editor(tmp_path):
    from umc.viewer import files as vf

    f = tmp_path / "a.vtt"
    f.write_text("WEBVTT\n", encoding="utf-8")
    kind = vf.kind_of_path(f)
    assert vf.editor_for(kind, f).id == "subtitle" and vf.can_edit(kind, f)
