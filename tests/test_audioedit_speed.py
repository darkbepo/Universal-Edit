"""Audio editor speed / tempo / pitch: the shared filter cases and real FFmpeg runs."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg
from umc.audioedit import speed
from umc.audioedit.document import AudioDoc
from umc.audioedit.io import change_speed
from umc.core.errors import ConversionCancelled, ConversionError

CASES = Path(__file__).parent / "fixtures" / "audioedit_speed_cases.json"
RATE = 44100


def test_shared_cases():
    for c in json.loads(CASES.read_text("utf-8"))["cases"]:
        name = f"{c['mode']} {c['value']} {c['rate']}"
        assert speed.filter_chain(c["mode"], c["value"], c["rate"]) == c["filter"], name
        assert speed.new_seconds(c["mode"], c["value"], 10, c["rate"]) == pytest.approx(
            c["seconds"], abs=1e-9
        ), name
        assert speed.is_identity(c["mode"], c["value"]) == c["identity"], name
        assert speed.expected_frames(c["mode"], c["value"], 123457, c["rate"]) == c["frames"]


@pytest.mark.parametrize("factor", [0.25, 0.3, 0.75, 3.0, 4.0])
def test_atempo_chain_stays_in_range(factor):
    parts = [float(p.split("=")[1]) for p in speed.atempo_chain(factor)]
    assert all(0.5 <= x <= 2 for x in parts)
    assert np.prod(parts) == pytest.approx(factor, rel=1e-5)


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        speed.clamp("volume", 1)


def test_replace_part_swaps_a_part_of_another_length():
    doc = AudioDoc(1000, np.arange(10, dtype=np.float32)[None, :])
    assert doc.replace_part("Change tempo", 2, 6, np.full((1, 2), 9, np.float32))
    assert doc.samples[0].tolist() == [0, 1, 9, 9, 6, 7, 8, 9]
    assert doc.selection == (2, 4) and doc.undo_label() == "Change tempo"
    doc.undo()
    assert doc.samples[0].tolist() == list(range(10))
    assert not doc.replace_part("x", 3, 3, np.ones((1, 1), np.float32))


def _tone(hz: float = 440.0, seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return np.vstack([np.sin(2 * np.pi * hz * t)] * 2).astype(np.float32)


def _pitch(x: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(x[0] * np.hanning(x.shape[1])))
    return float(np.argmax(spectrum)) * RATE / x.shape[1]


@needs_ffmpeg
@pytest.mark.parametrize(
    ("mode", "value", "seconds", "hz"),
    [
        ("pitch", 12, 2.0, 880),
        ("pitch", -12, 2.0, 220),
        ("tempo", 50, 4.0, 440),
        ("tempo", 200, 1.0, 440),
        ("speed", 200, 1.0, 880),
        ("speed", 50, 4.0, 220),
    ],
)
def test_ffmpeg_changes_the_sound(mode, value, seconds, hz):
    y = change_speed(_tone(), RATE, mode, value)
    assert y.shape == (2, round(seconds * RATE)) and y.dtype == np.float32
    assert _pitch(y) == pytest.approx(hz, rel=0.01)
    # the end is sound, not the padding's silence
    assert np.abs(y[:, -RATE // 20 :]).max() > 0.5


@needs_ffmpeg
def test_mono_stays_mono_and_short_parts_are_refused():
    y = change_speed(_tone()[:1], RATE, "tempo", 150)
    assert y.shape == (1, speed.expected_frames("tempo", 150, 2 * RATE, RATE))
    with pytest.raises(ConversionError, match="longer part"):
        change_speed(_tone(seconds=0.01), RATE, "tempo", 150)


@needs_ffmpeg
def test_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionCancelled):
        change_speed(_tone(seconds=30), RATE, "pitch", 3, cancel)
