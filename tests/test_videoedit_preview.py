from __future__ import annotations

import gc
import logging
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg
from umc.core.errors import ConversionCancelled
from umc.videoedit import preview
from umc.videoedit.audio_out import NullAudioOut, open_output
from umc.videoedit.media import probe_source
from umc.videoedit.preview import (
    PlaybackStreams,
    StillCache,
    preview_size,
    still,
    thumbnails,
    waveform,
)
from umc.videoedit.project import PictureClip, Project, ProjectSettings, Source, VideoClip


@pytest.fixture
def src(vmedia):
    names = {"red": "red.mp4", "blue": "blue.mp4"}
    return {k: probe_source(vmedia / v, k) for k, v in names.items()}


def _proj(src, main=(), **settings):
    return Project(ProjectSettings(**settings), tuple(src.values()), tuple(main))


def _red_blue(src):
    return _proj(src, [VideoClip("a", "red", 0.0, 2.0), VideoClip("b", "blue", 0.0, 2.0)])


def _leftover(tmp):
    return sorted(p.name for p in tmp.glob("*") if p.suffix in (".txt", ".ass", ".log"))


@pytest.fixture
def spawned(monkeypatch):
    """Every FFmpeg process the preview starts."""
    procs = []
    real = preview.ffmpeg.ffmpeg_popen

    def spy(*args, **kw):
        procs.append(real(*args, **kw))
        return procs[-1]

    monkeypatch.setattr(preview.ffmpeg, "ffmpeg_popen", spy)
    return procs


def _all_ended(procs, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in procs):
            return True
        time.sleep(0.05)
    return False


def _mean_diff(a: bytes, b: bytes) -> float:
    x = np.frombuffer(a, dtype=np.uint8).astype(np.int16)
    y = np.frombuffer(b, dtype=np.uint8).astype(np.int16)
    return float(np.abs(x - y).mean())


def test_preview_size():
    assert preview_size(_proj({}, resolution="1080p")) == (640, 360)
    assert preview_size(_proj({}, resolution="custom", width=320, height=240)) == (320, 240)
    assert preview_size(_proj({}, resolution="vertical")) == (202, 360)


@needs_ffmpeg
def test_still_size_and_colour(src, tmp_path):
    project = _red_blue(src)
    w, h = preview_size(project)
    data = still(project, 3.0, tmp_path)
    assert len(data) == w * h * 3
    px = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3).mean(axis=(0, 1))
    assert px[2] > 200 and px[0] < 50
    assert _leftover(tmp_path) == []  # the build's graph file is gone


@needs_ffmpeg
def test_still_past_the_end_is_background(src, tmp_path):
    project = _proj(src, [VideoClip("a", "red", 0.0, 2.0)], background="#102030")
    w, h = preview_size(project)
    assert still(project, 5.0, tmp_path) == bytes((0x10, 0x20, 0x30)) * (w * h)
    empty = _proj(src, background="#ff0000")
    w, h = preview_size(empty)
    assert still(empty, 0.0, tmp_path) == bytes((255, 0, 0)) * (w * h)


def test_still_cache_lru():
    cache = StillCache(max_frames=2)
    cache.put(1, 0, b"a")
    cache.put(1, 1, b"b")
    assert cache.get(1, 0) == b"a"  # 0 is now the most recent
    cache.put(1, 2, b"c")  # evicts 1
    assert cache.get(1, 1) is None
    assert cache.get(1, 0) == b"a" and cache.get(1, 2) == b"c"
    assert cache.get(2, 0) is None  # another project version


@needs_ffmpeg
def test_playback_streams_one_second(src, tmp_path):
    streams = PlaybackStreams(_red_blue(src), 3.0, tmp_path)
    try:
        assert streams.size == preview_size(_red_blue(src))
        frames = list(streams.frames())
        sound = b"".join(streams.audio())
    finally:
        streams.close()
        streams.close()  # safe twice
    assert abs(len(frames) - streams.fps) <= 1
    assert abs(len(sound) - 48000 * 4) <= 4800
    times = [t for t, _ in frames]
    assert times[0] == pytest.approx(3.0)
    assert all(b > a for a, b in zip(times, times[1:], strict=False))
    w, h = streams.size
    assert all(len(f) == w * h * 3 for _, f in frames)
    assert _leftover(tmp_path) == []


@needs_ffmpeg
def test_playback_streams_across_chunks(src, tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 0.5)
    streams = PlaybackStreams(_red_blue(src), 0.0, tmp_path)
    try:
        frames = list(streams.frames())
        sound = b"".join(streams.audio())
    finally:
        streams.close()
    assert len(streams._chunks) == 8
    step = 1 / streams.fps
    times = [t for t, _ in frames]
    assert abs(len(times) - 4 * streams.fps) <= 1
    assert all(
        b - a == pytest.approx(step, abs=1e-6) for a, b in zip(times, times[1:], strict=False)
    )
    assert abs(len(sound) - 4 * 48000 * 4) <= 4800
    w, h = streams.size
    first = np.frombuffer(frames[10][1], dtype=np.uint8).reshape(h, w, 3).mean(axis=(0, 1))
    last = np.frombuffer(frames[-10][1], dtype=np.uint8).reshape(h, w, 3).mean(axis=(0, 1))
    assert first[0] > 200 and last[2] > 200  # red, then blue


def test_playback_chunks_end_at_the_17th_item(tmp_path):
    pic = Source("p", tmp_path / "p.png", "image", 0.0, 320, 240, 0.0, False)
    main = [PictureClip(f"i{n}", "p", 1.0) for n in range(20)]
    project = Project(ProjectSettings(fps="30"), (pic,), tuple(main))
    streams = PlaybackStreams(project, 0.5, tmp_path)
    ends = [c.end / 30 + 0.5 for c in streams._chunks]
    assert ends == pytest.approx([16.0, 20.0])  # item 17 starts at 16 s
    streams.close()


@needs_ffmpeg
def test_playback_streams_close_early(src, tmp_path):
    streams = PlaybackStreams(_red_blue(src), 0.0, tmp_path)
    it = streams.frames()
    next(it)
    streams.close()
    assert len(list(it)) < 120  # stops instead of rendering the rest
    assert _leftover(tmp_path) == []


@needs_ffmpeg
def test_thumbnails_and_waveform(src, vmedia, tmp_path):
    thumbs = thumbnails(src["red"], 4, 40, tmp_path)
    assert len(thumbs) == 4 and all(p.exists() and p.stat().st_size > 0 for p in thumbs)
    pic = probe_source(vmedia / "pic.png", "pic")
    pics = thumbnails(pic, 4, 40, tmp_path)
    assert len(pics) == 1 and pics[0].exists()
    peaks = waveform(src["red"], 50)
    assert len(peaks) == 50 and max(peaks) > 0.1 and all(0 <= p <= 1 for p in peaks)
    assert waveform(src["blue"], 50) == []


def test_null_audio_clock():
    out = NullAudioOut()
    assert out.silent is True
    assert out.played_seconds() == 0.0
    out.write(bytes(48000 * 4 // 5))  # 0.2 s
    first = out.played_seconds()
    time.sleep(0.1)
    second = out.played_seconds()
    assert 0.0 <= first < second < 1.0
    out.close()


def test_open_output_without_audio(monkeypatch):
    monkeypatch.setenv("UMC_NO_AUDIO", "1")
    assert isinstance(open_output(), NullAudioOut)


@needs_ffmpeg
def test_playback_logs_a_failed_chunk(src, vmedia, tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 1.0)
    gone = tmp_path / "gone.mp4"
    shutil.copyfile(vmedia / "red.mp4", gone)
    source = probe_source(gone, "gone")
    project = Project(ProjectSettings(), (source,), (VideoClip("a", "gone", 0.0, 2.0),))
    gone.unlink()
    with (
        caplog.at_level(logging.WARNING, logger="umc.videoedit.preview"),
        PlaybackStreams(project, 0.0, tmp_path / "t") as streams,
    ):
        frames = list(streams.frames())
        sound = b"".join(streams.audio())
    assert abs(len(frames) - 60) <= 1  # padded with the background
    assert abs(len(sound) - 2 * 48000 * 4) <= 4800
    text = caplog.text
    assert "0.000" in text and "1.000" in text and "2.000" in text  # both chunks' ranges
    assert "gone.mp4" in text  # the reason from FFmpeg's log
    assert _leftover(tmp_path / "t") == []


@needs_ffmpeg
def test_playback_ok_logs_nothing(src, tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 0.5)
    with (
        caplog.at_level(logging.WARNING, logger="umc.videoedit.preview"),
        PlaybackStreams(_red_blue(src), 0.0, tmp_path) as streams,
    ):
        list(streams.frames())
        b"".join(streams.audio())
    assert caplog.records == []


def test_thumbnails_skip_to_key_frames_with_a_long_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake(args, **kw):
        seen["args"], seen["kw"] = list(args), kw
        return b""

    monkeypatch.setattr(preview.ffmpeg, "ffmpeg_bytes", fake)
    long = Source("v", tmp_path / "v.mp4", "video", 3600.0, 640, 360, 30.0, True)
    thumbnails(long, 10, 40, tmp_path)
    args = seen["args"]
    assert (
        args.index("-skip_frame") < args.index("-i")
        and args[args.index("-skip_frame") + 1] == "nokey"
    )
    assert seen["kw"]["timeout"] >= 60 + 3600 / 10


@needs_ffmpeg
def test_with_closes_every_process(src, tmp_path, spawned, monkeypatch):
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 0.5)
    with PlaybackStreams(_red_blue(src), 0.0, tmp_path) as streams:
        it = streams.frames()
        for _ in range(10):  # past the half: the next chunk is prefetched
            next(it)
        assert len(spawned) == 2
    assert _all_ended(spawned)
    assert _leftover(tmp_path) == []


@needs_ffmpeg
def test_closing_a_generator_kills_the_prefetched_chunk(src, tmp_path, spawned, monkeypatch):
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 0.5)
    streams = PlaybackStreams(_red_blue(src), 0.0, tmp_path)
    it = streams.frames()
    for _ in range(10):
        next(it)
    it.close()
    assert len(spawned) == 2 and _all_ended(spawned)
    assert all(not c.procs for c in streams._chunks)  # finished processes are dropped
    streams.close()


@needs_ffmpeg
def test_abandoned_streams_kill_their_processes(src, tmp_path, spawned):
    streams = PlaybackStreams(_red_blue(src), 0.0, tmp_path)
    streams._open(streams._chunks[0], "video")
    streams._open(streams._chunks[0], "audio")
    del streams
    gc.collect()
    assert len(spawned) == 2 and _all_ended(spawned)


@needs_ffmpeg
def test_still_can_be_cancelled(src, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionCancelled):
        still(_red_blue(src), 1.0, tmp_path, cancel_event=cancel)
    assert _leftover(tmp_path) == []


@needs_ffmpeg
def test_playback_seam_matches_stills(tmp_path, monkeypatch):
    """Across a chunk join every frame is the one ``still`` shows at that time."""
    ramp = tmp_path / "ramp.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "color=black:s=64x48:r=30:d=1,format=yuv420p,geq=lum='16+N*7':cb=128:cr=128",
         "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(ramp)],
        check=True,
    )  # fmt: skip
    source = probe_source(ramp, "ramp")
    project = Project(ProjectSettings(), (source,), (VideoClip("a", "ramp", 0.0, 1.0),))
    monkeypatch.setattr(preview, "CHUNK_SECONDS", 0.5)
    tmp = tmp_path / "t"
    with PlaybackStreams(project, 0.0, tmp) as streams:
        frames = list(streams.frames())
    assert len(frames) == 30
    for k in range(30):  # the join is between frames 14 and 15; stills hit every frame
        t, data = frames[k]
        assert t == pytest.approx(k / 30)
        assert _mean_diff(data, still(project, t, tmp)) < 6, k
    # and neighbouring frames really differ, so a skip or repeat would show
    assert _mean_diff(frames[14][1], frames[15][1]) > 6
