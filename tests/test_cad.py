from pathlib import Path

import ezdxf
import pymupdf
import pytest
from PIL import Image

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.errors import ToolMissingError
from umc.core.registry import targets_for
from umc.tools import libredwg


@pytest.fixture(scope="module")
def plan_dxf(tmp_path_factory) -> Path:
    """A 200 × 100 floor plan: red walls layer, blue circle, text, plus one paper layout."""
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("WALLS", color=1)  # red
    doc.layers.add("FURNITURE", color=5)  # blue
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (200, 0), (200, 100), (0, 100)], close=True, dxfattribs={"layer": "WALLS"}
    )
    msp.add_circle((150, 50), 30, dxfattribs={"layer": "FURNITURE"})
    msp.add_text("Kitchen", height=8, dxfattribs={"layer": "0"}).set_placement((20, 40))
    sheet = doc.layouts.new("Sheet 1")
    sheet.add_line((10, 10), (100, 10))
    path = tmp_path_factory.mktemp("cad") / "plan.dxf"
    doc.saveas(path)
    return path


def test_detect_and_targets(plan_dxf):
    f = detect(plan_dxf)
    assert f.family == MediaFamily.CAD
    assert {t.id for t in targets_for([f])} >= {"dxf", "cad:pdf", "cad:svg", "cad:png", "cad:jpg"}


def test_dxf_to_pdf_is_vector_and_fits_a3(plan_dxf, tmp_path):
    [pdf] = convert([plan_dxf], "cad:pdf", tmp_path, paper="a3")
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        assert (
            round(page.rect.width / 72 * 25.4) == 420 and round(page.rect.height / 72 * 25.4) == 297
        )
        assert page.get_drawings() and not page.get_images()
        assert "Kitchen" in page.get_text() or page.get_drawings()  # text may be drawn as outlines


def test_dxf_to_png_colour_modes(plan_dxf, tmp_path):
    [dark] = convert([plan_dxf], "cad:png", tmp_path / "d", colours="colour_dark", dpi=100)
    img = Image.open(dark).convert("RGB")
    assert img.getpixel((2, 2)) == (0, 0, 0)  # black background like the CAD screen
    colours = {c for _, c in img.getcolors(1 << 20)}
    assert any(r > 200 and g < 60 and b < 60 for r, g, b in colours)  # red walls
    assert any(b > 200 and r < 60 for r, g, b in colours)  # blue circle
    [print_] = convert([plan_dxf], "cad:jpg", tmp_path / "p", colours="black_on_white", dpi=100)
    img = Image.open(print_).convert("RGB")
    assert img.getpixel((2, 2))[0] > 240
    assert not any(r > 200 and g < 60 and b < 60 for _, (r, g, b) in img.getcolors(1 << 20))


def test_dxf_to_svg_and_paper_layouts(plan_dxf, tmp_path):
    [svg] = convert([plan_dxf], "cad:svg", tmp_path)
    assert svg.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    outs = convert([plan_dxf], "cad:png", tmp_path / "l", layouts="paper")
    assert [p.name for p in outs] == ["plan.png"]  # one non-empty paper layout


@pytest.mark.parametrize("version", ["R2000", "R12"])
def test_save_as_other_dxf_version(plan_dxf, tmp_path, version):
    [out] = convert([plan_dxf], "dxf", tmp_path, dxf_version=version)
    doc = ezdxf.readfile(out)
    assert doc.dxfversion == ezdxf.const.acad_release_to_dxf_version[version]
    assert len(doc.modelspace().query("CIRCLE")) == 1


def test_dwg_without_libredwg_asks_to_install(tmp_path, monkeypatch):
    dwg = tmp_path / "house.dwg"
    dwg.write_bytes(b"AC1032" + b"\0" * 100)
    monkeypatch.setattr(libredwg, "dwg2dxf_path", lambda: None)
    with pytest.raises(ToolMissingError) as info:
        convert([dwg], "cad:pdf", tmp_path / "o")
    assert info.value.tool == "LibreDWG"


def _zero_handles(path: Path) -> None:
    """Give every ENDBLK handle 0, like LibreDWG's dwg2dxf does."""
    import re

    text = path.read_text()
    text, n = re.subn(r"(ENDBLK\n  5\n)[0-9A-F]+\n", r"\g<1>0\n", text)
    assert n > 1
    path.write_text(text)


def test_reads_dxf_with_zero_handles(plan_dxf, tmp_path):
    src = tmp_path / "zero.dxf"
    src.write_bytes(plan_dxf.read_bytes())
    _zero_handles(src)
    [png] = convert([src], "cad:png", tmp_path / "o", dpi=50)
    assert Image.open(png).size[0] > 0
    [out] = convert([src], "dxf", tmp_path / "d")
    doc = ezdxf.readfile(out)  # the saved copy has proper handles
    assert len(doc.modelspace()) == len(ezdxf.readfile(plan_dxf).modelspace())


def test_fix_zero_handles_numbers_above_the_highest(tmp_path):
    from umc.converters.cad import fix_zero_handles

    src, dest = tmp_path / "a.dxf", tmp_path / "b.dxf"
    src.write_bytes(
        b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  9\r\n$HANDSEED\r\n  5\r\n20\r\n  0\r\nENDSEC\r\n"
        b"  0\r\nENDBLK\r\n  5\r\n0\r\n  0\r\nLINE\r\n  5\r\n1F\r\n  0\r\nENDBLK\r\n  5\r\n0\r\n"
    )
    assert fix_zero_handles(src, dest)
    out = dest.read_bytes().split(b"\r\n")
    assert out[out.index(b"$HANDSEED") + 2] == b"23"
    handles = [out[i + 1] for i, v in enumerate(out) if v == b"  5"]
    assert handles == [b"23", b"21", b"1F", b"22"]
    assert not fix_zero_handles(dest, tmp_path / "c.dxf")
