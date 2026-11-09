import pytest

from tests.conftest import convert, needs_ffmpeg
from umc.core.detect import MediaFamily, detect

pytestmark = needs_ffmpeg


def test_audio_is_detected(media):
    d = detect(media / "tone.wav")
    assert d.family is MediaFamily.AUDIO and d.ext == "wav"


def test_audio_detected_by_content(media, tmp_path):
    renamed = tmp_path / "mystery"
    renamed.write_bytes((media / "tone.wav").read_bytes())
    assert detect(renamed).ext == "wav"


def test_audio_only_is_not_offered_for_a_silent_video(media, tmp_path):
    import subprocess

    from umc.core.registry import targets_for

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=duration=1:size=160x120:rate=10", "-pix_fmt", "yuv420p", str(silent)],
        check=True,
    )  # fmt: skip
    silent_ids = {t.id for t in targets_for([detect(silent)])}
    assert "mp4" in silent_ids and not any(i.startswith("audio:") for i in silent_ids)
    assert "audio:mp3" in {t.id for t in targets_for([detect(media / "clip.mp4")])}
    assert "audio:mp3" not in {
        t.id for t in targets_for([detect(media / "clip.mp4"), detect(silent)])
    }


def test_audio_targets(media):
    from umc.core.registry import targets_for

    ids = {t.id for t in targets_for([detect(media / "tone.wav")])}
    assert {"mp3", "m4a", "flac", "ogg", "opus", "wav", "aiff", "wma"} <= ids
    assert not any(i.startswith(("slideshow", "frames")) for i in ids)


@pytest.mark.parametrize("fmt", ["mp3", "m4a", "flac", "ogg", "opus", "aiff", "wma", "ac3"])
def test_wav_to_every_format(media, tmp_path, fmt):
    from umc.tools.ffmpeg import probe

    [out] = convert([media / "tone.wav"], fmt, tmp_path)
    info = probe(out)
    assert info.has_audio and not info.has_video
    assert abs(info.duration - 3.0) < 0.2


def test_audio_settings(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [mp3] = convert([media / "tone.wav"], "mp3", tmp_path, bitrate=128)
    [out] = convert([mp3], "flac", tmp_path, channels=1, sample_rate=22050, start="1", end="2")
    info = probe(out)
    assert abs(info.duration - 1.0) < 0.15
    import subprocess

    meta = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=channels,sample_rate",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
    ).stdout
    assert meta.strip() == "22050,1"
