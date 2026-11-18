"""The viewer window ("Open with"), moving a file to Convert / Edit, and the single instance."""

import json
import shutil

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtMultimedia")

from umc.viewer import files as vf  # noqa: E402


@pytest.fixture
def folder(media, tmp_path):
    for name in ("img0.png", "three.pdf", "notes.txt", "clip.mp4", "tone.wav", "anim.gif"):
        shutil.copy(media / name, tmp_path / name)
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")  # binary (unknown text shows as text)
    return tmp_path


@pytest.fixture
def viewer(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.viewer.viewer_window import ViewerWindow

    w = ViewerWindow()
    qtbot.addWidget(w)
    # a question never waits for a click in tests: unsaved edits are dropped on closing
    w.ask = lambda text, buttons: "Don't save" if "Don't save" in buttons else buttons[-1]
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


def _shows(qtbot, w, widget, timeout=8000):
    qtbot.waitUntil(lambda: w.stack.currentWidget() is widget, timeout=timeout)


def test_picture_and_folder_list(qtbot, viewer, folder):
    viewer.open_path(folder / "img0.png")
    names = [p.name for p in viewer.items]  # grouped: pictures, videos, music, documents, text
    assert names == ["anim.gif", "img0.png", "clip.mp4", "tone.wav", "three.pdf", "notes.txt"]
    assert viewer.current_index() == names.index("img0.png")
    _shows(qtbot, viewer, viewer.canvas)
    assert viewer.canvas.image is not None and not viewer.canvas.image.isNull()
    assert "Picture" in viewer.detail_label.text() and "2 of 6" in viewer.detail_label.text()
    assert viewer.edit_button.isEnabled() and viewer.convert_button.isEnabled()
    assert viewer.windowTitle().startswith("img0.png")


def test_arrows_go_through_the_folder(qtbot, viewer, folder):
    viewer.open_path(folder / "img0.png")
    viewer.step(1)
    assert viewer.current.name == "clip.mp4"
    viewer.last()
    viewer.step(1)  # stays on the last
    assert viewer.current.name == "notes.txt"
    viewer.first()
    assert viewer.current.name == "anim.gif"
    _shows(qtbot, viewer, viewer.canvas)
    assert viewer.canvas.movie is not None  # animated GIF plays


def test_pdf_pages(qtbot, viewer, folder):
    viewer.open_path(folder / "three.pdf")
    _shows(qtbot, viewer, viewer.pages, timeout=15000)
    assert len(viewer.pages.labels) == 3
    assert "3 pages" in viewer.detail_label.text()
    qtbot.waitUntil(lambda: not viewer.pages.labels[0].pixmap().isNull(), timeout=15000)
    assert not viewer.edit_button.isEnabled()  # the document editor doesn't edit PDFs
    assert viewer.edit_button.toolTip() == "This kind of file can't be edited here"


def test_text(qtbot, viewer, folder):
    viewer.open_path(folder / "notes.txt")
    _shows(qtbot, viewer, viewer.text)
    assert viewer.text.toPlainText().strip()
    assert viewer.edit_button.isEnabled()  # a text file opens in the document editor


def test_video_plays(qtbot, viewer, folder):
    viewer.open_path(folder / "clip.mp4")
    assert viewer.stack.currentWidget() is viewer.media
    frames = []
    viewer.media.video.videoSink().videoFrameChanged.connect(lambda f: frames.append(f))
    qtbot.waitUntil(lambda: viewer.media.player.duration() > 0, timeout=10000)
    qtbot.waitUntil(lambda: len(frames) > 0, timeout=10000)
    assert not viewer.media.error
    player = viewer.media.player
    viewer.step(1)  # leaving it while it plays: it goes on in the mini player
    assert viewer.current.name == "tone.wav" and viewer.media.player is not player
    assert not viewer.media.playback.playing()  # the next song waits while the mini one plays
    assert viewer.mini.playback is not None and viewer.mini.playback.player is player
    assert not viewer.mini.isHidden()
    viewer.mini.stop()
    assert viewer.mini.playback is None and viewer.mini.isHidden()


def test_sound_shows_the_music_screen(qtbot, viewer, folder):
    viewer.open_path(folder / "tone.wav")
    assert viewer.media.screen.currentWidget() is viewer.media.music
    qtbot.waitUntil(lambda: viewer.media.player.duration() > 0, timeout=10000)


def test_unknown_file(qtbot, viewer, folder):
    viewer.open_path(folder / "data.bin")
    assert viewer.kind == vf.OTHER and viewer.stack.currentWidget() is viewer.info
    assert not viewer.edit_button.isEnabled()
    assert "2 B" in viewer.detail_label.text()


def test_convert_and_edit_move_the_file_to_the_main_window(qtbot, folder):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.launcher import AppController

    c = AppController(listen=False)
    c.view(folder / "img0.png")
    qtbot.addWidget(c.viewer)
    c.viewer.convert_button.click()
    assert not c.viewer.isVisible()
    main = c.windows[0]
    qtbot.addWidget(main)
    assert [f.name for f in main.files] == ["img0.png"] and main.stack.currentIndex() == 0

    c.view(folder / "img0.png")
    c.viewer.edit_button.click()
    assert len(c.windows) == 2  # the first window is busy with its file: a new one
    second = c.windows[1]
    qtbot.addWidget(second)
    assert second.stack.currentWidget() is second.editor
    for w in c.windows:
        qtbot.waitUntil(lambda w=w: not w.file_list.thumbnails_pending(), timeout=10000)
        w.close()


def test_a_second_start_hands_its_file_over(tmp_path):
    import uuid

    from PySide6.QtCore import QLockFile

    from tests.instance_window import start_window
    from umc.gui.single_instance import send_to_running

    name = f"umc-test-{uuid.uuid4().hex[:8]}"
    lock = str(tmp_path / "instance.lock")
    window = start_window(name, lock)
    try:
        assert window.stdout.readline().strip() == "ready"
        # a second normal start must not take over (it would cut the first one off)
        assert not QLockFile(lock).tryLock(0)
        assert send_to_running({"view": "/x/y.png"}, name=name)
        assert json.loads(window.stdout.readline()) == {"view": "/x/y.png"}
        assert not send_to_running({"view": "z"}, name=name + "-nobody")
    finally:
        window.wait(20)


def test_runner_drops_results_of_cancelled_and_replaced_jobs(qtbot):
    import threading

    from umc.gui.viewer.tasks import TaskRunner

    runner = TaskRunner()
    gate = threading.Event()
    got = []
    runner.submit("page", lambda: gate.wait(5) and "old page", got.append, exclusive=False)
    runner.submit("main", lambda: gate.wait(5) and "old file", got.append)
    runner.cancel_group("page")  # the user moved on to another document
    runner.submit("main", lambda: "new file", got.append)  # replaces the old file's load
    runner.submit("page", lambda: "new page", got.append, exclusive=False, lane="pdf")
    gate.set()
    qtbot.waitUntil(lambda: sorted(got) == ["new file", "new page"], timeout=5000)
    qtbot.wait(100)
    assert sorted(got) == ["new file", "new page"]
    runner.shutdown()
    assert runner.wait()


def test_switching_pdfs_fast_never_shows_old_pages(qtbot, viewer, folder, media):
    shutil.copy(media / "three.pdf", folder / "again.pdf")
    viewer.open_path(folder / "three.pdf")
    viewer.go_to(viewer.items.index((folder / "again.pdf").resolve()))
    _shows(qtbot, viewer, viewer.pages, timeout=15000)
    calls = []
    real = viewer.pages.set_page
    viewer.pages.set_page = lambda i, img: (calls.append(viewer._pdf_for_pages), real(i, img))
    qtbot.waitUntil(lambda: not viewer.pages.labels[0].pixmap().isNull(), timeout=15000)
    assert calls and all(p.name == "again.pdf" for p in calls)


def test_documents_are_previewed_as_pages(qtbot, viewer, folder):
    (folder / "page.html").write_text("<h1>Hello</h1><p>World</p>", encoding="utf-8")
    viewer.open_path(folder / "page.html")
    assert viewer.kind == vf.CONVERT
    _shows(qtbot, viewer, viewer.pages, timeout=30000)
    assert len(viewer.pages.labels) >= 1


def test_missing_tool_says_convert_can_install_it(folder, monkeypatch):
    from dataclasses import dataclass
    from types import SimpleNamespace

    from umc.core.detect import detect
    from umc.core.errors import ConversionError, ToolMissingError
    from umc.viewer import preview

    @dataclass
    class NoOptions:
        pass

    def run(_job):
        raise ToolMissingError("LibreOffice", "https://example.invalid", "show this file")

    fake = SimpleNamespace(id="doc:pdf", converter=SimpleNamespace(options_cls=NoOptions, run=run))
    monkeypatch.setattr("umc.core.registry.targets_for", lambda files: [fake])
    (folder / "sheet.xlsx").write_bytes(b"PK")
    with pytest.raises(ConversionError) as err:
        preview.preview_pdf(detect(folder / "sheet.xlsx"), folder)
    assert "LibreOffice is needed" in str(err.value)
    assert "Press Convert to install it" in str(err.value)


def test_closed_converter_window_is_reused(qtbot, folder):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.launcher import AppController

    c = AppController(listen=False)
    first = c.show_main()
    qtbot.addWidget(first)
    first.close()
    assert c.show_main() is first and len(c.windows) == 1
    first.close()


# --------------------------------------------------------------------------- grouped list, folders
def _group_titles(w):
    count = w.list.topLevelItemCount()
    return [w.list.topLevelItem(i).text(0).split("  ")[1] for i in range(count)]


def test_side_list_is_grouped_and_folds_are_remembered(qtbot, viewer, folder):
    (folder / "inner").mkdir()
    viewer.open_path(folder / "img0.png")
    assert _group_titles(viewer) == [
        "Folders",
        "Pictures",
        "Videos",
        "Music",
        "Documents",
        "Text & code",
    ]
    pictures = viewer.list.topLevelItem(1)
    assert pictures.childCount() == 2
    pictures.setExpanded(False)
    viewer.go_to(viewer.items.index(folder / "clip.mp4"))
    viewer.step(-1)  # anim.gif / img0.png are folded away: stays on the first open file
    assert viewer.current.name == "clip.mp4"
    from umc.gui.viewer.viewer_window import ViewerWindow

    again = ViewerWindow()  # a new window starts with the same folds
    qtbot.addWidget(again)
    again.open_path(folder / "notes.txt")
    assert not again.list.topLevelItem(1).isExpanded()
    again.close()
    pictures.setExpanded(True)


def test_empty_groups_are_hidden(qtbot, viewer, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    viewer.open_path(tmp_path / "a.txt")
    assert _group_titles(viewer) == ["Text & code"]


def test_folders_up_back_forward(qtbot, viewer, folder, media):
    sub = folder / "inner"
    sub.mkdir()
    shutil.copy(media / "img0.png", sub / "inside.png")
    viewer.open_path(folder / "notes.txt")
    assert not viewer.back_button.isEnabled() and viewer.up_button.isEnabled()
    folders = viewer.list.topLevelItem(0)
    viewer._on_click(folders.child(0), 0)  # a click on "inner" opens it
    assert viewer.location == sub and viewer.current.name == "inside.png"
    assert viewer.address.text() == str(sub)
    viewer.go_up()
    assert viewer.location == folder
    viewer.go_up()  # past where it started
    assert viewer.location == folder.parent
    viewer.go_back()
    viewer.go_back()
    assert viewer.location == sub and viewer.forward_button.isEnabled()
    viewer.go_forward()
    assert viewer.location == folder
    viewer.go_back()
    assert viewer.location == sub


def test_address_line_opens_folders_and_files(qtbot, viewer, folder):
    viewer.open_path(folder / "notes.txt")
    viewer.address.setText(str(folder / "tone.wav"))
    viewer._address_entered()
    assert viewer.location == folder and viewer.current.name == "tone.wav"
    viewer.address.setText(str(folder / "no such place"))
    viewer._address_entered()
    assert not viewer.address_note.isHidden()
    assert viewer.address.text() == str(folder)  # back to where it is
    viewer.address.setText(str(folder.parent))
    viewer._address_entered()
    assert viewer.location == folder.parent and viewer.back_button.isEnabled()


def test_list_follows_the_folder(qtbot, viewer, folder, media):
    viewer.open_path(folder / "notes.txt")
    shutil.copy(media / "img0.png", folder / "new picture.png")
    qtbot.waitUntil(lambda: folder / "new picture.png" in viewer.items, timeout=5000)
    assert viewer.current.name == "notes.txt"  # what's shown stays
    (folder / "notes.txt").unlink()
    qtbot.waitUntil(lambda: folder / "notes.txt" not in viewer.items, timeout=5000)
    assert viewer.current is not None and viewer.current.path.exists()
    (folder / "inner").mkdir()
    qtbot.waitUntil(lambda: "Folders" in _group_titles(viewer), timeout=5000)


# --------------------------------------------------------------------------- player
def test_sliders_jump_where_clicked(qtbot):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QSlider

    from umc.gui.viewer.media import JumpSlider

    s = JumpSlider(Qt.Orientation.Horizontal)
    qtbot.addWidget(s)
    s.setRange(0, 1000)
    s.resize(400, 24)
    s.show()
    moved = []
    s.sliderMoved.connect(moved.append)
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=QPoint(300, 12))
    assert 650 < s.value() < 850 and moved and moved[-1] == s.value()
    plain = QSlider(Qt.Orientation.Horizontal)  # Qt's own only steps a page
    qtbot.addWidget(plain)
    plain.setRange(0, 1000)
    plain.resize(400, 24)
    plain.show()
    qtbot.mouseClick(plain, Qt.MouseButton.LeftButton, pos=QPoint(300, 12))
    assert plain.value() < 650


def test_waveform_click_pauses_and_wheel_sets_volume(qtbot, viewer, folder):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    viewer.open_path(folder / "tone.wav")
    media = viewer.media
    qtbot.waitUntil(lambda: media.wave.levels is not None, timeout=10000)
    assert max(media.wave.levels) == 1.0
    qtbot.waitUntil(lambda: media.playback.playing(), timeout=10000)
    before = media.playback.player.position()
    qtbot.mouseClick(media.wave, Qt.MouseButton.LeftButton, pos=QPoint(media.wave.width() - 30, 60))
    assert not media.playback.playing()
    assert abs(media.playback.player.position() - before) < 500  # paused, didn't jump
    viewer._space()
    assert media.playback.playing()
    start = media.volume.value()
    pos = QPointF(40, 40)
    no_button, no_mod = Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    phase = Qt.ScrollPhase.NoScrollPhase
    glob = media.wave.mapToGlobal(pos)
    event = QWheelEvent(pos, glob, QPoint(), QPoint(0, 240), no_button, no_mod, phase, False)
    QApplication.sendEvent(media.wave, event)
    assert media.volume.value() == min(100, start + 10)
    assert media.playback.volume == media.volume.value()
    assert not media.volume_note.isHidden()
    media.wheel(-30)
    assert media.volume.value() == 0


def test_mini_player_keeps_playing_and_comes_back(qtbot, viewer, folder):
    viewer.open_path(folder / "tone.wav")
    qtbot.waitUntil(lambda: viewer.media.playback.playing(), timeout=10000)
    song = viewer.media.playback
    viewer.open_path(folder / "img0.png")
    assert viewer.mini.playback is song and song.playing()
    viewer.mini.name.click()  # back to it: the same playback, still going
    assert viewer.current.name == "tone.wav" and viewer.media.playback is song
    assert viewer.mini.playback is None and viewer.mini.isHidden()
    song.player.pause()
    viewer.step(1)  # a paused file isn't kept
    assert viewer.mini.playback is None


def test_video_output_is_connected_before_the_file_opens(qtbot, viewer, folder):
    """Opening (or playing) first and connecting the picture after showed a green frame."""
    from umc.gui.viewer.media import Playback

    pb = Playback(folder / "clip.mp4", False, 50, viewer)
    assert pb.player.source().isEmpty()  # nothing opened before a view takes it
    order = []
    pb.player.sourceChanged.connect(lambda _s: order.append("source"))
    pb.player.videoOutputChanged.connect(lambda: order.append("output"))
    viewer.media.attach(pb)
    assert order[:2] == ["output", "source"]
    assert pb.player.videoOutput() is viewer.media.video
    viewer.media.detach()
    pb.close()


# --------------------------------------------------------------------------- quick text edits
def _text_ready(qtbot, w):
    qtbot.waitUntil(
        lambda: w.stack.currentWidget() is w.text and w.text_file is not None, timeout=8000
    )


def test_text_is_edited_and_saved_byte_exact(qtbot, viewer, tmp_path):
    ini = tmp_path / "settings.ini"
    ini.write_bytes("[main]\r\nname=caf\xe9\r\n".encode("cp1252"))  # Windows-1252, CRLF
    viewer.open_path(ini)
    _text_ready(qtbot, viewer)
    assert not viewer.text.isReadOnly() and viewer.save_button.isHidden()
    viewer.text.setPlainText(viewer.text.toPlainText().replace("name", "title"))
    viewer.text.document().setModified(True)
    assert not viewer.save_button.isHidden() and viewer.name_label.text().startswith("• ")
    assert viewer.save_text()
    assert ini.read_bytes() == "[main]\r\ntitle=caf\xe9\r\n".encode("cp1252")
    assert viewer.save_button.isHidden() and viewer.name_label.text() == "settings.ini"


def test_undo_changes_and_the_leave_question(qtbot, viewer, folder):
    notes = folder / "notes.txt"
    before = notes.read_bytes()
    viewer.open_path(notes)
    _text_ready(qtbot, viewer)
    viewer.text.insertPlainText("typed ")
    assert viewer.text_modified()
    viewer.revert_text()
    # the file is written with \r\n on Windows; the viewer shows plain lines (and keeps \r\n)
    assert not viewer.text_modified()
    assert viewer.text.toPlainText() == before.decode().replace("\r\n", "\n")
    viewer.text.insertPlainText("typed ")
    answers = ["Cancel"]
    viewer.ask = lambda text, buttons: answers.pop(0)
    viewer.step(-1)  # Cancel: stays on the edited file
    assert viewer.shown == notes and viewer.text_modified()
    answers.append("Don't save")
    viewer.step(-1)
    assert viewer.shown != notes and notes.read_bytes() == before
    viewer.open_path(notes)
    _text_ready(qtbot, viewer)
    viewer.text.insertPlainText("kept ")
    answers.append("Save")
    viewer.step(-1)
    assert notes.read_text().startswith("kept ")


def test_typing_isnt_taken_by_the_viewers_keys(qtbot, viewer, folder):
    from PySide6.QtCore import Qt

    viewer.open_path(folder / "notes.txt")
    _text_ready(qtbot, viewer)
    viewer.text.setFocus()
    viewer.text.moveCursor(viewer.text.textCursor().MoveOperation.Start)
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Right, Qt.Key.Key_End, Qt.Key.Key_Backspace):
        qtbot.keyClick(viewer.text, key)
    assert viewer.shown == folder / "notes.txt" and viewer.location == folder


def test_big_or_odd_text_stays_read_only(qtbot, viewer, tmp_path, monkeypatch):
    from umc.viewer import textedit

    monkeypatch.setattr(textedit, "EDIT_LIMIT", 10)
    big = tmp_path / "big.log"
    big.write_text("x" * 100 + "\n")
    viewer.open_path(big)
    qtbot.waitUntil(lambda: viewer.stack.currentWidget() is viewer.text, timeout=8000)
    assert viewer.text.isReadOnly() and viewer.text_file is None
    assert "Edit opens it in the code editor" in viewer.detail_label.text()
