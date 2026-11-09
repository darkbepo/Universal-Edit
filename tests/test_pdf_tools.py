import os

import pymupdf
import pytest

from tests.conftest import convert
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.registry import targets_for


@pytest.fixture
def photo_pdf(tmp_path):
    """A one-page PDF holding a large, noisy (hard to compress) photo."""
    from PIL import Image

    img = Image.frombytes("RGB", (2400, 1800), os.urandom(2400 * 1800 * 3))
    img_path = tmp_path / "photo.png"
    img.save(img_path)
    path = tmp_path / "photo.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(img_path))
        doc.save(path)
    return path


def _texts(path, password=None):
    with pymupdf.open(path) as doc:
        if password:
            doc.authenticate(password)
        return [p.get_text().strip() for p in doc]


def test_tools_offered(media):
    one = {t.id for t in targets_for([detect(media / "three.pdf")])}
    assert {"pdf:compress", "pdf:rotate", "pdf:organize", "pdf:protect", "pdf:unlock"} <= one
    assert "pdf:merge" not in one  # needs 2+ files
    two = {t.id for t in targets_for([detect(media / "three.pdf"), detect(media / "doc.md")])}
    assert "pdf:merge" in two and "pdf:compress" not in two  # .md isn't a PDF


def test_merge_with_bookmarks(media, tmp_path):
    [out] = convert([media / "three.pdf", media / "three.pdf"], "pdf:merge", tmp_path)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 6
        assert [t[1:] for t in doc.get_toc()] == [["three", 1], ["three", 4]]


def test_compress_shrinks_images(photo_pdf, tmp_path):
    [out] = convert([photo_pdf], "pdf:compress", tmp_path, level="strong")
    assert out.stat().st_size < photo_pdf.stat().st_size / 3
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1 and doc[0].get_images()


def test_rotate_selected_pages(media, tmp_path):
    [out] = convert([media / "three.pdf"], "pdf:rotate", tmp_path, angle=90, rotate_pages="2-")
    with pymupdf.open(out) as doc:
        assert [p.rotation for p in doc] == [0, 90, 90]


def test_reorder_and_delete(media, tmp_path):
    [out] = convert([media / "three.pdf"], "pdf:organize", tmp_path, keep="3, 1")
    assert _texts(out) == ["Page 3", "Page 1"]


def test_protect_and_unlock(media, tmp_path):
    [locked] = convert([media / "three.pdf"], "pdf:protect", tmp_path, password="s3cret",
                       allow_copy=False)  # fmt: skip
    with pymupdf.open(locked) as doc:
        assert doc.needs_pass
        assert doc.authenticate("s3cret")
        assert not doc.permissions & pymupdf.PDF_PERM_COPY
    with pytest.raises(ConversionError, match="password"):
        convert([locked], "pdf:unlock", tmp_path, password="wrong")
    with pytest.raises(ConversionError, match="password-protected"):
        convert([locked], "pdf:compress", tmp_path)
    [opened] = convert([locked], "pdf:unlock", tmp_path, password="s3cret")
    with pymupdf.open(opened) as doc:
        assert not doc.needs_pass
    assert _texts(opened) == ["Page 1", "Page 2", "Page 3"]


def test_protect_requires_password(media, tmp_path):
    with pytest.raises(ConversionError, match="Enter a password"):
        convert([media / "three.pdf"], "pdf:protect", tmp_path, password="")
