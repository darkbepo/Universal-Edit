import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import convert, needs_ffmpeg
from umc.converters.subtitles import SubtitleOptions, decode, guess_language, restyle
from umc.core.detect import MediaFamily, detect
from umc.core.registry import targets_for

pytestmark = needs_ffmpeg

SRT = (
    "1\n00:00:00,200 --> 00:00:01,500\nHej Æble ØL Å\n\n"
    "2\n00:00:01,600 --> 00:00:01,900\nSecond line\n"
)


@pytest.fixture(scope="module")
def subs(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("subs")
    utf8 = d / "movie.da.srt"
    utf8.write_text(SRT, encoding="utf-8")
    legacy = d / "old.srt"
    legacy.write_bytes(SRT.encode("cp1252"))  # not UTF-8
    return {"utf8": utf8, "legacy": legacy}


def _ffprobe(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)["streams"]


def test_detection_and_targets(subs, media):
    f = detect(subs["utf8"])
    assert f.family == MediaFamily.SUBTITLE
    ids = {t.id for t in targets_for([f])}
    assert {"vtt", "ass", "ttml", "lrc"} <= ids and "srt" not in ids
    mixed = {t.id for t in targets_for([detect(media / "clip.mp4"), f])}
    assert {"burn:mp4", "subtrack:mkv", "subtrack:mp4"} <= mixed
    two = {t.id for t in targets_for([detect(media / "clip.mp4"), f, detect(subs["legacy"])])}
    assert "burn:mp4" not in two and "subtrack:mkv" in two  # burning takes one file


def test_decode_and_language():
    assert decode("ÆØÅ".encode("cp1252"), "windows-1252") == "ÆØÅ"
    assert decode(b"\xef\xbb\xbfhi", "windows-1252") == "hi"
    assert guess_language(Path("movie.da.srt")) == "dan"  # 2-letter → ISO 639-2
    assert guess_language(Path("Movie.eng.forced.srt")) == "eng"
    assert guess_language(Path("movie.srt")) == "und"


@pytest.mark.parametrize("fmt", ["vtt", "ass", "ttml", "lrc"])
def test_srt_to_formats(subs, tmp_path, fmt):
    [out] = convert([subs["legacy"]], fmt, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "Æble" in text  # Windows-1252 file was decoded correctly
    if fmt == "vtt":
        assert text.startswith("WEBVTT") and "00.200 --> 00:01.500" in text  # hours optional


def test_shift_and_back(subs, tmp_path):
    [vtt] = convert([subs["utf8"]], "vtt", tmp_path, shift=2.5)
    assert "02.700 --> 00:04.000" in vtt.read_text(encoding="utf-8")
    [srt] = convert([vtt], "srt", tmp_path / "b", shift=-2.5)
    assert "00:00:00,200 --> 00:00:01,500" in srt.read_text(encoding="utf-8")


def test_ass_style(subs, tmp_path):
    [ass] = convert([subs["utf8"]], "ass", tmp_path, font_size=60, colour="#ffcc00", position="top")
    style = next(
        line
        for line in ass.read_text(encoding="utf-8").splitlines()
        if line.startswith("Style: Default")
    )
    assert "DejaVu Sans,60,&H0000CCFF" in style
    assert style.split(",")[18] == "8"  # alignment = top centre


def test_restyle_leaves_other_ass_styles_alone():
    ass = (
        "[Script Info]\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
        "Style: Sign,Arial,20\nStyle: Default,Arial,20\n"
    )
    out = restyle(ass, SubtitleOptions(font_size=33))
    assert "Style: Sign,Arial,20" in out and "Style: Default,DejaVu Sans,33" in out
    assert "PlayResY: 1080" in out


def test_add_tracks_and_extract_them(subs, media, tmp_path):
    [mkv] = convert([media / "clip.mp4", subs["utf8"], subs["legacy"]], "subtrack:mkv", tmp_path)
    s = [x for x in _ffprobe(mkv) if x["codec_type"] == "subtitle"]
    assert [x.get("tags", {}).get("language") for x in s] == ["dan", None]  # "und" isn't stored
    assert s[0]["disposition"]["default"] == 1 and s[1]["disposition"]["default"] == 0
    outs = convert([mkv], "subs:srt", tmp_path / "x")
    assert sorted(p.name for p in outs) == [
        "clip (with subtitles).dan.srt",
        "clip (with subtitles).track2.srt",  # no language → numbered
    ]
    assert "Æble" in outs[0].read_text(encoding="utf-8")
    [mp4] = convert([media / "clip.mp4", subs["utf8"]], "subtrack:mp4", tmp_path)
    assert any(x["codec_name"] == "mov_text" for x in _ffprobe(mp4))


def test_extract_without_subtitles_explains(media, tmp_path):
    from umc.core.errors import ConversionError

    with pytest.raises(ConversionError, match="no text subtitles"):
        convert([media / "clip.mp4"], "subs:srt", tmp_path)


def test_burn_changes_only_the_subtitle_area(subs, media, tmp_path):
    from PIL import Image, ImageChops

    [burned] = convert(
        [media / "clip.mp4", subs["utf8"]], "burn:mp4", tmp_path, font_size=80, colour="#ff00ff"
    )
    assert burned.name == "clip (subtitled).mp4"

    def frame(video: Path, name: str) -> Image.Image:
        png = tmp_path / name
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                "1",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(png),
            ],
            check=True,
        )
        return Image.open(png).convert("RGB")

    a, b = frame(media / "clip.mp4", "a.png"), frame(burned, "b.png")
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 60 else 0)
    w, h = diff.size
    bottom = diff.crop((0, h // 2, w, h)).histogram()[255]
    top = diff.crop((0, 0, w, h // 3)).histogram()[255]
    assert bottom > 300 and top < 50
