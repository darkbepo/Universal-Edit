"""OpenRaster (.ora) files and opening images as layers."""

import zipfile

from PIL import Image

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.editor.document import Document
from umc.editor.io import open_files
from umc.editor.layer import Layer
from umc.editor.ora import read_ora, write_ora


def _doc() -> Document:
    bottom = Layer("Bottom", Image.new("RGBA", (20, 10), (255, 0, 0, 255)))
    top = Layer("Top", Image.new("RGBA", (5, 5), (0, 0, 255, 255)), x=3, y=4, opacity=0.5)
    hidden = Layer("Hidden", Image.new("RGBA", (20, 10), (0, 255, 0, 255)), visible=False)
    return Document(20, 10, [bottom, top, hidden], active=1)


def test_ora_round_trip(tmp_path):
    p = tmp_path / "a.ora"
    write_ora(_doc(), p)
    doc = read_ora(p)
    assert (doc.width, doc.height) == (20, 10)
    assert [la.name for la in doc.layers] == ["Bottom", "Top", "Hidden"]
    top = doc.layers[1]
    assert (top.x, top.y, top.image.size) == (3, 4, (5, 5))
    assert abs(top.opacity - 0.5) < 1e-3 and top.visible
    assert not doc.layers[2].visible
    assert top.image.getpixel((0, 0)) == (0, 0, 255, 255)


def test_ora_mimetype_first_and_stored(tmp_path):
    p = tmp_path / "a.ora"
    write_ora(_doc(), p)
    with zipfile.ZipFile(p) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"image/openraster"
        names = set(z.namelist())
        assert {"stack.xml", "mergedimage.png", "Thumbnails/thumbnail.png"} <= names
        thumb = Image.open(z.open("Thumbnails/thumbnail.png"))
        assert max(thumb.size) <= 256


def test_ora_is_detected_as_image(tmp_path):
    p = tmp_path / "a.ora"
    write_ora(_doc(), p)
    assert detect(p).family == MediaFamily.IMAGE


def test_ora_opens_in_converter(tmp_path):
    p = tmp_path / "a.ora"
    doc = _doc()
    write_ora(doc, p)
    [out] = convert([p], "png", tmp_path / "out")
    assert Image.open(out).convert("RGBA").tobytes() == doc.composite().tobytes()


def test_open_files_layers_and_size(tmp_path):
    a, b = tmp_path / "wide.png", tmp_path / "tall.png"
    Image.new("RGB", (100, 50), "red").save(a)
    Image.new("RGB", (30, 80), "blue").save(b)
    doc, errors = open_files([a, b])
    assert not errors
    assert (doc.width, doc.height) == (100, 80)
    assert [la.name for la in doc.layers] == ["wide", "tall"]
    assert doc.active == 1 and not doc.dirty


def test_open_files_ora_keeps_layers(tmp_path):
    p = tmp_path / "a.ora"
    write_ora(_doc(), p)
    doc, _ = open_files([p])
    assert len(doc.layers) == 3 and doc.layers[1].x == 3


def test_open_files_bad_file_reported(tmp_path):
    bad, good = tmp_path / "broken.png", tmp_path / "good.png"
    bad.write_bytes(b"not an image at all")
    Image.new("RGB", (8, 8)).save(good)
    doc, errors = open_files([bad, good])
    assert len(doc.layers) == 1 and len(errors) == 1 and "broken.png" in errors[0]


def test_open_files_none_open(tmp_path):
    import pytest

    from umc.core.errors import ConversionError

    bad = tmp_path / "broken.png"
    bad.write_bytes(b"junk")
    with pytest.raises(ConversionError, match="None of the files"):
        open_files([bad])


def test_open_files_converts_modes(tmp_path):
    paths = []
    for mode in ("P", "L", "CMYK", "I;16"):
        p = tmp_path / f"{mode.replace(';', '')}.tif"
        Image.new(mode, (8, 8)).save(p)
        paths.append(p)
    doc, errors = open_files(paths)
    assert not errors and all(la.image.mode == "RGBA" for la in doc.layers)
