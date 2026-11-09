import pymupdf
import pytest

from tests.conftest import convert, needs_pandoc, needs_soffice


def test_pdf_page_range_to_pdf(media, tmp_path):
    [out] = convert([media / "three.pdf"], "doc:pdf", tmp_path, page_range="2-3")
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        assert "Page 2" in doc[0].get_text()


def test_pdf_to_txt(media, tmp_path):
    [out] = convert([media / "three.pdf"], "doc:txt", tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "Page 1" in text and "Page 3" in text


@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_pdf_to_images(media, tmp_path, fmt):
    [folder] = convert([media / "three.pdf"], f"pages:{fmt}", tmp_path, dpi=72)
    assert len(list(folder.glob(f"*.{fmt}"))) == 3


def test_pdf_to_multipage_tiff(media, tmp_path):
    from PIL import Image

    [out] = convert([media / "three.pdf"], "pages:tiff", tmp_path, dpi=50, multipage_tiff=True)
    assert Image.open(out).n_frames == 3


@needs_pandoc
@pytest.mark.parametrize("fmt", ["docx", "html", "epub", "odt"])
def test_markdown_via_pandoc(media, tmp_path, fmt):
    [out] = convert([media / "doc.md"], f"doc:{fmt}", tmp_path)
    assert out.stat().st_size > 0


@needs_soffice
@needs_pandoc
def test_markdown_to_pdf_multi_step(media, tmp_path):
    [out] = convert([media / "doc.md"], "doc:pdf", tmp_path)
    with pymupdf.open(out) as doc:
        assert "Hello" in doc[0].get_text()


@needs_soffice
def test_txt_to_docx_and_doc(media, tmp_path):
    [docx] = convert([media / "notes.txt"], "doc:docx", tmp_path)
    [doc] = convert([docx], "doc:doc", tmp_path)
    assert doc.suffix == ".doc" and doc.stat().st_size > 0


@needs_soffice
def test_csv_to_ods_via_libreoffice(media, tmp_path):
    # CSV → XLSX is handled by the Data group (no LibreOffice); ODS still goes via LibreOffice.
    [out] = convert([media / "data.csv"], "doc:ods", tmp_path)
    assert out.stat().st_size > 0


@needs_soffice
def test_word_to_images(media, tmp_path):
    [docx] = convert([media / "notes.txt"], "doc:docx", tmp_path)
    [folder] = convert([docx], "pages:png", tmp_path / "pages", dpi=50)
    assert list(folder.glob("*.png"))


def test_missing_libreoffice_raises_tool_missing(media, tmp_path, monkeypatch):
    from umc.core.errors import ToolMissingError
    from umc.tools import libreoffice

    monkeypatch.setattr(libreoffice, "soffice_path", lambda: None)
    with pytest.raises(ToolMissingError) as info:
        convert([media / "notes.txt"], "doc:doc", tmp_path)
    assert info.value.tool == "LibreOffice"
    assert "libreoffice.org" in info.value.download_url
