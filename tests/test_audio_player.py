"""The audio editor's player: the enum values Qt really reports, and the volume."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtMultimedia")


def _format():
    from PySide6.QtMultimedia import QAudioFormat

    fmt = QAudioFormat()
    fmt.setSampleRate(44100)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


def test_sink_reports_the_constants_the_player_compares_with(qapp):
    # Qt 6.12 moved error() / state() to the QtAudio enums, which never equal the old QAudio
    # ones: playback stopped at once and the end of a sound went unnoticed. If Qt changes the
    # enums again, this fails instead of the player going quiet.
    from PySide6.QtMultimedia import QAudioDevice, QAudioSink

    from umc.gui.audioeditor import player

    sink = QAudioSink(QAudioDevice(), _format())
    assert sink.error() == player.NO_ERROR
    assert sink.state() == player.STOPPED
    assert type(sink.state()) is type(player.IDLE)


def test_sink_volume_follows_the_ear():
    from umc.gui.audioeditor.player import sink_volume

    assert sink_volume(0) == 0
    assert sink_volume(1) == pytest.approx(1)
    assert sink_volume(-1) == 0 and sink_volume(2) == pytest.approx(1)
    steps = [sink_volume(i / 10) for i in range(11)]
    assert steps == sorted(steps)
    assert sink_volume(0.5) < 0.5  # half way sounds half as loud, which is well under half power
    # the website's gainFor (web/src/audioedit/volume.ts) checks these same numbers
    for v, g in ((0.01, 0.0021824), (0.25, 0.0624694), (0.5, 0.150515), (0.7, 0.2614394)):
        assert sink_volume(v) == pytest.approx(g, abs=1e-6)


def test_player_volume_is_clamped(qapp):
    from umc.gui.audioeditor.player import DEFAULT_VOLUME, Player

    p = Player()
    assert p.volume == DEFAULT_VOLUME < 1
    p.set_volume(1.5)
    assert p.volume == 1
    p.set_volume(-0.2)
    assert p.volume == 0


def test_a_short_sound_plays_to_the_end_on_a_real_device(qtbot):
    # Only where there is a sound device (not offscreen CI): before the QtAudio fix, play()
    # refused at once, and the end was never noticed because stateChanged can't reach Python.
    import numpy as np
    from PySide6.QtMultimedia import QMediaDevices

    from umc.audioedit.document import AudioDoc
    from umc.gui.audioeditor.player import Player

    if QMediaDevices.defaultAudioOutput().isNull():
        pytest.skip("no sound output here")
    rate = 44100
    doc = AudioDoc(rate=rate, samples=np.zeros((2, rate // 5), dtype=np.float32))
    p = Player()
    p.set_volume(0)
    with qtbot.waitSignal(p.finished, timeout=5000):
        assert p.play(doc, 0, doc.frames) == ""
        assert p.active
    assert not p.active
