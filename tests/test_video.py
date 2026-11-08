import pytest
from PIL import Image

from tests.conftest import convert, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.mark.parametrize("fmt", ["mkv", "webm", "mov", "avi"])
def test_video_to_video(media, tmp_path, fmt):
    from umc.tools.ffmpeg import probe

    [out] = convert([media / "clip.mp4"], fmt, tmp_path, crf=35, preset="ultrafast")
    info = probe(out)
    assert info.has_video and abs(info.duration - 2.0) < 0.2


def test_resize_trim_and_speed(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [out] = convert(
        [media / "clip.mp4"],
        "mp4",
        tmp_path,
        resolution=120,
        start="0.5",
        speed=2.0,
        preset="ultrafast",
    )
    info = probe(out)
    assert info.height == 120
    assert abs(info.duration - 0.75) < 0.15


def test_remove_audio(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [out] = convert([media / "clip.mp4"], "mkv", tmp_path, remove_audio=True, preset="ultrafast")
    assert not probe(out).has_audio


def test_incompatible_codec_gives_friendly_error(media, tmp_path):
    from umc.core.errors import ConversionError

    with pytest.raises(ConversionError, match="can't be saved"):
        convert([media / "clip.mp4"], "webm", tmp_path, video_codec="h264")


@pytest.mark.parametrize("per_30, expected", [(30, 60), (10, 20), (1, 2)])
def test_frames_per_30(media, tmp_path, per_30, expected):
    [folder] = convert([media / "clip.mp4"], "frames:jpg", tmp_path, frames_per_30=per_30)
    assert folder.is_dir()
    assert len(list(folder.glob("*.jpg"))) == expected


def test_video_to_gif(media, tmp_path):
    [out] = convert([media / "clip.mp4"], "video:gif", tmp_path, fps=10, width=160)
    im = Image.open(out)
    assert im.size[0] == 160 and im.n_frames >= 18


def test_extract_audio(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [out] = convert([media / "clip.mp4"], "audio:mp3", tmp_path)
    info = probe(out)
    assert info.has_audio and not info.has_video


def test_cancel_stops_conversion(media, tmp_path):
    import threading

    from umc.core import options as opts
    from umc.core.detect import detect
    from umc.core.errors import ConversionCancelled
    from umc.core.job import ConversionJob
    from umc.core.registry import targets_for

    files = [detect(media / "clip.mp4")]
    target = next(t for t in targets_for(files) if t.id == "mkv")
    ev = threading.Event()
    ev.set()
    job = ConversionJob(
        files=files,
        target=target,
        output_dir=tmp_path,
        cancel_event=ev,
        options=opts.build(target.converter.options_cls),
    )
    with pytest.raises(ConversionCancelled):
        target.converter.run(job)
    assert not list(tmp_path.iterdir())
