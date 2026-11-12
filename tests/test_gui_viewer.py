"""The viewer window ("Open with"), moving a file to Convert / Edit, and the single instance."""

import shutil

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtMultimedia")

from umc.viewer import files as vf  # noqa: E402


@pytest.fixture
def folder(media, tmp_path):
    for name in ("img0.png", "three.pdf", "notes.txt", "clip.mp4", "tone.wav", "anim.gif"):
        shutil.copy(media / name, tmp_path / name)
    (tmp_path / "data.bin").write_bytes(b"xx")
    return tmp_path


@pytest.fixture
def viewer(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.viewer.viewer_window import ViewerWindow

    w = ViewerWindow()
    qtbot.addWidget(w)
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


def _shows(qtbot, w, widget, timeout=8000):
    qtbot.waitUntil(lambda: w.stack.currentWidget() is widget, timeout=timeout)


def test_picture_and_folder_list(qtbot, viewer, folder):
    viewer.open_path(folder / "img0.png")
    names = [p.name for p in viewer.items]
    assert names == ["anim.gif", "clip.mp4", "img0.png", "notes.txt", "three.pdf", "tone.wav"]
    assert viewer.list.currentRow() == names.index("img0.png")
    _shows(qtbot, viewer, viewer.canvas)
    assert viewer.canvas.image is not None and not viewer.canvas.image.isNull()
    assert "Picture" in viewer.detail_label.text() and "3 of 6" in viewer.detail_label.text()
    assert viewer.edit_button.isEnabled() and viewer.convert_button.isEnabled()
    assert viewer.windowTitle().startswith("img0.png")


def test_arrows_go_through_the_folder(qtbot, viewer, folder):
    viewer.open_path(folder / "img0.png")
    viewer.step(1)
    assert viewer.current.name == "notes.txt"
    viewer.go_to(len(viewer.items) - 1)
    viewer.step(1)  # stays on the last
    assert viewer.current.name == "tone.wav"
    viewer.go_to(0)
    assert viewer.current.name == "anim.gif"
    _shows(qtbot, viewer, viewer.canvas)
    assert viewer.canvas.movie is not None  # animated GIF plays


def test_pdf_pages(qtbot, viewer, folder):
    viewer.open_path(folder / "three.pdf")
    _shows(qtbot, viewer, viewer.pages, timeout=15000)
    assert len(viewer.pages.labels) == 3
    assert "3 pages" in viewer.detail_label.text()
    qtbot.waitUntil(lambda: not viewer.pages.labels[0].pixmap().isNull(), timeout=15000)
    assert not viewer.edit_button.isEnabled()  # the document editor is "coming soon"


def test_text(qtbot, viewer, folder):
    viewer.open_path(folder / "notes.txt")
    _shows(qtbot, viewer, viewer.text)
    assert viewer.text.toPlainText().strip()


def test_video_plays(qtbot, viewer, folder):
    viewer.open_path(folder / "clip.mp4")
    assert viewer.stack.currentWidget() is viewer.media
    frames = []
    viewer.media.video.videoSink().videoFrameChanged.connect(lambda f: frames.append(f))
    qtbot.waitUntil(lambda: viewer.media.player.duration() > 0, timeout=10000)
    qtbot.waitUntil(lambda: len(frames) > 0, timeout=10000)
    assert not viewer.media.error
    viewer.step(1)  # leaving stops it
    assert viewer.media.player.source().isEmpty()


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
        w.close()


def test_a_second_start_hands_its_file_over(qtbot, tmp_path):
    import uuid

    from umc.gui.single_instance import InstanceServer, send_to_running

    name = f"umc-test-{uuid.uuid4().hex[:8]}"
    lock = str(tmp_path / "instance.lock")
    first = InstanceServer(name=name, lock_path=lock)
    assert first.start()
    got = []
    first.message_received.connect(got.append)
    # a second normal start must not take over (it would cut the first one off)
    second = InstanceServer(name=name, lock_path=lock)
    assert not second.start()
    # sent from another thread, like another program would: on Windows a named pipe written
    # from the thread that also serves it can't finish (that thread is blocked writing)
    import threading

    sent = []
    t = threading.Thread(
        target=lambda: sent.append(send_to_running({"view": "/x/y.png"}, name=name))
    )
    t.start()
    qtbot.waitUntil(lambda: got == [{"view": "/x/y.png"}], timeout=5000)
    t.join(5)
    assert sent == [True]
    assert not send_to_running({"view": "z"}, name=name + "-nobody")


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
