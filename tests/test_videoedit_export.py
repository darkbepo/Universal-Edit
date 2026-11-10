from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import needs_ffmpeg
from tests.videoedit_helpers import durations, frame_at
from umc.core import options as opts
from umc.core.errors import ConversionCancelled, ConversionError
from umc.tools import ffmpeg
from umc.videoedit import export
from umc.videoedit.export import (
    EXPORT_CONTAINERS,
    GROUP_SIZE,
    ExportOptions,
    render_groups,
    run_export,
)
from umc.videoedit.media import probe_source
from umc.videoedit.project import (
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    TextItem,
    Transition,
    VideoClip,
    layout,
    length,
)

pytestmark = needs_ffmpeg

FRAME = 1 / 30


@pytest.fixture
def src(vmedia):
    names = {"red": "red.mp4", "blue": "blue.mp4", "music": "music.wav"}
    return {k: probe_source(vmedia / v, k) for k, v in names.items()}


def _proj(src, main=(), music=(), text=(), **settings):
    return Project(
        ProjectSettings(**settings), tuple(src.values()), tuple(main), (tuple(text),),
        (tuple(music),),
    )  # fmt: skip


def _crossfade_project(src):
    a = VideoClip("a", "red", 0.0, 2.0)
    b = VideoClip("b", "blue", 0.0, 2.0, transition_in=Transition("fade", 0.5))
    text = TextItem("t", "Hello", 0.5, 2.5)
    music = AudioClip("m", "music", 0.0, 0.0, 3.0)
    return _proj(src, [a, b], [music], [text])


def _options(**values) -> ExportOptions:
    return opts.build(ExportOptions, {**opts.defaults(ExportOptions), **values})


def _streams(path: Path) -> dict[str, dict]:
    cmd = [ffmpeg.ffprobe_path(), "-v", "error", "-print_format", "json", "-show_streams"]
    data = json.loads(subprocess.run([*cmd, str(path)], capture_output=True, check=True).stdout)
    out: dict[str, dict] = {}
    for s in data["streams"]:
        out.setdefault(s["codec_type"], s)
    return out


def _export(project, tmp_path, name="out.mp4", **values) -> Path:
    events: list[float] = []
    dest = run_export(
        project,
        _options(**values),
        tmp_path / name,
        on_progress=lambda p, _m: events.append(p),
        cancel_event=threading.Event(),
    )
    assert dest == tmp_path / name
    assert events and max(events) <= 100.0
    assert not list(tmp_path.glob("*.part*"))
    return dest


def test_export_options_schema():
    assert EXPORT_CONTAINERS == ("mp4", "mkv", "webm", "mov", "avi", "gif")
    o = ExportOptions()
    assert (o.container, o.video_codec, o.audio_codec, o.crf, o.preset) == (
        "mp4", "auto", "auto", 20, "medium"
    )  # fmt: skip
    assert (o.video_bitrate, o.audio_bitrate, o.resolution, o.remove_audio) == (
        0, 192, "project", False
    )  # fmt: skip
    assert (o.gif_fps, o.gif_width) == (15, 480)
    specs = {s.name: s for s in opts.option_specs(ExportOptions)}
    gif = {**opts.defaults(ExportOptions), "container": "gif"}
    mp4 = opts.defaults(ExportOptions)
    for name in ("video_codec", "audio_codec", "video_bitrate", "audio_bitrate"):
        assert not specs[name].visible(gif, "")
        assert specs[name].visible(mp4, "")
    for name in ("gif_fps", "gif_width"):
        assert specs[name].visible(gif, "")
        assert not specs[name].visible(mp4, "")
    assert [v for v, _ in specs["container"].choices] == list(EXPORT_CONTAINERS)
    assert specs["crf"].help == "Lower = better quality, bigger file."
    assert GROUP_SIZE == 16


def test_export_mp4_defaults(src, tmp_path):
    out = _export(_crossfade_project(src), tmp_path)
    s = _streams(out)
    assert s["video"]["codec_name"] == "h264"
    assert s["audio"]["codec_name"] == "aac"
    v, _a = durations(out)
    assert v == pytest.approx(3.5, abs=FRAME + 1e-3)


def test_export_webm_vp9_opus(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 1.0)])
    out = _export(p, tmp_path, "out.webm", container="webm", preset="ultrafast")
    s = _streams(out)
    assert s["video"]["codec_name"] == "vp9"
    assert s["audio"]["codec_name"] == "opus"


def test_export_remove_audio(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 1.0)])
    out = _export(p, tmp_path, remove_audio=True)
    assert "audio" not in _streams(out)
    assert "video" in _streams(out)


def test_export_resolution_720p(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 0.5)])
    out = _export(p, tmp_path, resolution="720p", preset="ultrafast")
    s = _streams(out)["video"]
    assert s["height"] == 720
    assert s["width"] % 2 == 0
    assert s["width"] == 960  # 320×240 project scaled by its height


def test_export_bad_codec_is_a_plain_message(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 0.5)])
    with pytest.raises(ConversionError):
        _export(p, tmp_path, "out.avi", container="avi", audio_codec="opus")
    assert not (tmp_path / "out.avi").exists()


def test_export_gif(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 1.0)])
    out = _export(p, tmp_path, "out.gif", container="gif", gif_fps=10, gif_width=200)
    with Image.open(out) as img:
        assert img.format == "GIF"
        assert img.n_frames > 1
        assert img.width == 200


def _pictures(tmp_path: Path, n: int = 20) -> Project:
    colours = {"r": (255, 0, 0), "b": (0, 0, 255)}
    sources = []
    for key, rgb in colours.items():
        Image.new("RGB", (64, 36), rgb).save(tmp_path / f"{key}.png")
        sources.append(probe_source(tmp_path / f"{key}.png", key))
    main = [
        PictureClip(
            f"p{i}", "r" if i % 2 == 0 else "b", 0.2,
            transition_in=Transition("fade" if i else "none", 0.1),
        )
        for i in range(n)
    ]  # fmt: skip
    settings = ProjectSettings(resolution="custom", width=160, height=90, fps="30")
    return Project(settings, tuple(sources), tuple(main))


def test_render_groups_keeps_timing(tmp_path):
    p = _pictures(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    grouped = render_groups(p, work, lambda _f: None, threading.Event())
    assert len(grouped.main) == 2
    assert all(isinstance(c, VideoClip) and c.fit == "stretch" for c in grouped.main)
    assert grouped.main[0].transition_in.kind == "none"
    assert grouped.main[1].transition_in == p.main[GROUP_SIZE].transition_in
    assert length(grouped) == pytest.approx(length(p), abs=0.001)
    assert sorted(work.glob("group_0_*.mkv"))

    out = _export(p, tmp_path, preset="ultrafast")
    v, _a = durations(out)
    assert v == pytest.approx(length(p), abs=FRAME + 1e-3)
    # Item 16 (red) crossfades from item 15 (blue) across the group boundary.
    span = layout(p)[p.main[GROUP_SIZE].id]
    r, _g, b = frame_at(out, span.start + 0.05).getpixel((80, 45))
    assert r > 50 and b > 50


def test_export_cancel_leaves_no_file(tmp_path):
    p = _pictures(tmp_path)
    cancel = threading.Event()

    def on_progress(percent: float, _msg: str) -> None:
        if percent > 10:
            cancel.set()

    dest = tmp_path / "out.mp4"
    with pytest.raises(ConversionCancelled):
        run_export(p, _options(), dest, on_progress=on_progress, cancel_event=cancel)
    assert not dest.exists()
    assert not (tmp_path / "out.part.mp4").exists()


def test_export_failure_keeps_existing_dest(src, tmp_path, monkeypatch):
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"keep me")

    def fail(args, **_kw):
        Path(args[-1]).write_bytes(b"half")  # a partial output
        raise ConversionError("Couldn't export the video.")

    monkeypatch.setattr(export.ffmpeg, "run_ffmpeg", fail)
    p = _proj(src, [VideoClip("a", "red", 0.0, 1.0)])
    with pytest.raises(ConversionError, match="Couldn't export the video."):
        run_export(p, _options(), dest, on_progress=lambda *_: None, cancel_event=threading.Event())
    assert dest.read_bytes() == b"keep me"
    assert not (tmp_path / "out.part.mp4").exists()


def test_export_cancel_survives_a_locked_part_file(tmp_path, monkeypatch):
    """FFmpeg may still hold the part file just after a cancel: still ConversionCancelled."""
    p = _pictures(tmp_path)
    cancel = threading.Event()
    real_unlink = Path.unlink

    def unlink(self, *args, **kw):
        if self.name == "out.part.mp4":
            raise PermissionError("in use")
        return real_unlink(self, *args, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(ConversionCancelled):
        run_export(
            p,
            _options(),
            tmp_path / "out.mp4",
            on_progress=lambda pct, _m: cancel.set() if pct > 10 else None,
            cancel_event=cancel,
        )
    assert not (tmp_path / "out.mp4").exists()


def test_export_replace_failure_is_a_plain_message(src, tmp_path, monkeypatch):
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"keep me")

    def locked(_a, _b):
        raise PermissionError("in use")

    monkeypatch.setattr(export.os, "replace", locked)
    p = _proj(src, [VideoClip("a", "red", 0.0, 0.5)])
    with pytest.raises(ConversionError, match="Couldn't save over “out.mp4”"):
        run_export(
            p, _options(preset="ultrafast"), dest, on_progress=lambda *_: None,
            cancel_event=threading.Event(),
        )  # fmt: skip
    assert dest.read_bytes() == b"keep me"
    assert not (tmp_path / "out.part.mp4").exists()


def test_export_refuses_a_wrong_extension(src, tmp_path):
    p = _proj(src, [VideoClip("a", "red", 0.0, 0.5)])
    with pytest.raises(ConversionError, match=r"must end in \.webm"):
        run_export(
            p, _options(container="webm"), tmp_path / "out.mp4", on_progress=lambda *_: None,
            cancel_event=threading.Event(),
        )  # fmt: skip
    assert not list(tmp_path.iterdir())


def test_render_groups_second_level_frees_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "GROUP_SIZE", 3)
    p = _pictures(tmp_path, 10)  # 10 → 4 groups → 2 groups → done
    work = tmp_path / "work"
    work.mkdir()
    grouped = render_groups(p, work, lambda _f: None, threading.Event())
    assert len(grouped.main) == 2
    assert length(grouped) == pytest.approx(length(p), abs=0.001)
    assert not list(work.glob("group_0_*.mkv"))
    assert sorted(f.name for f in work.glob("group_*.mkv")) == ["group_1_0.mkv", "group_1_1.mkv"]
    out = _export(grouped, tmp_path, preset="ultrafast")
    v, _a = durations(out)
    assert v == pytest.approx(length(p), abs=FRAME + 1e-3)


def test_intermediates_use_the_h264_fallbacks(monkeypatch):
    monkeypatch.setattr(export.codecs, "video_encoder", lambda codec: "h264_mf")
    args = export._intermediate_args()
    assert args[:2] == ["-c:v", "h264_mf"]
    assert "libx264" not in args
