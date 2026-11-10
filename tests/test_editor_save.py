"""Saving an edited document: .ora keeps layers, every other format via the image converter."""

from pathlib import Path

import pytest
from PIL import Image

from umc.converters.image import ImageConverter
from umc.core.errors import ConversionError
from umc.editor.document import Document
from umc.editor.layer import Layer
from umc.editor.ora import read_ora
from umc.editor.save import SAVE_FORMATS, save_document


def _doc():
    noise = Image.effect_noise((64, 64), 60).convert("RGBA")
    top = Layer("dot", Image.new("RGBA", (8, 8), (255, 0, 0, 255)), x=4, y=4, opacity=0.5)
    return Document(64, 64, [Layer("bg", noise), top])


def test_save_png_keeps_alpha(tmp_path):
    doc = Document(4, 4, [Layer("a", Image.new("RGBA", (4, 4), (0, 255, 0, 100)))])
    out = save_document(doc, tmp_path / "x.png", "png")
    assert out == tmp_path / "x.png"
    assert Image.open(out).convert("RGBA").getpixel((1, 1)) == (0, 255, 0, 100)


def test_save_ora_round_trip(tmp_path):
    out = save_document(_doc(), tmp_path / "x.ora", "ora")
    back = read_ora(out)
    assert [la.name for la in back.layers] == ["bg", "dot"]


def test_save_replaces_existing_file(tmp_path):
    dest = tmp_path / "x.png"
    dest.write_bytes(b"old")
    save_document(_doc(), dest, "png")
    assert Image.open(dest).size == (64, 64)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.png"]


def test_save_quality_option_used(tmp_path):
    low = save_document(_doc(), tmp_path / "low.jpg", "jpg", {"quality": 10})
    high = save_document(_doc(), tmp_path / "high.jpg", "jpg", {"quality": 95})
    assert low.stat().st_size < high.stat().st_size


def test_save_jpg_has_white_background(tmp_path):
    doc = Document.new(10, 10)  # fully transparent
    out = save_document(doc, tmp_path / "x.jpg", "jpg")
    assert Image.open(out).convert("RGB").getpixel((5, 5)) == (255, 255, 255)


def test_save_failure_leaves_dest(tmp_path, monkeypatch):
    dest = tmp_path / "x.jpg"
    dest.write_bytes(b"keep me")

    def boom(self, job):
        raise ConversionError("Couldn't save.")

    monkeypatch.setattr(ImageConverter, "run", boom)
    with pytest.raises(ConversionError):
        save_document(_doc(), dest, "jpg")
    assert dest.read_bytes() == b"keep me"


def test_save_unknown_format(tmp_path):
    with pytest.raises(ConversionError):
        save_document(_doc(), tmp_path / "x.xyz", "xyz")


def test_save_formats_list():
    ids = [fid for fid, _ in SAVE_FORMATS]
    assert ids[0] == "ora" and "png" in ids and "jpg" in ids


def test_selftest_editor_step(tmp_path):
    from umc.selftest import _editor_check

    paths = _editor_check(tmp_path)
    assert [p.suffix for p in paths] == [".ora", ".png", ".jpg"]
    assert all(p.exists() for p in paths)


def test_save_moves_within_destination_folder(tmp_path, monkeypatch):
    """os.replace can't cross drives on Windows: the converter output must be made next to dest."""
    import os

    import umc.editor.save as save_mod

    dest_dir = tmp_path / "other-drive"
    dest_dir.mkdir()
    real = os.replace

    def same_drive_only(src, dst):
        if dest_dir not in Path(src).resolve().parents:
            raise OSError(17, "The system cannot move the file to a different disk drive")
        return real(src, dst)

    monkeypatch.setattr(save_mod.os, "replace", same_drive_only)
    out = save_document(_doc(), dest_dir / "x.png", "png")
    assert out.exists() and sorted(p.name for p in dest_dir.iterdir()) == ["x.png"]
