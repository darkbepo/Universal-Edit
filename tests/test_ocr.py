import pymupdf
import pytest

from tests.conftest import convert
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.registry import targets_for
from umc.core.samples import SCAN_TEXT, write_test_scan
from umc.tools import tessdata


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    return write_test_scan(tmp_path_factory.mktemp("ocr") / "scan.png")


@pytest.fixture(scope="module")
def scanned_pdf(scan, tmp_path_factory):
    """A two-page PDF: a scanned page (image only) and a page with real text."""
    d = tmp_path_factory.mktemp("ocrpdf")
    [img_pdf] = convert([scan], "images:pdf", d, dpi=300)
    doc = pymupdf.open(img_pdf)
    page = doc.new_page()
    page.insert_text((72, 72), "Real text page", fontsize=14)
    out = d / "mixed.pdf"
    doc.save(out)
    return out


def _words(text: str) -> str:
    return " ".join(text.split())


def test_targets_offered_for_images_and_pdfs(scan, scanned_pdf):
    for f in (scan, scanned_pdf):
        ids = {t.id for t in targets_for([detect(f)])}
        assert {"ocr:pdf", "ocr:txt"} <= ids


def test_image_to_text(scan, tmp_path):
    [out] = convert([scan], "ocr:txt", tmp_path)
    text = _words(out.read_text(encoding="utf-8"))
    for line in SCAN_TEXT:
        assert line in text


def test_image_to_searchable_pdf(scan, tmp_path):
    [out] = convert([scan], "ocr:pdf", tmp_path)
    assert out.name == "scan_ocr.pdf"
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        assert SCAN_TEXT[0] in _words(doc[0].get_text())
        assert doc[0].get_images()  # the scan itself is kept
        # page size follows the image's 300 DPI: 1400 px = 4.67 in = 336 pt
        assert round(doc[0].rect.width) == 336


def test_scanned_pdf_keeps_text_pages(scanned_pdf, tmp_path):
    [out] = convert([scanned_pdf], "ocr:pdf", tmp_path)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        assert SCAN_TEXT[1] in _words(doc[0].get_text())
        assert "Real text page" in doc[1].get_text()
        assert not doc[1].get_images()  # copied as is, not re-rendered


def test_pdf_page_range_to_text(scanned_pdf, tmp_path):
    [out] = convert([scanned_pdf], "ocr:txt", tmp_path, page_range="1")
    text = out.read_text(encoding="utf-8")
    assert SCAN_TEXT[0] in _words(text) and "Real text" not in text


def test_combine_images(scan, tmp_path):
    second = write_test_scan(tmp_path / "second.png", ("PAGE TWO",))
    [out] = convert([scan, second], "ocr:pdf", tmp_path)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        assert "PAGE TWO" in doc[1].get_text()
    outs = convert([scan, second], "ocr:txt", tmp_path / "separate", combine=False)
    assert len(outs) == 2


def test_damaged_language_download_is_rejected(tmp_path, monkeypatch):
    import io
    import threading

    from umc.tools import installer

    class FakeResponse(io.BytesIO):
        headers: dict = {}

    monkeypatch.setenv("UMC_TOOLS_DIR", str(tmp_path))
    monkeypatch.setattr(tessdata, "bundled_dir", lambda: tmp_path / "no-bundle")
    monkeypatch.setattr(installer, "_get", lambda url, timeout=30: FakeResponse(b"not tessdata"))
    with pytest.raises(ConversionError, match="checksum"):
        tessdata.ensure(["dan"], lambda f, m: None, threading.Event())
    assert not list((tmp_path / "tessdata").iterdir())


def test_bundled_english_is_copied_without_download(tmp_path, monkeypatch):
    import threading

    from umc.tools import installer

    if not (tessdata.bundled_dir() / "eng.traineddata").is_file():
        pytest.skip("run scripts/fetch_tessdata.py first")
    monkeypatch.setenv("UMC_TOOLS_DIR", str(tmp_path))
    monkeypatch.setattr(installer, "download", lambda *a, **k: pytest.fail("downloaded"))
    folder = tessdata.ensure(["eng"], lambda f, m: None, threading.Event())
    assert (tmp_path / "tessdata" / "eng.traineddata").is_file() and folder
