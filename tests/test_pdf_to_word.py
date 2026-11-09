import io

import pymupdf
import pytest
from docx import Document
from PIL import Image

from tests.conftest import convert
from umc.core.errors import ConversionError


@pytest.fixture(scope="module")
def report_pdf(tmp_path_factory):
    """A page with a heading, a paragraph, a 3×2 ruled table and a picture."""
    path = tmp_path_factory.mktemp("p2w") / "report.pdf"
    png = io.BytesIO()
    Image.new("RGB", (120, 80), "teal").save(png, "PNG")
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 80), "Quarterly report", fontsize=22)
        page.insert_text((72, 110), "Sales grew in every region this quarter.", fontsize=11)
        x0, y0, cw, rh = 72, 140, 150, 24
        rows = [("Region", "Sales"), ("North", "120"), ("South", "95")]
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
                page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                page.insert_text((rect.x0 + 6, rect.y0 + 16), cell, fontsize=11)
        page.insert_image(pymupdf.Rect(72, 260, 192, 340), stream=png.getvalue())
        doc.save(path)
    return path


def test_pdf_to_docx_keeps_text_table_and_picture(report_pdf, tmp_path):
    [out] = convert([report_pdf], "doc:docx", tmp_path)
    doc = Document(out)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Quarterly report" in text and "Sales grew" in text
    assert len(doc.tables) == 1
    cells = [[c.text.strip() for c in row.cells] for row in doc.tables[0].rows]
    assert cells == [["Region", "Sales"], ["North", "120"], ["South", "95"]]
    assert doc.inline_shapes or doc.part.package.image_parts  # the picture came along


def test_scanned_pdf_points_to_ocr(tmp_path):
    scan = tmp_path / "scan.pdf"
    png = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(png, "PNG")
    with pymupdf.open() as doc:
        doc.new_page().insert_image(pymupdf.Rect(0, 0, 300, 200), stream=png.getvalue())
        doc.save(scan)
    with pytest.raises(ConversionError, match="OCR"):
        convert([scan], "doc:docx", tmp_path / "o")
    # Text and Markdown used to save an empty file instead.
    for fmt in ("txt", "md"):
        with pytest.raises(ConversionError, match="OCR"):
            convert([scan], f"doc:{fmt}", tmp_path / fmt)
        assert not list((tmp_path / fmt).glob(f"*.{fmt}"))
