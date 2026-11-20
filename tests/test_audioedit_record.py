"""Recording model (same cases as web/tests/audioedit-record.test.ts) and resampling."""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg
from umc.audioedit import record
from umc.audioedit.document import AudioDoc


def test_room_keeps_the_sound_within_the_limit():
    assert record.room(1000, 0, 0, 0, 2) == 500  # new sound, stereo take
    assert record.room(1000, 300, 1, 0, 2) == 700  # the take becomes mono
    assert record.room(1000, 300, 2, 100, 1) == 300  # the selection is replaced
    assert record.room(1000, 600, 2, 0, 2) == 0


def test_take_collects_chunks_until_full():
    take = record.Take(8000, 2, 10)
    assert take.add(np.full((2, 4), 0.25, np.float32))
    assert take.level == pytest.approx(0.25)
    assert not take.add(np.array([[0.1] * 8, [-0.75] * 8], np.float32))  # only 6 fit
    assert take.frames == 10 and take.full and take.level == pytest.approx(0.75)
    assert take.samples().shape == (2, 10)
    assert take.samples()[1, -1] == pytest.approx(-0.75)
    assert not take.add(np.ones((2, 3), np.float32)) and take.frames == 10
    assert take.seconds == pytest.approx(10 / 8000)
    assert record.Take(8000, 1, 10).samples().shape == (1, 0)


def test_raw_device_bytes():
    raw = np.array([0, 16384, -32768, 32767], "<i2").tobytes() + b"\x01"  # a stray byte
    x = record.from_int16(raw, 2)
    assert x.shape == (2, 2) and x.dtype == np.float32
    assert x[0].tolist() == [0.0, -1.0] and x[1, 0] == pytest.approx(0.5)
    f = record.from_float32(np.array([0.5, -0.5, 0.25, 1.0], "<f4").tobytes(), 2)
    assert f.tolist() == [[0.5, 0.25], [-0.5, 1.0]]


def test_a_take_goes_in_at_the_cursor_as_one_step():
    doc = AudioDoc(8000, np.zeros((2, 100), np.float32))
    doc.set_selection(40)
    assert doc.paste(np.ones((2, 10), np.float32), record.LABEL)
    assert doc.frames == 110 and doc.selection == (40, 50) and doc.undo_label() == "Record"
    doc.undo()
    assert doc.frames == 100


@needs_ffmpeg
def test_resample_keeps_the_length_in_time():
    from umc.audioedit import io

    t = np.arange(4410) / 44100
    x = np.vstack([np.sin(2 * np.pi * 440 * t)] * 2).astype(np.float32)
    y = io.resample(x, 44100, 48000)
    assert y.shape == (2, 4800)
    assert np.abs(y[:, 100:-100]).max() == pytest.approx(1.0, abs=0.02)
    assert io.resample(x, 44100, 44100) is x
