from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg
from tests.videoedit_helpers import durations, frame_at, render_to, rms
from umc.videoedit.media import probe_source
from umc.videoedit.project import (
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    TextItem,
    Transition,
    VideoClip,
    length,
)
from umc.videoedit.render import build_render
from umc.videoedit.window import window

pytestmark = needs_ffmpeg

FPS = 30.0
FRAME = 1 / FPS


@pytest.fixture
def src(vmedia):
    names = {
        "red": "red.mp4",
        "blue": "blue.mp4",
        "tall": "green_tall.mp4",
        "pic": "pic.png",
        "music": "music.wav",
    }
    return {k: probe_source(vmedia / v, k) for k, v in names.items()}


def _proj(src, main=(), music=(), text=(), **settings):
    return Project(
        ProjectSettings(**settings),
        tuple(src.values()),
        tuple(main),
        (tuple(text),),
        (tuple(music),),
    )


def _clip(cid, source="red", **kw):
    return VideoClip(cid, source, kw.pop("in_", 0.0), kw.pop("out", 2.0), **kw)


def _rgb(img, xy=None):
    if xy is None:
        xy = (img.width // 2, img.height // 2)
    return img.getpixel(xy)


def _mostly(rgb, channel):
    return rgb[channel] > 180 and all(v < 80 for i, v in enumerate(rgb) if i != channel)


def test_build_render_plan_shape(src, tmp_path):
    plan = build_render(_proj(src, [_clip("a")]), tmp_path)
    assert plan.video_label == "[vout]"
    assert plan.audio_label == "[aout]"
    assert plan.total == pytest.approx(2.0)
    assert "-filter_complex" in plan.args
    assert plan.cwd == tmp_path
    silent = build_render(_proj(src, [_clip("a")]), tmp_path, audio=False)
    assert silent.audio_label is None
    assert "[aout]" not in silent.args[silent.args.index("-filter_complex") + 1]


def test_render_join_two_clips(src, tmp_path):
    p = _proj(src, [_clip("a"), _clip("b", "blue")])
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    v, _a = durations(out)
    assert v == pytest.approx(4.0, abs=FRAME + 1e-3)
    assert _mostly(_rgb(frame_at(out, 0.5)), 0)
    assert _mostly(_rgb(frame_at(out, 3.5)), 2)


def test_render_crossfade_mixes(src, tmp_path):
    b = _clip("b", "blue", transition_in=Transition("fade", 1.0))
    p = _proj(src, [_clip("a"), b])
    assert length(p) == pytest.approx(3.0)
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    v, _a = durations(out)
    assert v == pytest.approx(3.0, abs=FRAME + 1e-3)
    r, _g, bl = _rgb(frame_at(out, 1.5))
    assert abs(r - 128) <= 45 and abs(bl - 128) <= 45


def test_render_speed_and_trim(src, tmp_path):
    p = _proj(src, [_clip("a", in_=0.5, out=1.5, speed=2.0)])
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    v, a = durations(out)
    assert v == pytest.approx(0.5, abs=FRAME + 1e-3)
    assert a == pytest.approx(0.5, abs=0.05)


def test_render_letterbox_tall_clip(src, tmp_path):
    p = _proj(src, [_clip("a", "tall", out=1.0)], resolution="custom", width=320, height=240)
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    img = frame_at(out, 0.5)
    assert all(v < 20 for v in img.getpixel((5, 120)))
    assert _mostly(_rgb(img), 1) or img.getpixel((160, 120))[1] > 100


def test_render_mixed_audio_stays_in_sync(src, tmp_path):
    p = _proj(src, [_clip("a"), PictureClip("p", "pic", duration=1.5), _clip("b", "blue")])
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    v, a = durations(out)
    assert v == pytest.approx(5.5, abs=FRAME + 1e-3)
    assert a == pytest.approx(v, abs=0.05)
    assert rms(out, 0.2, 1.8) > 0.05
    assert rms(out, 2.2, 3.3) < 0.01
    img = frame_at(out, 2.75)
    r, g, b = img.getpixel((160, 120))
    assert r > 200 and 120 < g < 200 and b < 60  # orange picture


def test_render_music_volume_and_fade(src, tmp_path):
    def music(volume, fade_out=0.0):
        return AudioClip("m", "music", 0.0, 0.0, 4.0, volume=volume, fade_out=fade_out)

    loud = render_to(
        _proj(src, music=[music(100)]), tmp_path / "loud.mp4", tmp_path, size=(160, 90)
    )
    quiet = render_to(
        _proj(src, music=[music(50)]), tmp_path / "quiet.mp4", tmp_path, size=(160, 90)
    )
    ratio = rms(quiet, 1.0, 3.0) / rms(loud, 1.0, 3.0)
    assert ratio == pytest.approx(0.5, abs=0.1)
    faded = render_to(
        _proj(src, music=[music(100, fade_out=1.0)]),
        tmp_path / "faded.mp4",
        tmp_path,
        size=(160, 90),
    )
    assert rms(faded, 3.7, 4.0) < 0.3 * rms(faded, 1.0, 2.5)


def test_render_text_only_project(src, tmp_path):
    p = _proj(src, text=[TextItem("t", "Hello", 0.0, 2.0)], background="#204080")
    out = render_to(p, tmp_path / "out.mp4", tmp_path, size=(320, 180))
    v, _a = durations(out)
    assert v == pytest.approx(2.0, abs=FRAME + 1e-3)
    r, g, b = frame_at(out, 1.0).getpixel((5, 5))
    assert abs(r - 0x20) < 12 and abs(g - 0x40) < 12 and abs(b - 0x80) < 12


def test_window_render_matches_full_render(src, tmp_path):
    a = _clip("a", fade_in=0.5)
    b = _clip("b", "blue", transition_in=Transition("fade", 0.5))
    p = _proj(src, [a, b])
    assert length(p) == pytest.approx(3.5)
    full = render_to(p, tmp_path / "full.mp4", tmp_path)
    for t in (0.3, 1.6, 2.0, 3.2):
        w = window(p, t, t + FRAME)
        part = render_to(
            w.project,
            tmp_path / f"w{t}.mp4",
            tmp_path,
            lead=w.lead,
            duration=w.duration,
        )
        x = np.asarray(frame_at(part, 0.0), dtype=np.float64)
        y = np.asarray(frame_at(full, t), dtype=np.float64)
        assert np.abs(x - y).mean() < 6, t


def test_render_music_negative_start(src, tmp_path):
    m = AudioClip("m", "music", -1.0, 0.0, 4.0)
    p = _proj(src, music=[m])
    assert length(p) == pytest.approx(3.0)
    out = render_to(p, tmp_path / "out.mp4", tmp_path, size=(160, 90))
    v, a = durations(out)
    assert v == pytest.approx(3.0, abs=FRAME + 1e-3)
    assert a == pytest.approx(3.0, abs=0.05)
    assert rms(out, 0.0, 0.3) > 0.05


def test_render_music_under_clip_mixes_both(src, tmp_path):
    m = AudioClip("m", "music", 0.5, 0.0, 1.0)
    p = _proj(src, [_clip("a", "blue")], music=[m])
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    assert rms(out, 0.0, 0.4) < 0.01
    assert rms(out, 0.6, 1.4) > 0.05
    assert rms(out, 1.6, 2.0) < 0.01


def test_render_tail_past_main_row(src, tmp_path):
    p = _proj(src, [_clip("a", out=1.0)], text=[TextItem("t", "Hi", 0.0, 2.0)])
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    v, a = durations(out)
    assert v == pytest.approx(2.0, abs=FRAME + 1e-3)
    assert a == pytest.approx(2.0, abs=0.05)
    assert all(c < 20 for c in frame_at(out, 1.5).getpixel((160, 120)))


def test_render_grading_and_flip_build(src, tmp_path):
    c = replace(_clip("a"), brightness=20, contrast=10, saturation=-50, rotate=90, flip_h=True)
    c = replace(c, crop_left=10, crop_right=10)
    graph = build_render(_proj(src, [c]), tmp_path).args
    g = graph[graph.index("-filter_complex") + 1]
    assert "crop=" in g and "transpose=1" in g and "hflip" in g
    assert "eq=brightness=0.100:contrast=1.100:saturation=0.500" in g
    out = render_to(_proj(src, [c]), tmp_path / "out.mp4", tmp_path)
    assert durations(out)[0] == pytest.approx(2.0, abs=FRAME + 1e-3)


def test_render_music_loop_repeats_the_slice(src, tmp_path):
    m = AudioClip("m", "music", 0.0, 1.0, 2.0, loop_until=3.5)
    out = render_to(_proj(src, music=[m]), tmp_path / "out.mp4", tmp_path, size=(160, 90))
    v, a = durations(out)
    assert a == pytest.approx(3.5, abs=0.05)
    for t in (0.2, 1.2, 2.2, 3.0):
        assert rms(out, t, t + 0.3) > 0.05, t


def test_render_music_keeps_its_level(src, tmp_path, vmedia):
    m = AudioClip("m", "music", 0.0, 0.0, 4.0)
    out = render_to(_proj(src, music=[m]), tmp_path / "out.mp4", tmp_path, size=(160, 90))
    assert rms(out, 1.0, 3.0) == pytest.approx(rms(vmedia / "music.wav", 1.0, 3.0), rel=0.02)


def test_render_sound_only_and_picture_only(src, tmp_path):
    p = _proj(src, [_clip("a"), PictureClip("p", "pic", duration=1.0)])
    sound = render_to(p, tmp_path / "sound.m4a", tmp_path, video=False)
    assert durations(sound) == (0.0, pytest.approx(3.0, abs=0.05))
    still = render_to(p, tmp_path / "still.mp4", tmp_path, audio=False)
    assert durations(still) == (pytest.approx(3.0, abs=FRAME + 1e-3), 0.0)
