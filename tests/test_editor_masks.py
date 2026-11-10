"""Layer masks, the mask brush, the gradient tool and background blur (model only)."""

import numpy as np
import pytest
from PIL import Image

from umc.core import options as opts
from umc.editor import effects, filters, ops, selection
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.tools.base import ToolContext
from umc.editor.tools.gradient import GradientOptions, GradientTool
from umc.editor.tools.paint import BrushOptions, MaskBrushTool


def _doc():
    bg = Layer("bg", Image.new("RGBA", (20, 10), (0, 0, 255, 255)))
    top = Layer("top", Image.new("RGBA", (20, 10), (255, 0, 0, 255)))
    return Document(20, 10, [bg, top], active=1)


def _ctx(options, button="left", shift=False):
    return ToolContext((255, 255, 255, 255), (0, 0, 0, 255), options, shift=shift, button=button)


def test_mask_hides_parts_of_a_layer_and_undoes():
    doc = _doc()
    h = History(doc)
    h.push(ops.add_mask(doc))
    assert doc.active_layer.mask.getextrema() == (255, 255)
    assert ops.add_mask(doc) is None  # already has one
    doc.active_layer.mask.paste(0, (0, 0, 10, 10))
    out = doc.composite()
    assert out.getpixel((5, 5))[:3] == (0, 0, 255) and out.getpixel((15, 5))[:3] == (255, 0, 0)
    h.push(ops.invert_mask(doc))
    assert doc.composite().getpixel((5, 5))[:3] == (255, 0, 0)
    h.undo()
    h.undo()
    assert doc.active_layer.mask is None


def test_mask_from_selection_apply_and_delete():
    doc = _doc()
    doc.selection = Rect(0, 0, 5, 10)
    ops.add_mask(doc, from_selection=True)
    m = doc.active_layer.mask
    assert m.getpixel((2, 2)) == 255 and m.getpixel((10, 2)) == 0
    ops.apply_mask(doc)
    layer = doc.active_layer
    assert layer.mask is None and layer.image.getpixel((10, 2))[3] == 0
    assert ops.delete_mask(doc) is None


def test_masks_follow_crop_resize_rotate_flip_and_duplicate():
    doc = _doc()
    ops.add_mask(doc)
    doc.active_layer.mask.paste(0, (0, 0, 5, 10))  # hide the left quarter
    ops.flip(doc, horizontal=True)
    assert doc.layers[1].mask.getpixel((19, 5)) == 0 and doc.layers[1].mask.getpixel((0, 5)) == 255
    ops.rotate(doc, 90)
    assert doc.layers[1].mask.size == (10, 20)
    ops.resize_image(doc, 5, 10, "nearest")
    assert doc.layers[1].mask.size == doc.layers[1].image.size == (5, 10)
    ops.crop(doc, Rect(0, 0, 5, 5))
    assert doc.layers[1].mask.size == doc.layers[1].image.size
    ops.duplicate_layer(doc)
    assert doc.layers[2].mask is not None and doc.layers[2].mask is not doc.layers[1].mask


def test_merge_bakes_the_mask_in():
    doc = _doc()
    ops.add_mask(doc)
    doc.active_layer.mask.paste(0, (0, 0, 10, 10))
    ops.merge_down(doc)
    merged = doc.layers[0]
    assert merged.mask is None
    assert merged.image.getpixel((5, 5))[:3] == (0, 0, 255) and merged.image.getpixel((15, 5))[
        :3
    ] == (255, 0, 0)


def test_mask_brush_hides_and_reveals_with_undo():
    doc = _doc()
    ops.add_mask(doc)
    h = History(doc)
    o = opts.build(BrushOptions, {**opts.defaults(BrushOptions), "size": 6, "hardness": 100})
    tool = MaskBrushTool()
    tool.press(doc, 5, 5, _ctx(o))
    h.push(tool.release(doc, 5, 5, _ctx(o)))
    assert doc.active_layer.mask.getpixel((5, 5)) == 0
    assert doc.active_layer.image.getpixel((5, 5)) == (255, 0, 0, 255)  # pixels untouched
    tool.press(doc, 5, 5, _ctx(o, "right"))
    h.push(tool.release(doc, 5, 5, _ctx(o, "right")))
    assert doc.active_layer.mask.getpixel((5, 5)) == 255
    h.undo()
    assert doc.active_layer.mask.getpixel((5, 5)) == 0
    doc2 = _doc()  # no mask yet: the first stroke adds one, in the same undo step
    h2 = History(doc2)
    tool.press(doc2, 5, 5, _ctx(o))
    h2.push(tool.release(doc2, 5, 5, _ctx(o)))
    assert doc2.active_layer.mask.getpixel((5, 5)) == 0
    assert doc2.active_layer.mask.getpixel((15, 8)) == 255
    h2.undo()
    assert doc2.active_layer.mask is None


def _gradient(doc, a, b, **kw):
    o = opts.build(GradientOptions, {**opts.defaults(GradientOptions), **kw})
    tool = GradientTool()
    tool.press(doc, *a, _ctx(o))
    tool.move(doc, *b, _ctx(o))
    return tool.release(doc, *b, _ctx(o))


def test_gradient_paints_colour_from_front_to_back():
    doc = _doc()
    h = History(doc)
    h.push(_gradient(doc, (0, 5), (20, 5)))
    img = doc.active_layer.image
    assert img.getpixel((0, 5))[0] > 240 and img.getpixel((19, 5))[0] < 15
    assert 100 < img.getpixel((10, 5))[0] < 160
    h.undo()
    assert doc.active_layer.image.getpixel((0, 5)) == (255, 0, 0, 255)
    _gradient(doc, (0, 5), (20, 5), colours="front_clear")
    r, g, b, _ = doc.active_layer.image.getpixel((19, 5))  # near the see-through end: red shows
    assert r == 255 and g < 15 and b < 15


def test_gradient_on_the_mask_and_as_a_soft_selection():
    doc = _doc()
    _gradient(doc, (0, 0), (20, 0), target="mask")
    m = doc.active_layer.mask
    assert m.getpixel((0, 5)) > 240 and m.getpixel((19, 5)) < 15
    _gradient(doc, (0, 0), (0, 10), target="selection")
    w = selection.weight(doc, doc.selection)
    assert w[0, 0] > 0.9 and w[-1, 0] < 0.1
    # an adjustment now fades out from top to bottom
    from umc.editor.adjust import Adjustments

    doc.active = 0
    effects.apply_to_document(doc, Adjustments(brightness=100), "Quick fix")
    img = doc.layers[0].image
    assert img.getpixel((5, 0))[0] > img.getpixel((5, 9))[0]


def test_gradient_radial_and_a_click_does_nothing():
    doc = _doc()
    assert _gradient(doc, (10, 5), (10, 5)) is None
    _gradient(doc, (10, 5), (20, 5), shape="radial")
    img = doc.active_layer.image
    assert img.getpixel((10, 5))[0] > 220 and img.getpixel((0, 5))[0] < 15


def test_background_blur_blurs_only_outside_the_selection():
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 255))
    for x in range(0, 40, 2):
        img.paste((255, 255, 255, 255), (x, 0, x + 1, 20))  # stripes
    doc = Document(40, 20, [Layer("photo", img)])
    doc.selection = Rect(0, 0, 10, 20)  # "the subject"
    before = np.asarray(img).copy()
    h = History(doc)
    h.push(effects.apply_to_document(doc, filters.BackgroundBlur(3, 0), "Background blur"))
    out = np.asarray(doc.layers[0].image)
    assert (out[:, :8] == before[:, :8]).all()  # the subject stays sharp
    assert out[:, 30:, 0].std() < before[:, 30:, 0].std() / 3  # the background is blurred
    assert doc.selection == Rect(0, 0, 10, 20) and doc.selection_mask is None  # selection kept
    h.undo()
    assert (np.asarray(doc.layers[0].image) == before).all()


@pytest.mark.parametrize("soften", [0, 4])
def test_background_blur_needs_a_selection(soften):
    doc = Document(10, 10, [Layer("photo", Image.new("RGBA", (10, 10), (9, 9, 9, 255)))])
    # without a selection "outside" is nothing at all → no change
    assert (
        effects.apply_to_document(doc, filters.BackgroundBlur(3, soften), "Background blur") is None
    )


def test_background_blur_with_everything_selected_changes_nothing():
    from umc.editor.geometry import Rect

    doc = Document(10, 10, [Layer("photo", Image.new("RGBA", (10, 10), (9, 9, 9, 255)))])
    doc.selection = Rect(0, 0, 10, 10)
    assert effects.apply_to_document(doc, filters.BackgroundBlur(3, 0), "Background blur") is None
