import pytest
from PIL import Image

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.samples import write_test_dng, write_test_psd


@pytest.fixture
def dng(tmp_path):
    return write_test_dng(tmp_path / "photo.dng")


@pytest.fixture
def psd(tmp_path):
    return write_test_psd(tmp_path / "design.psd")


@pytest.mark.parametrize("name", ["a.cr2", "a.NEF", "a.arw", "a.dng", "a.raf", "a.orf", "a.psd"])
def test_raw_and_psd_are_images(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    assert detect(p).family is MediaFamily.IMAGE


def test_dng_to_jpg_keeps_colours(dng, tmp_path):
    [out] = convert([dng], "jpg", tmp_path / "out")
    img = Image.open(out).convert("RGB")
    assert img.size == (64, 48)
    r, g, b = img.getpixel((5, 24))
    assert r > 200 and b < 80  # left half red
    r, g, b = img.getpixel((60, 24))
    assert b > 200 and r < 80  # right half blue


def test_psd_to_png(psd, tmp_path):
    [out] = convert([psd], "png", tmp_path / "out")
    img = Image.open(out).convert("RGB")
    assert img.size == (40, 30)
    assert img.getpixel((10, 10)) == (0, 200, 0)


def test_raw_into_pdf_and_slideshow_gif(dng, psd, tmp_path):
    [pdf] = convert([dng, psd], "images:pdf", tmp_path / "out")
    assert pdf.read_bytes().startswith(b"%PDF")
    [gif] = convert([dng, psd], "slideshow:gif", tmp_path / "out", seconds_per_image=0.2)
    assert Image.open(gif).n_frames == 2
