import pymupdf
import pytest

from tests.conftest import convert, needs_soffice
from umc.core.errors import ConversionError


def _pages(path):
    with pymupdf.open(path) as doc:
        return [p.get_text().strip() for p in doc]


def test_split_every_page(media, tmp_path):
    [folder] = convert([media / "three.pdf"], "split:pdf", tmp_path, mode="every")
    files = sorted(folder.glob("*.pdf"))
    assert [f.name for f in files] == ["three_page_1.pdf", "three_page_2.pdf", "three_page_3.pdf"]
    assert _pages(files[1]) == ["Page 2"]


def test_split_chunks(media, tmp_path):
    [folder] = convert(
        [media / "three.pdf"], "split:pdf", tmp_path, mode="chunks", pages_per_document=2
    )
    files = sorted(folder.glob("*.pdf"))
    assert [f.name for f in files] == ["three_page_3.pdf", "three_pages_1-2.pdf"]
    assert _pages(folder / "three_pages_1-2.pdf") == ["Page 1", "Page 2"]


def test_split_custom_ranges_with_part_names(media, tmp_path):
    [folder] = convert(
        [media / "three.pdf"], "split:pdf", tmp_path, mode="ranges", ranges="1, 2-", naming="part"
    )
    assert _pages(folder / "three_part_01.pdf") == ["Page 1"]
    assert _pages(folder / "three_part_02.pdf") == ["Page 2", "Page 3"]


def test_split_without_subfolder(media, tmp_path):
    convert([media / "three.pdf"], "split:pdf", tmp_path, subfolder=False)
    assert len(list(tmp_path.glob("three_page_*.pdf"))) == 3


def test_invalid_range(media, tmp_path):
    with pytest.raises(ConversionError):
        convert([media / "three.pdf"], "split:pdf", tmp_path, mode="ranges", ranges="abc")


@needs_soffice
def test_split_word_document(media, tmp_path):
    [docx] = convert([media / "notes.txt"], "doc:docx", tmp_path)
    [folder] = convert([docx], "split:pdf", tmp_path / "split")
    assert list(folder.glob("*.pdf"))


@pytest.mark.parametrize(
    "paper, orientation, size",
    [
        ("a4", "portrait", (595, 842)),
        ("letter", "landscape", (792, 612)),
    ],
)
def test_pdf_page_size(media, tmp_path, paper, orientation, size):
    [out] = convert(
        [media / "three.pdf"],
        "doc:pdf",
        tmp_path,
        paper=paper,
        orientation=orientation,
        margin_mm=20,
    )
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        w, h = doc[0].rect.width, doc[0].rect.height
        assert (round(w), round(h)) == size
        assert "Page 1" in doc[0].get_text()
