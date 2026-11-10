"""Editor model: layers, document composite, undo history."""

import pytest
from PIL import Image

from umc.core.errors import ConversionError
from umc.editor.document import Document, check_size
from umc.editor.geometry import Rect
from umc.editor.layer import Layer


def test_composite_opacity_and_hidden():
    red = Layer("r", Image.new("RGBA", (4, 4), (255, 0, 0, 255)))
    blue = Layer("b", Image.new("RGBA", (4, 4), (0, 0, 255, 255)), opacity=0.5)
    hidden = Layer("h", Image.new("RGBA", (4, 4), (0, 255, 0, 255)), visible=False)
    doc = Document(4, 4, [red, blue, hidden])  # index 0 = bottom
    px = doc.composite().getpixel((0, 0))
    assert px[3] == 255 and abs(px[0] - 128) <= 1 and abs(px[2] - 127) <= 1 and px[1] == 0


def test_composite_offset_layer_and_rect():
    small = Layer("s", Image.new("RGBA", (2, 2), (0, 0, 0, 255)), x=2, y=2)
    doc = Document(4, 4, [small])
    img = doc.composite()
    assert img.getpixel((1, 1))[3] == 0 and img.getpixel((3, 3)) == (0, 0, 0, 255)
    part = doc.composite(Rect(2, 2, 4, 4))
    assert part.size == (2, 2) and part.getpixel((0, 0)) == (0, 0, 0, 255)


def test_layer_converts_to_rgba():
    assert Layer("p", Image.new("P", (2, 2))).image.mode == "RGBA"


def test_size_limit():
    with pytest.raises(ConversionError, match="too large"):
        check_size(16_001, 10)
    with pytest.raises(ConversionError):
        check_size(16_000, 16_000)  # 256 MP > 250 MP
    check_size(16_000, 100)


def test_rect_ops():
    assert Rect.from_points(5, 5, 1, 2) == Rect(1, 2, 5, 5)
    assert Rect(0, 0, 4, 4).intersect(Rect(6, 6, 8, 8)).empty
    assert Rect(0, 0, 2, 2).union(Rect(1, 1, 3, 3)) == Rect(0, 0, 3, 3)
    assert Rect(1, 1, 3, 4).offset(1, -1) == Rect(2, 0, 4, 3)
    assert Rect(0, 0, 0, 0).union(Rect(1, 1, 2, 2)) == Rect(1, 1, 2, 2)


def test_new_document_and_bounds():
    doc = Document.new(30, 20)
    assert len(doc.layers) == 1 and doc.active_layer.name == "Background"
    assert doc.bounds == Rect(0, 0, 30, 20) and not doc.dirty
    assert doc.layers[0].bounds == Rect(0, 0, 30, 20)
