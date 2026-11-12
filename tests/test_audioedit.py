"""Audio editor model (web/tests/audioedit.test.ts checks the same cases)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg
from umc.audioedit import ops, peaks
from umc.audioedit.document import AudioDoc
from umc.audioedit.io import open_audio, save_audio
from umc.audioedit.wav import read_wav, write_wav
from umc.core.errors import ConversionError


def ramp_doc(n: int = 10, channels: int = 1) -> AudioDoc:
    """Samples 0, 1, 2, … (as floats) so every edit is easy to read back."""
    x = np.tile(np.arange(n, dtype=np.float32), (channels, 1))
    return AudioDoc(8000, x)


def vals(doc: AudioDoc, ch: int = 0) -> list[float]:
    return doc.samples[ch].tolist()


# ------------------------------------------------------------------ WAV
@pytest.mark.parametrize("bits", [32, 16])
def test_wav_round_trip(bits):
    x = np.array([[0.0, 0.5, -0.5, 1.0], [0.25, -0.25, 0.0, -1.0]], dtype=np.float32)
    rate, y = read_wav(write_wav(44100, x, bits))
    assert rate == 44100 and y.shape == (2, 4)
    assert np.allclose(x, y, atol=1 / 30000 if bits == 16 else 0)


def test_wav_reads_24_bit_and_8_bit():
    import struct

    def wav(tag_bits: int, body: bytes, channels: int = 1) -> bytes:
        w = tag_bits // 8
        fmt = struct.pack("<HHIIHH", 1, channels, 8000, 8000 * w * channels, w * channels, tag_bits)
        return (
            b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + fmt + b"data" + struct.pack("<I", len(body)) + body
        )  # fmt: skip

    _, y = read_wav(wav(24, bytes([0, 0, 0x40, 0, 0, 0xC0])))  # +0.5, -0.5
    assert y[0].tolist() == [0.5, -0.5]
    _, y = read_wav(wav(8, bytes([128, 192, 64])))
    assert y[0].tolist() == [0.0, 0.5, -0.5]
    with pytest.raises(ConversionError):
        read_wav(b"not a wav file at all")


# ------------------------------------------------------------------ edits + undo
def test_cut_paste_and_undo_redo():
    doc = ramp_doc()
    doc.set_selection(2, 5)
    clip = doc.cut()
    assert clip[0].tolist() == [2, 3, 4]
    assert vals(doc) == [0, 1, 5, 6, 7, 8, 9] and doc.selection == (2, 2) and doc.dirty
    doc.set_selection(7)  # cursor at the end
    assert doc.paste(clip)
    assert vals(doc) == [0, 1, 5, 6, 7, 8, 9, 2, 3, 4] and doc.selection == (7, 10)
    assert doc.undo() and vals(doc) == [0, 1, 5, 6, 7, 8, 9] and doc.selection == (7, 7)
    assert doc.undo() and vals(doc) == list(range(10)) and doc.selection == (2, 5)
    assert not doc.dirty
    assert doc.redo() and doc.redo() and vals(doc) == [0, 1, 5, 6, 7, 8, 9, 2, 3, 4]
    assert doc.undo_label() == "Paste"


def test_paste_replaces_the_selection_and_fits_channels():
    doc = ramp_doc(6, channels=2)
    doc.set_selection(1, 4)
    assert doc.paste(np.array([[9.0]], dtype=np.float32))  # mono into stereo
    assert vals(doc, 0) == vals(doc, 1) == [0, 9, 4, 5]
    mono = ramp_doc(3)
    mono.set_selection(0)
    mono.paste(np.array([[2.0], [4.0]], dtype=np.float32))  # stereo into mono: averaged
    assert vals(mono) == [3, 0, 1, 2]


def test_trim_delete_silence():
    doc = ramp_doc()
    doc.set_selection(3, 6)
    assert doc.trim() and vals(doc) == [3, 4, 5] and doc.selection == (0, 3)
    assert doc.undo() and vals(doc) == list(range(10))
    doc.set_selection(0, 10)
    assert not doc.trim()  # everything selected: nothing to trim
    doc.set_selection(8, 99)  # clamped to the end
    assert doc.selection == (8, 10)
    assert doc.delete() and vals(doc) == list(range(8))
    doc.set_selection(1, 3)
    assert doc.silence() and vals(doc) == [0, 0, 0, 3, 4, 5, 6, 7]
    doc.set_selection(4)
    assert not doc.delete() and not doc.silence() and not doc.fade_in()  # need a selection


def test_fades_amplify_normalize_reverse():
    doc = AudioDoc(8000, np.ones((1, 5), dtype=np.float32))
    doc.set_selection(0, 5)
    assert doc.fade_in() and vals(doc) == [0, 0.25, 0.5, 0.75, 1]
    doc.undo()
    assert doc.fade_out() and vals(doc) == [1, 0.75, 0.5, 0.25, 0]
    doc.undo()
    doc.set_selection(2)  # no selection: the whole sound
    assert doc.amplify(-6.0206) and np.allclose(vals(doc), [0.5] * 5, atol=1e-4)
    assert doc.normalize() and np.isclose(ops.peak(doc.samples), ops.db_to_gain(-1.0), atol=1e-6)
    assert not doc.normalize()  # already there
    r = ramp_doc(4)
    assert r.reverse() and vals(r) == [3, 2, 1, 0]
    silent = AudioDoc(8000, np.zeros((1, 4), dtype=np.float32))
    assert not silent.normalize() and not silent.amplify(0)


def test_history_is_capped(monkeypatch):
    import umc.audioedit.document as d

    monkeypatch.setattr(d, "HISTORY_BYTES", 100)
    doc = AudioDoc(8000, np.zeros((1, 20), dtype=np.float32))  # 80 bytes per silence step
    for _ in range(3):
        doc.set_selection(0, 10)
        doc.reverse()
    assert len(doc._undo) == 1 and doc.can_undo()


def test_format_time():
    assert ops.format_time(65.25) == "1:05.250"
    assert ops.format_time(3723) == "1:02:03.000"
    assert ops.format_time(0) == "0:00.000"


# ------------------------------------------------------------------ waveform columns
def test_peak_columns_zoomed_in_and_out():
    x = np.array([[0, 1, -1, 2, -2, 3, -3, 4, -4, 5]], dtype=np.float32)
    p = peaks.build(x)
    mins, maxs = peaks.columns(x, p, 0, 2, 10)  # 2 frames per column, 5 columns exist
    assert mins[0].tolist() == [0, -1, -2, -3, -4] and maxs[0].tolist() == [1, 2, 3, 4, 5]
    mins, maxs = peaks.columns(x, p, 8, 0.5, 4)  # zoomed past single samples
    assert maxs[0].tolist() == [-4, -4, 5, 5]
    big = np.sin(np.arange(10_000, dtype=np.float32))[None, :]
    pb = peaks.build(big)
    mins, maxs = peaks.columns(big, pb, 0, 1000, 20)  # from the block summary
    assert mins.shape == (1, 10)
    for i in range(10):
        part = big[0, i * 1000 : (i + 1) * 1000]
        # blocks of 256 may reach a little past the column: never inside less than the truth
        assert mins[0, i] <= part.min() and maxs[0, i] >= part.max()
    assert peaks.columns(big, pb, 20_000, 10, 5)[0].shape == (1, 0)


# ------------------------------------------------------------------ files
@needs_ffmpeg
def test_open_join_and_save(media, tmp_path):
    doc = open_audio([media / "tone.wav", media / "clip.mp4"])
    assert doc.rate == 44100 and doc.channels == 2  # the clip's mono sound fits the first file
    assert 4.9 < doc.duration < 5.1 and not doc.dirty
    doc.set_selection(0, doc.rate)
    doc.delete()
    for fmt in ("wav", "mp3", "flac"):
        out = save_audio(doc, tmp_path / f"out.{fmt}", fmt, bitrate=128)
        back = open_audio([out])
        assert abs(back.duration - doc.duration) < 0.1, fmt
    with pytest.raises(ConversionError):
        open_audio([media / "img0.png"])
