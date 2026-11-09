from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.registry import targets_for
from umc.tools import ghostscript

needs_gs = pytest.mark.skipif(not ghostscript.is_available(), reason="Ghostscript not installed")

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">
<rect width="200" height="100" fill="none"/>
<rect x="10" y="10" width="80" height="80" fill="#ff0000"/>
<circle cx="150" cy="50" r="40" fill="#0000ff"/>
<text x="20" y="60" font-size="20">Hej</text></svg>"""

EPS = """%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 200 100
%%EndComments
1 0 0 setrgbcolor newpath 10 10 moveto 90 10 lineto 90 90 lineto 10 90 lineto closepath fill
0 0 1 setrgbcolor newpath 150 50 40 0 360 arc fill
/Helvetica findfont 18 scalefont setfont 0 setgray 20 40 moveto (EPS test) show
showpage
%%EOF
"""


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("vector")
    (d / "logo.svg").write_text(SVG, encoding="utf-8")
    (d / "art.eps").write_text(EPS, encoding="ascii")
    with pymupdf.open() as doc:  # Illustrator 9+ files are PDFs inside
        page = doc.new_page(width=300, height=150)
        page.draw_rect(pymupdf.Rect(10, 10, 140, 140), color=(0, 0.6, 0), fill=(0, 0.6, 0))
        page.insert_text((160, 80), "Illustrator", fontsize=20)
        doc.save(d / "poster.ai")
    img = Image.new("RGB", (300, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, 120, 180), fill="red")
    draw.ellipse((150, 40, 280, 170), fill="navy")
    img.save(d / "shapes.png")
    return {
        "svg": d / "logo.svg",
        "eps": d / "art.eps",
        "ai": d / "poster.ai",
        "png": d / "shapes.png",
    }


def _colour_at(path: Path, xy: tuple[float, float]) -> tuple[int, ...]:
    img = Image.open(path).convert("RGBA")
    return img.getpixel((int(xy[0] * img.width), int(xy[1] * img.height)))


def test_detection_and_targets(files):
    for ext in ("svg", "eps", "ai"):
        assert detect(files[ext]).family == MediaFamily.VECTOR
    ids = {t.id for t in targets_for([detect(files["svg"])])}
    assert {"eps", "ps", "vec:pdf", "vec:png", "vec:jpg"} <= ids and "svg" not in ids
    assert "trace:svg" in {t.id for t in targets_for([detect(files["png"])])}


def test_svg_to_png_keeps_size_and_transparency(files, tmp_path):
    [png] = convert([files["svg"]], "vec:png", tmp_path)
    img = Image.open(png)
    assert img.size == (200, 100) and img.mode == "RGBA"
    assert _colour_at(png, (0.25, 0.5))[:3] == (255, 0, 0)
    assert _colour_at(png, (0.75, 0.5))[:3] == (0, 0, 255)
    assert _colour_at(png, (0.98, 0.02))[3] == 0  # transparent corner
    [big] = convert([files["svg"]], "vec:jpg", tmp_path / "big", width_px=1000)
    assert Image.open(big).size == (1000, 500)


def test_svg_to_pdf_is_vector(files, tmp_path):
    [out] = convert([files["svg"]], "vec:pdf", tmp_path)
    with pymupdf.open(out) as doc:
        assert doc[0].rect.width == pytest.approx(200)
        assert doc[0].get_drawings()  # real shapes, not a picture
        assert not doc[0].get_images()


def test_ai_to_svg_and_png(files, tmp_path):
    [svg] = convert([files["ai"]], "svg", tmp_path)
    text = svg.read_text(encoding="utf-8")
    assert text.startswith("<svg") and "<path" in text
    [kept] = convert([files["ai"]], "svg", tmp_path / "t", text_as_paths=False)
    assert "Illustrator" in kept.read_text(encoding="utf-8")
    [png] = convert([files["ai"]], "vec:png", tmp_path)
    assert Image.open(png).size == (625, 313)  # 300 × 150 pt at 150 DPI (312.5 rounds up)


def test_pdf_pages_to_svg(media, tmp_path):
    outs = convert([media / "three.pdf"], "pages:svg", tmp_path)
    assert [p.name for p in outs] == ["three_1.svg", "three_2.svg", "three_3.svg"]


@needs_gs
def test_eps_to_pdf_png_and_svg_to_eps(files, tmp_path):
    [pdf_out] = convert([files["eps"]], "vec:pdf", tmp_path)
    with pymupdf.open(pdf_out) as doc:
        assert doc[0].rect.width == pytest.approx(200, abs=1)  # cropped to the bounding box
        assert "EPS test" in doc[0].get_text()
    [png] = convert([files["eps"]], "vec:png", tmp_path, width_px=400)
    assert _colour_at(png, (0.25, 0.5))[:3] == (255, 0, 0)
    [eps] = convert([files["svg"]], "eps", tmp_path)
    head = eps.read_bytes()[:40]
    assert head.startswith(b"%!PS-Adobe") and b"EPSF" in head
    [ps] = convert([files["ai"]], "ps", tmp_path)
    assert ps.read_bytes().startswith(b"%!PS")


def test_eps_without_ghostscript_asks_to_install(files, tmp_path, monkeypatch):
    from umc.core.errors import ToolMissingError

    monkeypatch.setattr(ghostscript, "gs_path", lambda: None)
    with pytest.raises(ToolMissingError) as info:
        convert([files["eps"]], "vec:pdf", tmp_path)
    assert info.value.tool == "Ghostscript"


@pytest.mark.parametrize("mode", ["color", "binary"])
def test_trace_image_to_svg(files, tmp_path, mode):
    [svg] = convert([files["png"]], "trace:svg", tmp_path, colour_mode=mode)
    text = svg.read_text(encoding="utf-8")
    assert text.count("<path") >= (2 if mode == "color" else 1)  # B&W: one compound path
    # render the trace back and compare with the original
    [png] = convert([svg], "vec:png", tmp_path / "back")
    expected = (255, 0, 0) if mode == "color" else (0, 0, 0)
    assert _colour_at(png, (70 / 300, 0.5))[:3] == expected


def test_trace_shrinks_big_images_but_keeps_display_size(files, tmp_path):
    big = tmp_path / "big.png"
    Image.open(files["png"]).resize((3000, 2000)).save(big)
    [svg] = convert([big], "trace:svg", tmp_path / "o", max_size=600)
    head = svg.read_text(encoding="utf-8")[:300]
    assert 'width="3000" height="2000" viewBox="0 0 600 400"' in head
