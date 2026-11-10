"""Text v2: editable text layers, outline, shadow, box, alignment, watermark (model only)."""

from dataclasses import replace

import numpy as np
from PIL import Image

from umc.editor import ops
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.ora import read_ora, write_ora
from umc.editor.tools.text import (
    TextSpec,
    add_text_layer,
    anchor_of,
    edit_text_layer,
    render_spec,
    text_layer_at,
)

BLACK, WHITE, RED = (0, 0, 0, 255), (255, 255, 255, 255), (255, 0, 0, 255)


def _doc():
    return Document(300, 200, [Layer("bg", Image.new("RGBA", (300, 200), WHITE))])


def _add(doc, text="Hello", **style):
    return add_text_layer(doc, text, 50, 40, "DejaVu Sans", 30, False, False, BLACK, **style)


def test_styles_grow_the_image_and_keep_the_click_point():
    plain, ox, oy = render_spec(TextSpec("Hi", size=30), (300, 200))
    assert (ox, oy) == (0, 0)
    outlined, *_ = render_spec(TextSpec("Hi", size=30, outline=4), (300, 200))
    assert outlined.width > plain.width and outlined.height > plain.height
    boxed, bx, by = render_spec(TextSpec("Hi", size=30, box=True, box_color=RED), (300, 200))
    assert bx > 0 and by > 0 and boxed.getpixel((2, boxed.height // 2))[:3] == (255, 0, 0)
    shadowed, sx, sy = render_spec(TextSpec("Hi", size=30, shadow=True), (300, 200))
    a = np.asarray(shadowed)[..., 3]
    assert a.max() == 255 and (a > 0).sum() > (np.asarray(plain)[..., 3] > 0).sum()


def test_alignment_moves_short_lines():
    left, *_ = render_spec(TextSpec("Wide line\nx", size=30), (300, 200))
    right, *_ = render_spec(TextSpec("Wide line\nx", size=30, align="right"), (300, 200))
    last_row = lambda im: np.flatnonzero(np.asarray(im)[-8, :, 3] > 0)  # noqa: E731
    assert last_row(right).min() > last_row(left).max()


def test_watermark_fills_the_canvas_faintly():
    doc = _doc()
    _add(doc, "© me", watermark=True)
    layer = doc.active_layer
    assert layer.image.size == (300, 200) and (layer.x, layer.y) == (0, 0)
    assert layer.opacity < 0.5
    a = np.asarray(layer.image)[..., 3]
    assert (
        (a[:, :150] > 0).any()
        and (a[:, 150:] > 0).any()
        and (a[:100] > 0).any()
        and (a[100:] > 0).any()
    )


def test_text_layers_can_be_edited_again_in_place():
    doc = _doc()
    h = History(doc)
    h.push(_add(doc, "Hello", outline=2, outline_color=WHITE))
    i = doc.active
    spec = doc.layers[i].text
    assert spec.text == "Hello" and spec.outline == 2 and anchor_of(doc.layers[i]) == (50, 40)
    assert text_layer_at(doc, 55, 45) == i and text_layer_at(doc, 5, 5) is None
    h.push(edit_text_layer(doc, i, replace(spec, text="Hello world", box=True)))
    edited = doc.layers[i]
    assert edited.text.text == "Hello world" and edited.text.box and anchor_of(edited) == (50, 40)
    assert edited.name == "Hello world" and h.undo_label == "Edit text"
    h.undo()
    assert doc.layers[i].text.text == "Hello"


def test_a_moved_text_layer_stays_where_it_was_moved_when_edited():
    doc = _doc()
    _add(doc, "Hi")
    i = doc.active
    ops.nudge_layers(doc, [i], 30, 10)
    edit_text_layer(doc, i, replace(doc.layers[i].text, text="Hi!", shadow=True))
    assert anchor_of(doc.layers[i]) == (80, 50)


def test_empty_text_deletes_and_transforms_make_it_pixels():
    doc = _doc()
    _add(doc, "Bye")
    edit_text_layer(doc, doc.active, replace(doc.active_layer.text, text="  "))
    assert len(doc.layers) == 1
    _add(doc, "Turn")
    ops.rotate(doc, 90)
    assert doc.layers[1].text is None  # rotated text is just pixels
    _add(doc, "Copy")
    ops.duplicate_layer(doc)
    assert doc.active_layer.text is not None and doc.active_layer.text.text == "Copy"


def test_text_stays_editable_through_an_ora_file(tmp_path):
    doc = _doc()
    _add(doc, "Saved", outline=3, box=True, box_color=RED)
    path = tmp_path / "t.ora"
    write_ora(doc, path)
    back = read_ora(path)
    spec = back.layers[1].text
    assert spec is not None and spec.text == "Saved" and spec.box_color == RED and spec.outline == 3
    assert back.layers[0].text is None
