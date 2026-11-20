"""The audio editor page: Convert | Edit, editing with the mouse and buttons, saving, leaving."""

from __future__ import annotations

import subprocess
import threading

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
    assert page.edit_buttons["cut"].isEnabled()
    assert page.time_boxes["length"].text().startswith("0:01")
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


def test_typing_selection_times(window, qtbot, media):
    from PySide6.QtCore import Qt

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc, boxes = page.doc, page.time_boxes
    assert page.times.isVisible() and boxes["start"].text() == "0:00.000"
    boxes["end"].setFocus()
    boxes["end"].selectAll()
    qtbot.keyClicks(boxes["end"], "2.5")
    qtbot.keyClick(boxes["end"], Qt.Key.Key_Return)
    assert doc.selection == (0, int(2.5 * doc.rate))
    assert boxes["end"].text() == "0:02.500" and boxes["length"].text() == "0:02.500"
    assert page.type_time("start", "0:01")
    assert doc.selection == (doc.rate, int(2.5 * doc.rate))
    assert page.type_time("length", "0,5")
    assert doc.selection == (doc.rate, int(1.5 * doc.rate))
    # a start past the end moves the end along; a time past the sound stops at its end
    assert page.type_time("start", "2")
    assert doc.selection == (2 * doc.rate, 2 * doc.rate)
    boxes["end"].setFocus()
    boxes["end"].selectAll()
    qtbot.keyClicks(boxes["end"], "9:00")
    qtbot.keyClick(boxes["end"], Qt.Key.Key_Return)
    assert doc.selection == (2 * doc.rate, doc.frames)
    assert boxes["end"].text() == "0:03.000"  # the real end, not what was typed
    # nonsense: a plain hint, the box marked, the selection kept
    before = doc.selection
    box = boxes["start"]
    box.setFocus()
    box.selectAll()
    qtbot.keyClicks(box, "abc")
    qtbot.keyClick(box, Qt.Key.Key_Return)
    assert doc.selection == before and box.property("invalid")
    assert "1:05.250" in page.message.text()
    page._update_state()  # a refresh while typing keeps the refused text
    assert box.text() == "abc"
    # Esc in a box puts the time back and doesn't deselect
    box.setFocus()
    box.selectAll()
    qtbot.keyClicks(box, "1")
    qtbot.keyClick(box, Qt.Key.Key_Escape)
    assert doc.selection == before and box.text() == "0:02.000"
    assert not box.property("invalid")
    # Delete while typing edits the box, not the sound
    frames = doc.frames
    box.setFocus()
    qtbot.keyClick(box, Qt.Key.Key_Delete)
    qtbot.keyClick(box, Qt.Key.Key_Space)
    qtbot.keyClick(box, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)  # Trim
    qtbot.keyClick(box, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)  # Silence
    assert doc.frames == frames and not doc.dirty


def test_speed_pitch_window_changes_the_selection(window, qtbot, media, monkeypatch):
    from umc.gui.audioeditor import audio_editor_page as mod

    page = open_in_editor(window, qtbot, [media / "tone.wav"])  # 3 s
    doc = page.doc
    seen = {}

    def answer(mode, value):
        def exec_(dlg):
            seen["before"] = dlg.result.text()
            dlg.mode.setCurrentIndex([k for k, _l, _a in mod.SPEED_MODES].index(mode))
            dlg.amount.setValue(value)
            seen["after"] = dlg.result.text()
            return True

        monkeypatch.setattr(mod.SpeedDialog, "exec", exec_)

    doc.set_selection(0, doc.rate)  # the first second, at half the tempo
    answer("tempo", 50)
    assert not page.run("speed")  # started; the result arrives later
    assert page.busy and page.cancel_button.isVisible()
    assert not page.edit_buttons["speed"].isEnabled()
    with qtbot.waitSignal(page.change_done, timeout=30_000):
        pass
    assert not page.busy and not page.cancel_button.isVisible()
    assert doc.frames == 4 * doc.rate and doc.selection == (0, 2 * doc.rate)
    assert seen["after"] == "0:01.000 → 0:02.000" and doc.dirty
    assert page.undo_button.toolTip() == "Undo Change tempo (Ctrl+Z)"
    page.undo()
    assert doc.frames == 3 * doc.rate and not doc.dirty
    # 0 semitones changes nothing
    answer("pitch", 0)
    assert not page.run("speed") and not page.busy and doc.frames == 3 * doc.rate
    # too short a part: a plain message
    doc.set_selection(0, 10)
    assert not page.run("speed") and "longer part" in page.message.text()


def test_speed_change_can_be_cancelled(window, qtbot, media, monkeypatch):
    from umc.audioedit import io
    from umc.core.errors import ConversionCancelled
    from umc.gui.audioeditor import audio_editor_page as mod

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    started = threading.Event()

    def slow(samples, rate, mode, value, cancel, progress):
        started.set()
        assert cancel.wait(10)
        raise ConversionCancelled()

    monkeypatch.setattr(mod, "change_speed", slow)
    monkeypatch.setattr(mod.SpeedDialog, "exec", lambda dlg: True)
    monkeypatch.setattr(mod.SpeedDialog, "choice", lambda dlg: ("pitch", 3.0))
    page.run("speed")
    assert started.wait(5)
    with qtbot.waitSignal(page.change_done, timeout=10_000):
        page.cancel_button.click()
    assert "Cancelled" in page.message.text() and not page.doc.dirty
    assert io.change_speed is not slow
    # leaving while a change runs stops it first
    started.clear()
    page.run("speed")
    assert started.wait(5) and page.busy
    assert page.maybe_leave()
    assert not page.busy and not page.doc.dirty


def test_learn_noise_then_reduce_it(window, qtbot, media, monkeypatch):
    import numpy as np

    from umc.gui.audioeditor import audio_editor_page as mod

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    rng = np.random.default_rng(3)
    doc.samples[:, : doc.rate] = 0.02 * rng.standard_normal((doc.channels, doc.rate))
    hiss = float(np.abs(doc.samples[:, : doc.rate]).mean())
    assert not page.edit_buttons["reduce_noise"].isEnabled()  # nothing learned yet
    assert not page.edit_buttons["learn_noise"].isEnabled()  # needs a selection
    doc.set_selection(0, doc.rate)
    page._update_state()
    assert page.run("learn_noise") and "Noise learned from 0:01.000" in page.message.text()
    assert page.edit_buttons["reduce_noise"].isEnabled()
    monkeypatch.setattr(mod.NoiseDialog, "exec", lambda dlg: True)
    monkeypatch.setattr(mod.NoiseDialog, "values", lambda dlg: (24.0, 6.0, 3))
    doc.set_selection(0)  # nothing selected: the whole sound
    assert not page.run("reduce_noise") and page.busy
    with qtbot.waitSignal(page.change_done, timeout=30_000):
        pass
    assert doc.dirty and page.undo_button.toolTip() == "Undo Reduce noise (Ctrl+Z)"
    assert float(np.abs(doc.samples[:, : doc.rate]).mean()) < hiss / 4
    assert doc.selection == (0, doc.frames)
    # too short a noise sample: a plain message
    doc.set_selection(0, 100)
    assert not page.run("learn_noise") and "longer part" in page.message.text()
    page.undo()
    assert not doc.dirty


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
    page.player.set_volume(0)  # no beeps on a developer's PC
    page.toggle_play()
    if page.player.active:  # a sound device exists here
        qtbot.wait(200)
        page.stop()
        assert not page.player.active and page.view.play_head is None
    else:
        assert page.message.text() == NO_DEVICE


@pytest.fixture
def no_saved_volume(window):
    from PySide6.QtCore import QSettings

    from umc.gui.audioeditor.audio_editor_page import VOLUME_KEY

    QSettings().remove(VOLUME_KEY)
    yield VOLUME_KEY
    QSettings().remove(VOLUME_KEY)


def test_volume_slider_sets_the_playback_volume_and_is_remembered(
    window, qtbot, media, no_saved_volume
):
    from PySide6.QtCore import QSettings

    from umc.gui.audioeditor.player import sink_volume

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    assert page.volume_slider.value() == 70
    page.volume_slider.setValue(35)
    assert page.player.volume == pytest.approx(0.35)
    assert page.volume_label.text() == "35 %"
    assert QSettings().value(no_saved_volume, type=int) == 35
    page.volume_slider.setValue(0)  # silent while it plays on a developer's PC
    page.toggle_play()
    if page.player.active:  # a sound device exists here: the sink follows the slider live
        assert page.player.sink.volume() == pytest.approx(0, abs=1e-3)
        page.volume_slider.setValue(1)
        assert page.player.sink.volume() == pytest.approx(sink_volume(0.01), abs=1e-4)
        page.stop()


def test_broken_file_is_a_plain_message(window, qtbot, tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"\x00" * 1000)
    page = window._page("audio")
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_paths([bad])
    assert page.doc is None and page.message.text() and "Traceback" not in page.message.text()


def test_spectrogram_switch_draws_and_is_remembered(window, qtbot, media):
    from PySide6.QtCore import QSettings

    from umc.gui.audioeditor.audio_editor_page import SPECTROGRAM_KEY

    QSettings().remove(SPECTROGRAM_KEY)
    try:
        page = open_in_editor(window, qtbot, [media / "tone.wav"])
        btn = page.edit_buttons["spectrogram"]
        assert btn.isCheckable() and not btn.isChecked() and not page.view.spectrogram
        btn.click()
        assert btn.isChecked() and page.view.spectrogram
        assert QSettings().value(SPECTROGRAM_KEY, type=bool) is True
        page.view.grab()  # paints the lanes
        assert len(page.view._spectro) == page.doc.channels
        assert page.view._spectro[0].height() > 10
        page.view.zoom(2.0)
        page.view.grab()  # quick zooms stretch the last picture, then it's drawn again
        qtbot.waitUntil(lambda: page.view._spectro_key[2] == page.view.fpp, timeout=3000)
        btn.click()
        assert not btn.isChecked() and not page.view.spectrogram
        assert QSettings().value(SPECTROGRAM_KEY, type=bool) is False
    finally:
        QSettings().remove(SPECTROGRAM_KEY)


class FakeRecorder:
    """Stands in for the microphone: the test hands over chunks with ``feed``."""

    def __init__(self, rate: int = 0, channels: int = 0) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            chunk = Signal(object)
            failed = Signal(str)

        self._signals = _Signals()
        self.chunk, self.failed = self._signals.chunk, self._signals.failed
        self.force = (rate, channels)
        self.active, self.rate, self.channels = False, 0, 0

    def __call__(self, _parent=None):  # used as the Recorder class
        return self

    def start(self, rate: int, channels: int) -> str:
        self.rate, self.channels = self.force[0] or rate, self.force[1] or channels
        self.active = True
        return ""

    def stop(self) -> None:
        self.active = False

    def feed(self, seconds: float, value: float = 0.5) -> None:
        import numpy as np

        self.chunk.emit(np.full((self.channels, round(seconds * self.rate)), value, np.float32))


def test_record_explains_or_records(window, qtbot, media):
    from umc.audioedit import record

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    if page.start_recording():  # a microphone exists here
        qtbot.wait(200)
        page.stop_recording()
        assert not page.recording
    else:
        assert page.message.text() == record.NO_INPUT
        assert page.record_button.isEnabled() and not page.busy


def test_record_goes_in_at_the_cursor_as_one_step(window, qtbot, media, monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    rate, frames = doc.rate, doc.frames
    doc.set_selection(rate)
    qtbot.keyClick(page, "r")  # R starts recording
    assert page.recording and page.busy and fake.active
    assert page.record_button.text() == "■ Stop recording" and page.record_level.isVisible()
    assert not page.edit_buttons["cut"].isEnabled() and not page.play_button.isEnabled()
    fake.feed(0.25)
    fake.feed(0.25, 0.75)
    assert page.record_level.value() == 75 and "0:00.500" in page.message.text()
    page.record_button.click()
    assert not page.recording and not fake.active and page.record_button.text() == "● Record"
    assert doc.frames == frames + rate // 2
    assert doc.selection == (rate, rate + rate // 2) and doc.dirty
    assert doc.samples[0, rate] == pytest.approx(0.5)
    assert page.undo_button.toolTip() == "Undo Record (Ctrl+Z)"
    page.undo()
    assert doc.frames == frames and not doc.dirty


@needs_ffmpeg
def test_record_at_another_rate_is_converted(window, qtbot, media, monkeypatch):
    fake = FakeRecorder(rate=8000, channels=1)
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    rate, frames = doc.rate, doc.frames
    doc.set_selection(0, rate)  # replaced by the take
    assert page.start_recording()
    fake.feed(0.5)
    with qtbot.waitSignal(page.change_done, timeout=20000):
        page.stop_recording()
    assert doc.frames == frames - rate + rate // 2
    assert doc.selection == (0, rate // 2)
    assert doc.samples[:, 1000].tolist() == pytest.approx([0.5] * doc.channels, abs=0.01)
    page.undo()
    assert doc.frames == frames and not doc.dirty


def test_record_in_an_empty_editor_makes_a_new_sound(window, qtbot, monkeypatch):
    from umc.audioedit import record

    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    assert window.open_editor("audio")
    page = window.audio_editor
    assert page.doc is None and page.record_button.isEnabled()
    page.record_button.click()
    assert fake.rate == record.NEW_RATE and fake.channels == record.NEW_CHANNELS
    page.stop_recording()  # nothing came yet
    assert page.doc is None and page.message.text() == "Nothing was recorded."
    page.record_button.click()
    fake.feed(1.0)
    page.record_button.click()
    doc = page.doc
    assert doc is not None and doc.rate == record.NEW_RATE and doc.channels == 2
    assert doc.frames == record.NEW_RATE and doc.dirty and page.save_button.isEnabled()
    assert page.center.currentIndex() == 0
    page.undo()


def test_record_stops_when_the_sound_is_full(window, qtbot, media, monkeypatch):
    from umc.audioedit import record

    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    frames = doc.frames
    monkeypatch.setattr(
        "umc.gui.audioeditor.audio_editor_page.MAX_SAMPLES", (frames + 100) * doc.channels
    )
    assert page.start_recording()
    fake.feed(1.0)
    assert not page.recording and page.message.text() == record.FULL
    assert doc.frames == frames + 100
    page.undo()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.MAX_SAMPLES", 10)
    assert not page.start_recording() and page.message.text() == record.NO_ROOM
    assert not fake.active


def test_leaving_while_recording_keeps_the_take(window, qtbot, media, monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    frames = page.doc.frames
    assert page.start_recording()
    fake.feed(0.5)
    monkeypatch.setattr(page, "_ask_save_changes", lambda: "cancel")
    assert not page.maybe_leave()
    assert not page.recording and page.doc.frames == frames + page.doc.rate // 2
    page.undo()


def test_real_recorder_survives_the_device_stopping(qtbot) -> None:
    """The microphone stopping by itself: the last bytes are handed over, then one ``failed``
    (it used to call stop() → read → stop() … forever)."""
    import numpy as np

    from umc.gui.audioeditor import recorder as rec_mod

    class StubIO:
        def __init__(self) -> None:
            self.data = np.array([1000, -1000, 2000, -2000], np.int16).tobytes()

        def readAll(self) -> bytes:  # noqa: N802
            data, self.data = self.data, b""
            return data

    class StubSource:
        def __init__(self) -> None:
            self.stopped = False

        def state(self):
            return rec_mod.STOPPED

        def error(self):
            return rec_mod.NO_ERROR

        def stop(self) -> None:
            self.stopped = True

        def deleteLater(self) -> None:  # noqa: N802
            pass

    r = rec_mod.Recorder()
    source = StubSource()
    r.source, r._io, r.channels, r._float = source, StubIO(), 2, False
    chunks, failures = [], []
    r.chunk.connect(chunks.append)
    r.failed.connect(failures.append)
    r._read()
    assert not r.active and source.stopped
    assert len(chunks) == 1 and chunks[0].shape == (2, 2)
    assert failures == ["The microphone stopped, so the recording ended."]
    r._read()  # the timer firing once more does nothing
    assert len(failures) == 1


def test_take_goes_where_recording_started(window, qtbot, media, monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    doc.set_selection(1000)
    frames = doc.frames
    assert page.start_recording()
    fake.feed(0.1)
    doc.set_selection(5000)  # the cursor moves while recording (clicks aren't blocked)
    page.stop_recording()
    added = doc.frames - frames
    assert added == round(0.1 * doc.rate)
    assert doc.selection == (1000, 1000 + added)
    doc.mark_saved()  # no "save changes?" question when the test ends


def test_last_chunk_filling_the_take_pastes_it_once(window, qtbot, media, monkeypatch):
    from umc.audioedit import record

    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    monkeypatch.setattr(record, "room", lambda *_a: 1000)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    doc = page.doc
    frames = doc.frames
    assert page.start_recording()
    fake.feed(500 / fake.rate)
    stop = fake.stop

    def stop_with_last_chunk() -> None:  # the real recorder hands over what is still waiting
        fake.feed(800 / fake.rate)
        stop()

    fake.stop = stop_with_last_chunk
    page.stop_recording()
    assert doc.frames == frames + 1000 and not page.recording
    doc.mark_saved()


def test_tracks_add_switch_mix_and_save(window, qtbot, media, mono, tmp_path, monkeypatch):
    import numpy as np
    from PySide6.QtWidgets import QFileDialog

    from umc.audioedit import tracks as tr
    from umc.audioedit.io import open_audio

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    first = page.doc
    assert page.tracks_panel.isVisible() and len(page.tracks) == 1 and not page.mixing
    assert page.tracks[0].name == "tone" and not page.dirty
    # a file as a second track: read at the first track's rate, keeps its own channels
    with qtbot.waitSignal(page.opened, timeout=20000):
        assert page.add_track_file(mono)
    assert len(page.tracks) == 2 and page.active_track == 1 and page.mixing
    second = page.doc
    assert second is not first and second.rate == first.rate and second.channels == 1
    assert page.tracks[1].name == "beep" and page.dirty  # a new track is a change
    assert len(page.tracks_panel.row_widgets) == 2
    # edits go to the selected track only, with its own undo
    second.set_selection(0, second.rate // 4)
    assert page.run("delete")
    assert first.can_undo() is False and second.can_undo()
    assert page.select_track(0) and page.doc is first and page.view.doc is first
    assert page.undo_button.isEnabled() is False
    # mixing settings from the strip
    row = page.tracks_panel.row_widgets[1]
    row["gain"].setValue(-6)
    row["pan"].setValue(-100)
    assert page.tracks[1].gain_db == -6.0 and page.tracks[1].pan == -1.0
    assert row["gain_label"].text() == "-6 dB" and row["pan_label"].text() == "L 100"
    assert page._length() == max(first.frames, second.frames)
    assert "mix of 2 tracks" in page.info_label.text()
    # Save writes the mix
    dest = tmp_path / "mix"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "WAV (*.wav)"))
    with qtbot.waitSignal(page.save_done, timeout=20000):
        assert page.save_as()
    assert not page.dirty and "Saved “mix.wav”" in page.message.text()
    saved = open_audio([tmp_path / "mix.wav"])
    want = tr.mix(page.tracks)
    assert saved.samples.shape == want.shape
    assert np.max(np.abs(saved.samples - want)) < 1e-4
    # mute is a change too; solo the first track and only it is heard
    page.tracks_panel.row_widgets[0]["solo"].click()
    assert page.dirty and tr.audible(page.tracks) == [page.tracks[0]]
    page._tracks_saved_rev = page._tracks_rev  # no "save changes?" when the window closes


def test_empty_track_is_recorded_into_and_tracks_are_removed(window, qtbot, media, monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr("umc.gui.audioeditor.audio_editor_page.Recorder", fake)
    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    first = page.doc
    assert page.add_empty_track()
    doc = page.doc
    assert doc.frames == 0 and doc.rate == first.rate and page.tracks[1].name == "Track 2"
    assert page.start_recording()
    fake.feed(0.5)
    page.stop_recording()
    assert doc.frames == first.rate // 2 and doc.undo_label() == "Record"
    # removing a track with sound asks first
    asked = []
    monkeypatch.setattr(page, "confirm", lambda text: asked.append(text) or False)
    assert not page.remove_track(1) and len(page.tracks) == 2
    monkeypatch.setattr(page, "confirm", lambda text: asked.append(text) or True)
    assert page.remove_track(1)
    assert len(asked) == 2 and "Track 2" in asked[0]
    assert page.tracks == [page.tracks[0]] and page.doc is first and page.active_track == 0
    assert page.remove_track(0)  # the last one: back to the empty editor
    assert page.doc is None and page.tracks == [] and page.tracks_panel.isHidden()


def test_a_track_that_does_not_fit_is_refused(window, qtbot, media, mono, monkeypatch):
    from umc.gui.audioeditor import audio_editor_page as mod

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    monkeypatch.setattr(mod, "MAX_SAMPLES", page.doc.samples.size + 10)
    with qtbot.waitSignal(page.opened, timeout=20000):
        assert page.add_track_file(mono)
    assert len(page.tracks) == 1 and page.message.text() == mod.TRACK_NO_ROOM


def test_mix_saves_with_an_empty_track_selected_and_one_track_settings_count(
    window, qtbot, media, tmp_path, monkeypatch
):
    import numpy as np
    from PySide6.QtWidgets import QFileDialog

    from umc.audioedit.io import open_audio

    page = open_in_editor(window, qtbot, [media / "tone.wav"])
    first = page.doc
    # one track at −24 dB: the setting is used (not only shown)
    page.tracks_panel.row_widgets[0]["gain"].setValue(-24)
    assert page.mixing
    dest = tmp_path / "quiet"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "WAV (*.wav)"))
    with qtbot.waitSignal(page.save_done, timeout=20000):
        assert page.save_as()
    quiet = open_audio([tmp_path / "quiet.wav"]).samples
    assert np.max(np.abs(quiet)) < np.max(np.abs(first.samples)) * 0.07
    # an empty track selected: Save still saves the mix
    assert page.add_empty_track() and page.doc.frames == 0
    assert page.save_button.isEnabled()
    dest2 = tmp_path / "mix"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest2), "WAV (*.wav)"))
    with qtbot.waitSignal(page.save_done, timeout=20000):
        assert page.save_as()
    assert (tmp_path / "mix.wav").is_file() and not page.dirty
    # picking a track while busy keeps the strip disabled
    page.saving = True
    assert not page.select_track(0)
    assert not page.tracks_panel.row_widgets[0]["name"].isEnabled()
    page.saving = False
