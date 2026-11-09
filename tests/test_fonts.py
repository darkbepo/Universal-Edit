from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.errors import ConversionError
from umc.core.registry import targets_for
from umc.core.samples import write_test_font


@pytest.fixture(scope="module")
def ttf(tmp_path_factory) -> Path:
    return write_test_font(tmp_path_factory.mktemp("fonts") / "UMC Test.ttf")


def _has_liga(font: TTFont) -> bool:
    gsub = font["GSUB"].table
    return any(fr.FeatureTag == "liga" for fr in gsub.FeatureList.FeatureRecord)


def test_detect_and_targets(ttf, tmp_path):
    f = detect(ttf)
    assert f.family == MediaFamily.FONT
    assert {t.id for t in targets_for([f]) if t.group == "Font"} == {"otf", "woff", "woff2"}
    (tmp_path / "noext").write_bytes(ttf.read_bytes())
    assert detect(tmp_path / "noext").ext == "ttf"


@pytest.mark.parametrize("fmt", ["woff", "woff2"])
def test_web_fonts_keep_everything(ttf, tmp_path, fmt):
    [out] = convert([ttf], fmt, tmp_path)
    assert out.read_bytes()[:4] == (b"wOFF" if fmt == "woff" else b"wOF2")
    font = TTFont(out)
    assert font.flavor == fmt and "glyf" in font and _has_liga(font)
    assert font.getGlyphOrder() == TTFont(ttf).getGlyphOrder()
    [back] = convert([out], "ttf", tmp_path / "back")
    assert sorted(TTFont(back).keys()) == sorted(TTFont(ttf).keys())


def test_ttf_to_otf_and_back(ttf, tmp_path):
    [otf] = convert([ttf], "otf", tmp_path)
    font = TTFont(otf)
    assert otf.read_bytes()[:4] == b"OTTO" and "CFF " in font and "glyf" not in font
    assert _has_liga(font) and font["hmtx"]["A"][0] == 600
    # the CFF outline covers the same area as the TrueType one
    from fontTools.pens.boundsPen import BoundsPen

    def bounds(f: TTFont, name: str):
        pen = BoundsPen(f.getGlyphSet())
        f.getGlyphSet()[name].draw(pen)
        return tuple(round(v) for v in pen.bounds)

    def close(a, b):  # control points are rounded to whole font units: allow 1 unit in 1000
        return all(abs(x - y) <= 1 for x, y in zip(a, b, strict=True))

    src = TTFont(ttf)
    assert bounds(src, "A") == (50, 0, 550, 450)  # the curve's peak
    assert close(bounds(font, "A"), bounds(src, "A"))
    [back] = convert([otf], "ttf", tmp_path / "back")
    tt = TTFont(back)
    assert "glyf" in tt and "CFF " not in tt and tt["maxp"].tableVersion == 0x00010000
    assert close(bounds(tt, "A"), (50, 0, 550, 450)) and _has_liga(tt)
    [woff2] = convert([otf], "woff2", tmp_path / "w")
    assert "CFF " in TTFont(woff2)  # WOFF2 keeps the CFF outlines


def test_subset_latin_and_custom(ttf, tmp_path):
    [custom] = convert([ttf], "woff2", tmp_path, subset="custom", custom_text="fi")
    font = TTFont(custom)
    cmap = font.getBestCmap()
    assert set(cmap) == {0x20, 0x66, 0x69}  # space is always kept
    assert "f_i" in font.getGlyphOrder() and "A" not in font.getGlyphOrder()  # ligature follows
    [latin] = convert([ttf], "woff", tmp_path / "l", subset="latin")
    assert set(TTFont(latin).getBestCmap()) == {0x20, 0x41, 0x42, 0x66, 0x69}
    with pytest.raises(ConversionError, match="Type the characters"):
        convert([ttf], "woff", tmp_path / "x", subset="custom", custom_text="  ")


def test_bad_font_has_friendly_error(tmp_path):
    bad = tmp_path / "broken.ttf"
    bad.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 40)
    with pytest.raises(ConversionError, match="valid font"):
        convert([bad], "woff2", tmp_path / "o")
