"""Pasteboard: layers can lie around the canvas; they are shown but never saved."""

from __future__ import annotations

from PIL import Image

from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.layer import Layer


def _doc() -> Document:
    bg = Layer("bg", Image.new("RGBA", (100, 80), (255, 255, 255, 255)))
    red = Layer("red", Image.new("RGBA", (40, 20), (255, 0, 0, 255)), x=80, y=-10)
    return Document(100, 80, [bg, red])


def test_pasteboard_covers_layers_around_the_canvas():
    doc = _doc()
    assert doc.pasteboard() == Rect(0, -10, 120, 80)
    doc.layers[1] = Layer("red", doc.layers[1].image, x=10, y=10)
    assert doc.pasteboard() == doc.bounds


def test_pasteboard_is_limited_and_rounded():
    doc = _doc()
    doc.layers[1] = Layer("far", Image.new("RGBA", (100, 10)), x=-90, y=5)
    assert doc.pasteboard() == Rect(-50, 0, 100, 80)  # at most half the longer side
    doc.layers[1] = Layer("red", Image.new("RGBA", (40, 20)), x=80, y=-10)
    assert doc.pasteboard(16) == Rect(0, -16, 132, 80)


def test_hidden_layers_do_not_grow_it():
    doc = _doc()
    doc.layers[1] = Layer("red", doc.layers[1].image, x=80, y=-10, visible=False)
    assert doc.pasteboard() == doc.bounds


def test_saving_uses_only_the_canvas_but_the_view_shows_the_rest():
    doc = _doc()
    assert doc.composite().size == (100, 80)
    view = doc.composite(Rect(0, -10, 120, 80), clip=False)
    assert view.size == (120, 90)
    assert view.getpixel((110, 0)) == (255, 0, 0, 255)  # off the canvas, on the pasteboard
    assert doc.composite(Rect(0, -10, 120, 80)).size == (100, 80)
