"""Right-click "Quick Convert": what each file type's menu offers, and the small window that
converts with the default settings next to the original."""

import shutil
from pathlib import Path

import pytest

from umc.core import quickconvert as qc


def _ids(ext):
    return [i.target_id for i in qc.menu_for(ext).items]


def test_menus_offer_only_what_fits_the_file():
    png = _ids("png")
    assert "jpg" in png and "webp" in png and "images:pdf" in png and "ocr:pdf" in png
    assert "png" not in png  # not to its own format
    assert not [i for i in png if i.startswith(("pack:", "slideshow:"))]
    mp4 = _ids("mp4")
    assert "audio:mp3" in mp4 and "video:gif" in mp4 and "mkv" in mp4 and "mp4" not in mp4
    mp3 = _ids("mp3")
    assert "wav" in mp3 and "mp3" not in mp3 and "jpg" not in mp3
    pdf = _ids("pdf")
    assert "doc:docx" in pdf and "pdf:compress" in pdf and "ocr:pdf" in pdf
    for asks_something in ("pdf:protect", "pdf:unlock", "pdf:rotate", "pdf:organize", "split:pdf"):
        assert asks_something not in pdf
    assert "doc:pdf" not in pdf  # PDF → PDF
    assert _ids("zip")[0] == "archive:extract"
    assert qc.menu_for("nonsense").items == ()


def test_same_menus_share_a_key_and_long_ones_nest():
    assert qc.menu_for("jpg").key == qc.menu_for("jpeg").key
    assert qc.menu_for("png").key != qc.menu_for("jpg").key
    mp3 = qc.menu_for("mp3")
    assert not mp3.nested and all(not g for g, _ in mp3.entries())
    mp4 = qc.menu_for("mp4")
    assert mp4.nested
    entries = dict(mp4.entries())
    assert [i.label for i in entries["Audio only"]][:2] == ["MP3", "M4A (AAC)"]
    assert "Animation" not in entries  # one item: shown in the menu itself
    flat = dict((i.target_id, label) for i, label in mp4.flat_labels())
    assert flat["audio:mp3"] == "Audio only: MP3" and flat["video:gif"] == "GIF"


def test_menus_for_every_extension():
    from umc.tools.shell_integration import convert_extensions

    table = qc.menus(convert_extensions())
    assert "png" in table and "docx" in table and "stl" in table and "srt" in table
    assert all(m.items for m in table.values())


def test_job_writes_next_to_the_file_with_defaults(media, tmp_path):
    src = tmp_path / "photo.png"
    shutil.copy(media / "img0.png", src)
    job = qc.job_for(src, "jpg")
    assert job.output_dir is None and job.files[0].path == src
    out = job.target.converter.run(job)
    assert [Path(p).name for p in out] == ["photo.jpg"]
    again = qc.job_for(src, "jpg").target
    out2 = again.converter.run(qc.job_for(src, "jpg"))
    assert [Path(p).name for p in out2] == ["photo (1).jpg"]  # never overwritten


def test_job_problems_are_plain(media, tmp_path):
    with pytest.raises(qc.QuickConvertError, match="isn't there"):
        qc.job_for(tmp_path / "gone.png", "jpg")
    song = tmp_path / "x.png"
    shutil.copy(media / "img0.png", song)
    with pytest.raises(qc.QuickConvertError, match="can't be converted to that format"):
        qc.job_for(song, "audio:mp3")
    assert qc.label_of("jpg", song) == "JPG"


pytest.importorskip("pytestqt")


def test_window_converts_everything_and_closes_by_itself(qtbot, media, tmp_path):
    from umc.gui import quick_convert

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    shutil.copy(media / "img0.png", a)
    shutil.copy(media / "img0.png", b)
    w = quick_convert.QuickConvertWindow()
    qtbot.addWidget(w)
    w._close_timer.setInterval(50)
    w.show()
    w.add("jpg", [str(a)])
    w.add("jpg", [str(b), str(a)])  # Explorer's second start; "a" is already queued
    assert len(w.items) == 2
    qtbot.waitUntil(lambda: w.done, timeout=20000)
    assert (tmp_path / "a.jpg").is_file() and (tmp_path / "b.jpg").is_file()
    assert w.title.text() == "Done" and "2 files saved next to the originals" in w.line.text()
    assert not w.show_button.isHidden() and w.open_button.isHidden()
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(w, QEnterEvent(w.rect().center(), w.rect().center(), w.rect().center()))
    assert not w._close_timer.isActive()  # the pointer is over it: someone is reading
    QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))
    qtbot.waitUntil(lambda: not w.isVisible(), timeout=3000)


def test_window_lists_problems_and_offers_the_full_app(qtbot, media, tmp_path):
    from umc.gui import quick_convert

    good = tmp_path / "good.png"
    shutil.copy(media / "img0.png", good)
    w = quick_convert.QuickConvertWindow()
    qtbot.addWidget(w)
    w.show()
    opened = []
    w.open_in_app.connect(opened.append)
    w.add("audio:mp3", [str(good), str(tmp_path / "gone.png")])
    qtbot.waitUntil(lambda: w.done, timeout=10000)
    assert w.title.text() == "Nothing was converted"
    assert "good.png: This file can't be converted to that format." in w.problems.text()
    assert "isn't there any more" in w.problems.text()
    assert not w.open_button.isHidden()
    w.open_button.click()
    assert opened == [[str(good)]]


def test_controller_sends_every_start_to_one_window(qtbot, media, tmp_path):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.launcher import AppController

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    shutil.copy(media / "img0.png", a)
    shutil.copy(media / "img0.png", b)
    c = AppController(listen=False)
    c._on_message({"quick": {"target": "webp", "paths": [str(a)]}})
    qtbot.addWidget(c.quick)
    c._on_message({"quick": {"target": "webp", "paths": [str(b)]}})
    c._on_message({"quick": {"target": 5, "paths": "nonsense"}})  # ignored
    assert len(c.quick.items) == 2
    qtbot.waitUntil(lambda: c.quick.done, timeout=20000)
    assert (tmp_path / "a.webp").is_file() and (tmp_path / "b.webp").is_file()
    c.quick_convert("jpg", [str(a)])  # a new round starts with an empty list
    assert len(c.quick.items) == 1
    qtbot.waitUntil(lambda: c.quick.done, timeout=20000)
    c.quick.close()


def test_format_picker_for_file_managers_without_submenus(qtbot, media, tmp_path):
    from PySide6.QtWidgets import QPushButton

    from umc.gui import quick_convert

    a, b = tmp_path / "a.png", tmp_path / "b.jpg"
    shutil.copy(media / "img0.png", a)
    shutil.copy(media / "img0.png", b)
    menu = qc.menu_for_files([a, b])
    ids = [i.target_id for i in menu.items]
    assert "webp" in ids and "jpg" not in ids and "png" not in ids  # what both can become
    w = quick_convert.QuickConvertWindow()
    qtbot.addWidget(w)
    w._hovered = True
    w.show()
    w.choose([str(a), str(b)])
    assert w.title.text() == "Convert to…" and not w.picker.isHidden()
    webp = next(x for x in w.picker.findChildren(QPushButton) if x.text() == "WEBP")
    webp.click()
    qtbot.waitUntil(lambda: w.done, timeout=20000)
    assert (tmp_path / "a.webp").is_file() and (tmp_path / "b.webp").is_file()


def test_cancelled_files_are_not_converted_later(qtbot, media, tmp_path):
    from umc.gui import quick_convert

    w = quick_convert.QuickConvertWindow()
    qtbot.addWidget(w)
    w._hovered = True
    a, b, c = (tmp_path / f"{n}.png" for n in "abc")
    for p in (a, b, c):
        shutil.copy(media / "img0.png", p)
    w.add("jpg", [str(a), str(b)])
    w.cancel()  # while "a" converts: "b" stays undone
    qtbot.waitUntil(lambda: not w.busy, timeout=20000)
    w.add("jpg", [str(c)])  # the next right-click
    qtbot.waitUntil(lambda: w.done, timeout=20000)
    assert (tmp_path / "c.jpg").is_file() and not (tmp_path / "b.jpg").exists()
