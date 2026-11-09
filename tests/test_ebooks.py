import pytest

from tests.conftest import convert
from umc.converters.document import plan
from umc.core.detect import detect
from umc.core.errors import ToolMissingError
from umc.core.registry import targets_for
from umc.tools import calibre

needs_calibre = pytest.mark.skipif(not calibre.is_available(), reason="Calibre not installed")
ALL = {"pymupdf": True, "pandoc": True, "libreoffice": True, "pdf2docx": True, "calibre": True}

BOOK = "# Chapter 1\n\nHej ÆØÅ — an e-book.\n\n# Chapter 2\n\nThe end.\n"


@pytest.fixture
def book_md(tmp_path):
    p = tmp_path / "novel.md"
    p.write_text(BOOK, encoding="utf-8")
    return p


def test_kindle_targets_and_routes(book_md):
    ids = {t.id for t in targets_for([detect(book_md)])}
    assert {"doc:mobi", "doc:azw3", "doc:epub", "doc:fb2"} <= ids
    assert [e.engine for e in plan("epub", "azw3", ALL)] == ["calibre"]
    assert [e.engine for e in plan("pdf", "mobi", ALL)] == ["pdf2docx", "calibre"]
    assert [e.engine for e in plan("docx", "epub", ALL)] == ["pandoc"]  # unchanged


def test_missing_calibre_asks_to_install(book_md, tmp_path, monkeypatch):
    monkeypatch.setattr(calibre, "ebook_convert_path", lambda: None)
    with pytest.raises(ToolMissingError) as info:
        convert([book_md], "doc:azw3", tmp_path)
    assert info.value.tool == "Calibre"


@needs_calibre
@pytest.mark.parametrize("fmt", ["azw3", "mobi"])
def test_markdown_to_kindle_and_back(book_md, tmp_path, fmt):
    [kindle] = convert(
        [book_md], f"doc:{fmt}", tmp_path, ebook_title="My Novel", ebook_author="A. Writer"
    )
    data = kindle.read_bytes()
    assert data[60:68] == b"BOOKMOBI"  # PalmDB type/creator of Kindle books
    assert b"My Novel" in data and b"A. Writer" in data  # EXTH title / author records
    assert detect(kindle).ext == fmt
    [epub] = convert([kindle], "doc:epub", tmp_path / "back")
    import zipfile

    with zipfile.ZipFile(epub) as zf:
        text = " ".join(
            zf.read(n).decode("utf-8", "ignore")
            for n in zf.namelist()
            if n.endswith((".html", ".xhtml", ".htm"))
        )
    assert "Hej ÆØÅ" in text and "Chapter 2" in text
