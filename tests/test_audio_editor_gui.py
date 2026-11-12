"""The audio editor page: Convert | Edit, editing with the mouse and buttons, saving, leaving."""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import needs_ffmpeg

pytest.importorskip("pytestqt")
pytestmark = needs_ffmpeg


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
    if w.audio_editor is not None:
        w.audio_editor.player.stop()
        w.audio_editor.doc = None  # nothing to ask about when the test window closes


@pytest.fixture
def mono(tmp_path):
    p = tmp_path / "beep.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=660:duration=1", str(p)], check=True)  # fmt: skip
    return p


def open_in_editor(window, qtbot, paths):
    window.add_files([str(p) for p in paths])
    assert window.edit_mode.isEnabled() and window.edit_kind() == "audio"
    with qtbot.waitSignal(window._page("audio").opened, timeout=20000):
        window.enter_edit()
    page = window.audio_editor
    assert window.stack.currentWidget() is page and page.doc is not None
    return page


def drag(qtbot, view, x0, x1):
    from PySide6.QtCore import QPoint, Qt

    y = view.height() // 2
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(x0, y))
    qtbot.mouseMove(view, QPoint(x1, y))
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(x1, y))


def test_edit_joins_files_and_edits_with_the_mouse(window, qtbot, media, mono):
    page = open_in_editor(window, qtbot, [media / "tone.wav", mono])
    doc = page.doc
    assert doc.channels == 2 and 3.9 < doc.duration < 4.1  # 3 s + 1 s, joined
    assert "tone.wav + 1 more" in page.title.text()
    view = page.view
    w = view.width()
    drag(qtbot, view, w // 4, w // 2)
    a, b = doc.selection
    assert 0.9 < (b - a) / doc.rate < 1.1
    assert page.edit_buttons["cut"].isEnabled() and "Length" in page.selection_label.text()
    page.edit_buttons["cut"].click()
    assert 2.9 < doc.duration < 3.1 and doc.dirty and page.undo_button.isEnabled()
    assert "Unsaved changes" in page.info_label.text()
    assert page.edit_buttons["paste"].isEnabled()
    page.run("paste")
    assert 3.9 < doc.duration < 4.1
    page.undo()
    page.undo()
    assert not doc.dirty
    # zoom and paint at every level without errors
    page.run("zoom_sel")
    for _ in range(14):
        page.run("zoom_in")
        view.grab()
    page.run("fit")
    view.grab()
    assert view.fpp == pytest.approx(doc.frames / view.width())


def test_effects_and_save_as(window, qtbot, media, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from umc.audioedit.io import open_audio

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    doc.set_selection(0, doc.rate)
    for key in ("fade_in", "fade_out", "trim"):
        assert page.run(key), key
    assert 0.99 < doc.duration < 1.01
    doc.set_selection(0)
    assert page.run("reverse") and page.run("normalize")
    doc.set_selection(0, doc.rate // 2)
    assert page.run("silence") and page.run("delete")
    dest = tmp_path / "out"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "FLAC (*.flac)")
    )
    with qtbot.waitSignal(page.save_done, timeout=20000):
        assert page.save()  # first Save asks for a name
    assert (tmp_path / "out.flac").is_file() and not doc.dirty
    assert abs(open_audio([tmp_path / "out.flac"]).duration - 0.5) < 0.02
    assert page._save_target == (tmp_path / "out.flac", "flac")


def test_leaving_asks_about_unsaved_changes(window, qtbot, media, monkeypatch):
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    page.doc.select_all()
    page.run("delete")
    answers = iter(["cancel", "discard"])
    monkeypatch.setattr(page, "_ask_save_changes", lambda: next(answers))
    window.leave_edit()
    assert window.stack.currentWidget() is page  # cancelled
    window.leave_edit()
    assert window.stack.currentIndex() == 0
    # back to Edit: the files are opened again, no second question
    with qtbot.waitSignal(page.opened, timeout=20000):
        window.enter_edit()
    assert page.doc.duration > 2.9 and not page.doc.dirty


def test_tile_opens_an_empty_editor_and_drops_open_files(window, qtbot, media):
    assert window.open_editor("audio")
    page = window.audio_editor
    assert window.stack.currentWidget() is page and page.doc is None
    assert page.center.currentWidget() is page.placeholder
    assert not page.save_button.isEnabled()
    page.drop_paths([media / "img0.png"])
    assert "Only audio" in page.message.text()
    with qtbot.waitSignal(page.opened, timeout=20000):
        window.handle_drop([str(media / "tone.wav")])
    assert page.doc is not None and page.center.currentIndex() == 0


def test_play_starts_or_explains(window, qtbot, media):
    from umc.gui.audioeditor.player import NO_DEVICE

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    page.toggle_play()
    if page.player.active:  # a sound device exists here
        qtbot.wait(200)
        page.stop()
        assert not page.player.active and page.view.play_head is None
    else:
        assert page.message.text() == NO_DEVICE


def test_broken_file_is_a_plain_message(window, qtbot, tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"\x00" * 1000)
    page = window._page("audio")
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_paths([bad])
    assert page.doc is None and page.message.text() and "Traceback" not in page.message.text()
