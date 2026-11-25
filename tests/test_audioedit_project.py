"""Audio editor projects (.umcaudio): tracks kept separate, exactly; damaged files refused."""

import io
import json
import zipfile

import numpy as np
import pytest

from umc.audioedit import project as aproj
from umc.audioedit import tracks as tr
from umc.audioedit.document import AudioDoc
from umc.audioedit.wav import write_wav


def two_tracks():
    rng = np.random.default_rng(1)
    a = tr.Track(AudioDoc(48000, rng.random((2, 300), dtype=np.float32)), "Voice", -3.0, -0.25)
    b = tr.Track(
        AudioDoc(48000, rng.random((1, 200), dtype=np.float32)), "Pad", mute=True, offset=99
    )
    return [a, b]


def zipped(meta: dict, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("project.json", json.dumps(meta))
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_round_trip_is_exact():
    tracks = two_tracks()
    back, active = aproj.from_bytes(aproj.to_bytes(tracks, 1))
    assert active == 1
    for t, u in zip(tracks, back, strict=True):
        assert (t.name, t.gain_db, t.pan, t.mute, t.solo, t.offset) == (
            u.name, u.gain_db, u.pan, u.mute, u.solo, u.offset,
        )  # fmt: skip
        assert u.doc.rate == 48000 and np.array_equal(t.doc.samples, u.doc.samples)


def test_settings_are_clamped_and_names_filled_in():
    wav = write_wav(8000, np.zeros((1, 10), np.float32))
    entry = {"file": "t.wav", "gain_db": 99, "pan": -5, "offset": -3, "name": " "}
    meta = {"format": aproj.FORMAT, "version": 1, "rate": 8000, "active": 7, "tracks": [entry]}
    (t,), active = aproj.from_bytes(zipped(meta, {"t.wav": wav}))
    assert (t.gain_db, t.pan, t.offset, t.name, active) == (tr.MAX_GAIN_DB, -1.0, 0, "Track 1", 0)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a zip", "isn't an audio editor project"),
        (zipped({"format": "other"}, {}), "isn't an audio editor project"),
        (zipped({"format": aproj.FORMAT, "version": 99, "tracks": []}, {}), "newer version"),
        (zipped({"format": aproj.FORMAT, "version": 1, "tracks": []}, {}), "no tracks"),
        (
            zipped({"format": aproj.FORMAT, "version": 1, "tracks": [{"file": "gone.wav"}]}, {}),
            "missing",
        ),
    ],
)
def test_damaged_projects_are_refused(data, message):
    with pytest.raises(aproj.ProjectError, match=message):
        aproj.from_bytes(data)


def test_tracks_must_share_a_rate():
    meta = {"format": aproj.FORMAT, "version": 1,
            "tracks": [{"file": "a.wav"}, {"file": "b.wav"}]}  # fmt: skip
    files = {"a.wav": write_wav(8000, np.zeros((1, 4), np.float32)),
             "b.wav": write_wav(16000, np.zeros((1, 4), np.float32))}  # fmt: skip
    with pytest.raises(aproj.ProjectError, match="different sample rates"):
        aproj.from_bytes(zipped(meta, files))
