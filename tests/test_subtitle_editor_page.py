"""The desktop subtitle editor page: opening, editing, undo, find, shift / sync, saving."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from umc.gui.subtitleeditor.subtitle_editor_page import SubtitleEditorPage  # noqa: E402

SRT = (
    "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello there\r\n\r\n"
    "2\r\n00:00:03,000 --> 00:00:04,000\r\nSecond line\r\n\r\n"
    "3\r\n00:00:05,000 --> 00:00:06,000\r\nThird one\r\n"
)


@pytest.fixture
def page(qtbot):
    p = SubtitleEditorPage()
    qtbot.addWidget(p)
    p.resize(1000, 650)
    p.show()
    p.questions = []
    p.answers = []

    def ask(title: str, text: str, buttons: tuple[str, ...]) -> str:
        p.questions.append(text)
        return p.answers.pop(0)

    p.ask = ask
    p.save_paths = []
    p.choose_save_path = lambda suggested, fmts: p.save_paths.pop(0)
    yield p
    qtbot.waitUntil(lambda: not p.busy, timeout=10000)
    p.history.mark_saved()  # nothing to ask about when the window closes


def _open(qtbot, page, path: Path, video: Path | None = None) -> None:
    assert page.open_paths([path], video=video)
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)


def _save(qtbot, page, as_: bool = False) -> bool:
    started = page.save_as() if as_ else page.save()
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    return started


def _srt(tmp_path: Path, text: str = SRT, name: str = "film.srt") -> Path:
    f = tmp_path / name
    f.write_bytes(text.encode("utf-8"))
    return f


def test_open_shows_rows_and_untouched_save_keeps_bytes(qtbot, page, tmp_path):
    f = _srt(tmp_path)
    _open(qtbot, page, f)
    assert page.model.rowCount() == 3
    assert page.model.data(page.model.index(1, 1)) == "0:00:03.000"
    assert page.model.data(page.model.index(1, 4)) == "Second line"
    assert page._row == 0 and page.text_box.toPlainText() == "Hello there"
    assert not page.dirty and "film.srt" in page.title.text()
    assert _save(qtbot, page)
    assert f.read_bytes() == SRT.encode()


def test_typing_is_one_undo_step_and_saves_with_crlf(qtbot, page, tmp_path):
    f = _srt(tmp_path)
    _open(qtbot, page, f)
    page.text_box.setFocus()
    cursor = page.text_box.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    page.text_box.setTextCursor(cursor)
    qtbot.keyClicks(page.text_box, "!!")
    assert page.doc.cues[0].text == "Hello there!!" and page.dirty
    assert "●" in page.title.text()
    page.undo()
    assert page.doc.cues[0].text == "Hello there" and not page.dirty
    assert page.text_box.toPlainText() == "Hello there"
    page.redo()
    assert _save(qtbot, page)
    assert f.read_bytes() == SRT.replace("Hello there", "Hello there!!").encode()
    assert not page.dirty


def test_times_add_delete_split_join_sort(qtbot, page, tmp_path):
    _open(qtbot, page, _srt(tmp_path))
    page.select_row(1)
    page._typed_time("start", "0:10")
    assert (page.doc.cues[1].start, page.doc.cues[1].end) == (10_000, 10_000)
    assert page.sort_button.isVisibleTo(page)  # now out of order
    page._typed_time("length", "1.5")
    assert page.doc.cues[1].end == 11_500
    page._typed_time("end", "nonsense")
    assert "1:05.250" in page.message.text()
    page.sort()
    assert [c.text for c in page.doc.cues] == ["Hello there", "Third one", "Second line"]
    assert page._row == 2 and not page.sort_button.isVisibleTo(page)
    page.select_row(0)
    page.add_cue()
    assert page.model.rowCount() == 4 and page._row == 1 and page.doc.cues[1].start == 2000
    page.delete()
    assert page.model.rowCount() == 3
    page.select_row(0)
    page.split()
    assert [c.text for c in page.doc.cues[:2]] == ["Hello", "there"]
    assert page.chosen_rows() == [0, 1]
    page.join()
    assert page.doc.cues[0].text == "Hello\nthere" and page.model.rowCount() == 3
    for _ in range(7):  # start, length, sort, add, delete, split, join
        page.undo()
    assert page.doc == page.opened.doc and not page.dirty


def test_shift_and_sync(qtbot, page, tmp_path):
    _open(qtbot, page, _srt(tmp_path))
    page.shift_times(-500, chosen_only=False)
    assert [c.start for c in page.doc.cues] == [500, 2500, 4500]
    page.select_row(2)
    page.shift_times(1000, chosen_only=True)
    assert [c.start for c in page.doc.cues] == [500, 2500, 5500]
    assert page.sync_times(0, 1000, 2, 11_000)
    assert [c.start for c in page.doc.cues] == [1000, 5000, 11_000]
    assert not page.sync_times(0, 5000, 2, 1000)
    assert "after the first" in page.message.text()


def test_find_and_replace(qtbot, page, tmp_path):
    _open(qtbot, page, _srt(tmp_path))
    page.find_bar.open(True)
    page.find_bar.query.setText("line")
    assert page.find_bar.count.text() == "1 found"
    assert page.find(1)
    assert page._row == 1 and page.text_box.textCursor().selectedText() == "line"
    page.find_bar.replacement.setText("row")
    page.replace_one()
    assert page.doc.cues[1].text == "Second row"
    page.find_bar.query.setText("e")
    page.find_bar.replacement.setText("E")
    page.replace_all()
    assert page.doc.cues[0].text == "HEllo thErE"
    page.find_bar.regex.setChecked(True)
    page.find_bar.query.setText("(")
    assert "isn't valid" in page.find_bar.count.text()


def test_problems_are_marked_and_next_problem_jumps(qtbot, page, tmp_path):
    text = (
        "1\n00:00:01,000 --> 00:00:01,200\nFlash\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\nFine\n\n"
        "3\n00:00:04,500 --> 00:00:07,000\nOverlaps\n"
    )
    _open(qtbot, page, _srt(tmp_path, text))
    page.run_check()
    assert page.problem_button.text() == "2 with problems"
    assert "Shown for only 0.2 s" in page.model.data(page.model.index(0, 0), 3)  # tooltip
    page.next_problem()
    assert page._row == 2 and "Starts before line 2 ends" in page.note.text()
    page.next_problem()
    assert page._row == 0


def test_save_as_another_format_asks_about_lost_parts(qtbot, page, tmp_path):
    ass = tmp_path / "styled.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Top,,0,0,0,,{\\i1}Hi{\\i0}\\Nthere\n",
        encoding="utf-8",
    )
    _open(qtbot, page, ass)
    out = tmp_path / "plain.srt"
    page.save_paths = [out, out]
    page.answers = ["no"]
    assert not page.save_as()
    assert "styles, positions and effects" in page.questions[-1]
    page.answers = ["yes"]
    assert _save(qtbot, page, as_=True)
    assert out.read_text() == "1\n00:00:01,000 --> 00:00:02,000\n<i>Hi</i>\nthere\n"
    assert page.doc.format == "srt" and page.path == out and not page.dirty


def test_smi_saves_through_save_as_and_new_document_asks_for_a_name(qtbot, page, tmp_path):
    smi = tmp_path / "old.smi"
    smi.write_text("<SAMI><BODY><SYNC Start=1000><P>Hi\n<SYNC Start=2000><P>&nbsp;</BODY>")
    _open(qtbot, page, smi)
    assert page.path is None and "SAMI" in page.message.text()
    page.save_paths = [tmp_path / "old.srt"]
    assert _save(qtbot, page)
    assert (tmp_path / "old.srt").read_text() == "1\n00:00:01,000 --> 00:00:02,000\nHi\n"
    assert page.open_empty()
    assert page.model.rowCount() == 0 and page.path is None
    page.add_cue()
    page.text_box.setPlainText("first")
    assert page.doc.cues[0].text == "first"
    page.save_paths = [None]
    assert not page.save()  # cancelled
    page.answers = ["cancel"]
    assert not page.maybe_leave()
    page.answers = ["discard"]
    assert page.maybe_leave()


def test_reopen_with_charset(qtbot, page, tmp_path):
    f = tmp_path / "ru.srt"
    f.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nПривет\n".encode("windows-1251"))
    _open(qtbot, page, f)
    assert page.doc.cues[0].text != "Привет"
    page.reopen("windows-1251")
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    assert page.doc.cues[0].text == "Привет" and "UTF-8" in page.message.text()
    assert _save(qtbot, page)
    assert f.read_bytes() == "1\n00:00:01,000 --> 00:00:02,000\nПривет\n".encode()


def test_bad_file_shows_a_plain_message(qtbot, page, tmp_path):
    f = tmp_path / "broken.vtt"
    f.write_text("not a vtt\n")
    _open(qtbot, page, f)
    assert "WEBVTT" in page.message.text() and page.model.rowCount() == 0
    assert not page.open_paths([tmp_path / "notes.txt"])


def test_video_preview_shows_the_cue_and_takes_times(qtbot, page, tmp_path, media):
    clip = media / "clip.mp4"
    if not clip.exists():
        pytest.skip("ffmpeg not installed")
    _open(qtbot, page, _srt(tmp_path), video=clip)
    assert page.preview.has_video and page.preview.isVisibleTo(page)
    assert page.video_button.text() == "Change video…"
    page.preview.seek(1500)
    assert page.model.playing == {0} and "Hello there" in page.preview.caption_text
    page.preview.seek(2500)
    assert page.preview.caption_text == ""
    page.select_row(1)
    page.preview.position = lambda: 3200  # the clip is only 2 s long
    page.set_from_video("end")
    assert page.doc.cues[1].end == 3200
    page.preview.close_video()
    assert not page.preview.has_video and page.preview.isHidden()
    assert not page.set_start_button.isEnabled()
