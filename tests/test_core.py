from pathlib import Path

import pytest

from umc.converters.document import plan
from umc.converters.video_frames import ffmpeg_pattern, select_expr
from umc.core import options as opts
from umc.core.detect import MediaFamily, detect
from umc.core.errors import ConversionError
from umc.core.job import file_fractions, unique_path
from umc.core.registry import targets_for
from umc.tools.pdf import parse_page_range


def test_detect_by_extension(media):
    assert detect(media / "img0.png").family is MediaFamily.IMAGE
    assert detect(media / "tall.jpg").ext == "jpg"
    assert detect(media / "doc.md").family is MediaFamily.DOCUMENT


def test_detect_by_magic_bytes(media, tmp_path):
    renamed = tmp_path / "mystery"
    renamed.write_bytes((media / "img0.png").read_bytes())
    d = detect(renamed)
    assert d is not None and d.ext == "png"


def test_unknown_file_is_rejected(tmp_path):
    p = tmp_path / "x.zzz"
    p.write_bytes(b"\x00\x01binary\x00")  # unknown text files are accepted as plain text now
    assert detect(p) is None


def test_mixed_families_can_only_be_packed(media):
    targets = targets_for([detect(media / "img0.png"), detect(media / "doc.md")])
    assert {t.id for t in targets} == {"pack:zip", "pack:7z", "pack:tar.gz"}


def test_unique_path(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert unique_path(tmp_path, "a", "txt").name == "a (1).txt"


def test_parse_time():
    assert opts.parse_time("90") == 90
    assert opts.parse_time("1:30") == 90
    assert opts.parse_time("01:00:01.5") == 3601.5
    assert opts.parse_time("") == 0
    with pytest.raises(ConversionError):
        opts.parse_time("abc")


def test_page_range():
    assert parse_page_range("all", 5) == [0, 1, 2, 3, 4]
    assert parse_page_range("1-2, 4", 5) == [0, 1, 3]
    assert parse_page_range("4-", 5) == [3, 4]
    with pytest.raises(ConversionError):
        parse_page_range("x", 5)


def test_select_expression_keeps_n_of_30():
    assert select_expr(30) == ""
    for n in (1, 3, 10, 15, 29):
        kept = [m for m in range(30) if (m + 1) * n // 30 - m * n // 30 == 1]
        assert len(kept) == n
        assert f"*{n}/30" in select_expr(n)


def test_ffmpeg_pattern():
    assert ffmpeg_pattern("{name}_{n:05d}", "clip") == "clip_%05d"
    assert ffmpeg_pattern("frame", "clip") == "frame_%05d"
    assert ffmpeg_pattern("{name}-{n}", "50%") == "50%%-%d"


def test_document_route_planning():
    all_engines = {"pymupdf": True, "pandoc": True, "libreoffice": True}
    assert [e.engine for e in plan("docx", "pdf", all_engines)] == ["libreoffice"]
    assert [e.engine for e in plan("md", "docx", all_engines)] == ["pandoc"]
    md_pdf = plan("md", "pdf", all_engines)
    assert md_pdf[0].engine == "pandoc" and md_pdf[-1].dst == "pdf"
    assert plan("pdf", "txt", all_engines)[0].engine == "pymupdf"
    # Without LibreOffice, prefer a route that avoids it where one exists.
    no_lo = {"pymupdf": True, "pandoc": True, "libreoffice": False}
    assert all(e.engine != "libreoffice" for e in plan("docx", "md", no_lo))


def test_every_target_has_an_options_schema(media):
    for name in ("img0.png", "doc.md", "three.pdf"):
        for t in targets_for([detect(media / name)]):
            assert opts.option_specs(t.converter.options_cls)
            assert opts.build(t.converter.options_cls, opts.defaults(t.converter.options_cls))


def test_image_targets_include_slideshow_and_pdf(media):
    ids = {t.id for t in targets_for([detect(media / f"img{i}.png") for i in range(3)])}
    assert {"jpg", "png", "webp", "slideshow:mp4", "slideshow:gif", "images:pdf"} <= ids
    single = {t.id for t in targets_for([detect(media / "img0.png")])}
    assert "slideshow:gif" not in single  # animated GIF slideshow needs 2+ images


def test_video_targets(media):
    if not (media / "clip.mp4").exists():
        pytest.skip("ffmpeg missing")
    ids = {t.id for t in targets_for([detect(media / "clip.mp4")])}
    assert {"mp4", "mkv", "webm", "video:gif", "frames:png", "audio:mp3"} <= ids


def test_paths(media):
    assert isinstance(media, Path)


def test_file_fractions_split_overall_progress_per_file():
    assert file_fractions(0.0, 3) == [0.0, 0.0, 0.0]
    assert file_fractions(0.5, 2) == [1.0, 0.0]
    assert [round(f, 2) for f in file_fractions(0.6, 4)] == [1.0, 1.0, 0.4, 0.0]
    assert file_fractions(1.0, 3) == [1.0, 1.0, 1.0]
    assert file_fractions(1.7, 1) == [1.0] and file_fractions(-1, 1) == [0.0]
    assert file_fractions(0.5, 0) == []
