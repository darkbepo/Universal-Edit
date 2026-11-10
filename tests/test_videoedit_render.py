from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from tests.conftest import needs_ffmpeg
from tests.videoedit_helpers import durations, frame_at, render_to, rms
from umc.core.errors import ConversionError
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
    assert plan.cwd == tmp_path
    # The graph goes into a file (Windows' command line is limited to 32,767 characters).
    script = plan.args[plan.args.index("-/filter_complex") + 1]
    assert (tmp_path / script).read_text(encoding="utf-8") == plan.graph
    silent = build_render(_proj(src, [_clip("a")]), tmp_path, audio=False)
    assert silent.audio_label is None
    assert "[aout]" not in silent.graph
    assert silent.args[silent.args.index("-/filter_complex") + 1] != script  # one file per build


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
    r, g, b = img.getpixel((160, 120))
    assert r < 30 and 100 < g < 160 and b < 30  # FFmpeg "green" is #008000


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
    g = build_render(_proj(src, [c]), tmp_path).graph
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


def _pic_proj(pic, **settings):
    s = probe_source(pic, "x")
    return Project(ProjectSettings(**settings), (s,), (PictureClip("c", "x", duration=0.5),))


def test_picture_cache_follows_background_and_file(tmp_path):
    pic = tmp_path / "alpha.png"
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    img.paste((0, 200, 0, 255), (100, 0, 200, 100))  # right half opaque green
    img.save(pic)
    work = tmp_path / "work"
    work.mkdir()
    size = {"resolution": "custom", "width": 200, "height": 100}
    red = render_to(_pic_proj(pic, background="#ff0000", **size), tmp_path / "r.mp4", work)
    r, g, b = frame_at(red, 0.2).getpixel((40, 50))
    assert r > 200 and g < 40 and b < 40  # transparency flattened on the background
    r, g, b = frame_at(red, 0.2).getpixel((160, 50))
    assert g > 160 and r < 40
    blue = render_to(_pic_proj(pic, background="#0000ff", **size), tmp_path / "b.mp4", work)
    r, g, b = frame_at(blue, 0.2).getpixel((40, 50))
    assert b > 200 and r < 40 and g < 40  # the new background, not the cached red one
    Image.new("RGB", (200, 100), (255, 255, 0)).save(pic)  # the file is replaced on disk
    yellow = render_to(_pic_proj(pic, background="#0000ff", **size), tmp_path / "y.mp4", work)
    r, g, b = frame_at(yellow, 0.2).getpixel((40, 50))
    assert r > 200 and g > 200 and b < 40
    assert all(p.suffix == ".png" for p in (work / "pics").iterdir())  # no temp leftovers


def test_render_exif_rotated_picture(tmp_path):
    img = Image.new("RGB", (200, 100), (255, 0, 0))
    img.paste((0, 0, 255), (100, 0, 200, 100))  # stored: left red, right blue
    exif = Image.Exif()
    exif[0x0112] = 6  # shown turned 90° clockwise: tall, top red, bottom blue
    pic = tmp_path / "rot.jpg"
    img.save(pic, exif=exif, quality=95)
    p = _pic_proj(pic, resolution="custom", width=320, height=240)
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    frame = frame_at(out, 0.2)
    assert all(v < 20 for v in frame.getpixel((20, 120)))  # letterbox bars at the sides
    r, _g, b = frame.getpixel((160, 60))
    assert r > 200 and b < 60
    r, _g, b = frame.getpixel((160, 180))
    assert b > 200 and r < 60


def test_render_odd_sizes_become_even(tmp_path):
    odd = tmp_path / "odd.mkv"
    lavfi = ["-f", "lavfi", "-i", "color=red:s=321x241:r=30:d=1"]
    cmd = ["ffmpeg", "-v", "error", "-y", *lavfi, "-c:v", "ffv1", str(odd)]
    subprocess.run(cmd, check=True)
    pic = tmp_path / "odd.png"
    Image.new("RGB", (201, 101), (255, 128, 0)).save(pic)
    sources = (probe_source(odd, "o"), probe_source(pic, "q"))
    main = (VideoClip("a", "o", 0.0, 1.0), PictureClip("b", "q", duration=0.5))
    p = Project(ProjectSettings(), sources, main)
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    assert frame_at(out, 0.2).size == (320, 240)
    assert frame_at(out, 1.2).size == (320, 240)
    plan = build_render(p, tmp_path, size=(321, 181))
    assert "scale=320:180" in plan.graph and "321" not in plan.graph
    small = render_to(p, tmp_path / "small.mp4", tmp_path, size=(161, 91))
    assert frame_at(small, 0.2).size == (160, 90)
    assert "scale=2:2" in build_render(p, tmp_path, size=(1, 1)).graph


def test_render_paths_with_spaces_and_quotes(tmp_path, vmedia):
    folder = tmp_path / "my 'clips' (1)"
    folder.mkdir()
    clip = folder / "a b 'q' (1).mp4"
    pic = folder / "pic 'x' [2];.png"
    shutil.copy(vmedia / "red.mp4", clip)
    shutil.copy(vmedia / "pic.png", pic)
    work = tmp_path / "work 'dir' [x]"
    work.mkdir()
    sources = (probe_source(clip, "a"), probe_source(pic, "b"))
    main = (VideoClip("c1", "a", 0.0, 1.0), PictureClip("c2", "b", duration=0.5))
    p = Project(ProjectSettings(), sources, main)
    out = render_to(p, tmp_path / "out file.mp4", work)
    v, a = durations(out)
    assert v == pytest.approx(1.5, abs=FRAME + 1e-3)
    assert a == pytest.approx(1.5, abs=0.05)


def test_render_single_item(src, tmp_path):
    out = render_to(_proj(src, [_clip("a")]), tmp_path / "out.mp4", tmp_path)
    v, a = durations(out)
    assert v == pytest.approx(2.0, abs=FRAME + 1e-3)
    assert a == pytest.approx(2.0, abs=0.05)
    assert _mostly(_rgb(frame_at(out, 1.0)), 0)
    assert rms(out, 0.2, 1.8) > 0.05


def test_render_empty_project_is_refused(src, tmp_path):
    with pytest.raises(ConversionError, match="empty"):
        build_render(_proj(src), tmp_path)


def test_sound_is_always_limited(src, tmp_path):
    """The limiter runs with or without music, so a window sounds like the full project."""
    plain = build_render(_proj(src, [_clip("a")]), tmp_path).graph
    assert "alimiter=limit=0.95:level=0" in plain
    m = AudioClip("m", "music", 0.0, 0.0, 1.0)
    mixed = build_render(_proj(src, [_clip("a")], music=[m]), tmp_path).graph
    assert mixed.count("alimiter=limit=0.95:level=0") == 1


def test_many_pictures_keep_the_command_line_short(tmp_path):
    pic = tmp_path / "p.png"
    Image.new("RGB", (32, 18), (255, 128, 0)).save(pic)
    main = tuple(PictureClip(f"c{i}", "p", duration=0.1) for i in range(120))
    settings = ProjectSettings(resolution="custom", width=64, height=36)
    p = Project(settings, (probe_source(pic, "p"),), main)
    plan = build_render(p, tmp_path)
    assert len(subprocess.list2cmdline(plan.args)) < 8000
    out = render_to(p, tmp_path / "out.mp4", tmp_path, audio=False)
    assert durations(out)[0] == pytest.approx(12.0, abs=0.1)


# --- text titles (ASS) -------------------------------------------------------------------


def test_build_ass_escaping():
    from umc.videoedit.text_ass import build_ass

    ass = build_ass([TextItem("t", "a{b}\\c\nline2", 0.0, 1.0)], (320, 240))
    assert "a\\{b\\}\\\\c\\Nline2" in ass
    assert "PlayResX: 320" in ass and "PlayResY: 240" in ass
    assert "WrapStyle: 0" in ass and "ScaledBorderAndShadow: yes" in ass


def test_build_ass_position_and_size():
    from umc.videoedit.text_ass import build_ass

    ass = build_ass([TextItem("t", "Hi", 0.0, 1.0, size=8.0)], (320, 240))
    assert "\\an2\\pos(160,228)" in ass
    style = next(line for line in ass.splitlines() if line.startswith("Style:"))
    assert style.split(",")[2] == "19"
    top_left = build_ass(
        [TextItem("t", "Hi", 0.0, 1.0, anchor="top-left", x_offset=10)], (320, 240)
    )
    assert "\\an7\\pos(48,12)" in top_left


def test_build_ass_negative_start_partial_fade():
    from umc.videoedit.text_ass import build_ass

    # fade-in of 1 s that began 0.4 s before the window: 40 % done at time 0
    ass = build_ass([TextItem("t", "Hi", -0.4, 2.0, fade_in=1.0)], (320, 240))
    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    assert "0:00:00.00,0:00:02.00" in dialogue
    assert "\\fade(153,0,255,0,600,2000,2000)" in dialogue
    # past the fade-in already: no fade at all
    done = build_ass([TextItem("t", "Hi", -2.0, 2.0, fade_in=1.0)], (320, 240))
    assert "\\fad" not in done


def _text_proj(src, item, **settings):
    return _proj(src, [_clip("a", "blue")], text=[item], **settings)


def test_render_text_visible(src, tmp_path):
    item = TextItem("t", "HELLO", 0.0, 1.5, anchor="center", size=20.0, outline=0.0)
    out = render_to(_text_proj(src, item), tmp_path / "out.mp4", tmp_path)
    for t, expect in ((1.0, True), (1.8, False)):
        img = np.asarray(frame_at(out, t))
        h, w = img.shape[:2]
        centre = img[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3]
        bright = int((centre[..., 0] > 200).sum())
        assert (bright > 50) == expect, (t, bright)


def test_render_text_box_colour(src, tmp_path):
    item = TextItem(
        "t",
        "HELLO",
        0.0,
        1.5,
        anchor="center",
        size=20.0,
        box=True,
        box_colour="#ff0000",
        box_opacity=100,
        outline=6.0,
    )
    out = render_to(_text_proj(src, item), tmp_path / "out.mp4", tmp_path)
    img = np.asarray(frame_at(out, 1.0)).astype(int)
    h, w = img.shape[:2]
    red = (img[..., 0] > 200) & (img[..., 1] < 60) & (img[..., 2] < 60)
    # the box surrounds the text, so red appears in the middle band of the frame
    assert int(red[h // 3 : 2 * h // 3].sum()) > 200


def test_text_escaping_and_unicode_path(src, tmp_path, vmedia):
    odd = tmp_path / "Ærø 'it' {x}.mp4"
    shutil.copy(vmedia / "red.mp4", odd)
    sources = (probe_source(odd, "r"),)
    text = TextItem("t", "Ærø ✓ {b} \\ 'q'", 0.0, 1.0)
    p = Project(ProjectSettings(), sources, (VideoClip("c", "r", 0.0, 1.0),), ((text,),))
    out = render_to(p, tmp_path / "out.mp4", tmp_path)
    assert durations(out)[0] == pytest.approx(1.0, abs=FRAME + 1e-3)


def test_text_window_matches_full_render(src, tmp_path):
    item = TextItem("t", "HELLO", 0.5, 2.0, anchor="center", size=25.0, outline=0.0)
    p = _text_proj(src, item)
    full = render_to(p, tmp_path / "full.mp4", tmp_path)
    for t in (0.4, 0.8, 1.2, 1.9):
        w = window(p, t, t + FRAME)
        part = render_to(
            w.project, tmp_path / f"w{t}.mp4", tmp_path, lead=w.lead, duration=w.duration
        )
        x = np.asarray(frame_at(part, 0.0), dtype=np.float64)
        y = np.asarray(frame_at(full, t), dtype=np.float64)
        assert np.abs(x - y).mean() < 6, t
