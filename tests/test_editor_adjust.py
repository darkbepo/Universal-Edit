"""Tone and colour adjustments (model only): the shared pixel cases, curves, auto levels,
histogram, selection limit and undo."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from umc.editor import adjust
from umc.editor.adjust import Adjustments, Levels
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.layer import Layer

CASES = json.loads((Path(__file__).parent / "fixtures" / "adjust_cases.json").read_text())


def build(settings: dict) -> Adjustments:
    s = dict(settings)
    levels = {k: Levels(**v) for k, v in s.pop("levels", {}).items()}
    curves = {k: tuple(map(tuple, v)) for k, v in s.pop("curves", {}).items()}
    return Adjustments(**s, levels=levels, curves=curves)


@pytest.mark.parametrize("name", sorted(CASES["cases"]))
def test_shared_cases(name):
    """The same pixels as the website (web/tests/editor-adjust.test.ts reads this file too)."""
    case = CASES["cases"][name]
    arr = np.array(CASES["pixels"], dtype=np.uint8).reshape(1, -1, 4)
    assert adjust.apply_array(arr, build(case["settings"]))[0].tolist() == case["expected"]


def test_neutral_settings_change_nothing():
    arr = np.array(CASES["pixels"], dtype=np.uint8).reshape(1, -1, 4)
    neutral = Adjustments(levels={"rgb": Levels()}, curves={"r": adjust.IDENTITY_CURVE})
    assert neutral.identity
    assert (adjust.apply_array(arr, neutral) == arr).all()


def test_alpha_is_never_changed():
    arr = np.array(CASES["pixels"], dtype=np.uint8).reshape(1, -1, 4)
    out = adjust.apply_array(arr, Adjustments(exposure=2, saturation=100, contrast=80))
    assert (out[..., 3] == arr[..., 3]).all()


def test_curve_goes_through_its_points_and_never_overshoots():
    pts = ((0, 0), (64, 40), (192, 220), (255, 255))
    t = adjust.curve_table(pts)
    for x, y in pts:
        assert t[x] == pytest.approx(y)
    assert (np.diff(t) >= 0).all() and t.min() >= 0 and t.max() <= 255
    flat = adjust.curve_table(((50, 20), (200, 230)))
    assert flat[0] == 20 and flat[255] == 230  # flat beyond the end points
    assert adjust.curve_table(((10, 10), (10, 99), (255, 255)))[10] == 99  # last point at x wins


def test_auto_levels_stretches_a_flat_photo():
    flat = Image.new("RGBA", (10, 10), (100, 100, 100, 255))
    flat.paste((140, 140, 140, 255), (0, 0, 10, 5))
    lv = adjust.auto_levels(flat)
    assert (lv.black, lv.white) == (100, 140)
    out = adjust.apply(flat, Adjustments(levels={"rgb": lv}))
    assert out.getpixel((0, 9))[0] == 0 and out.getpixel((0, 0))[0] == 255
    assert adjust.auto_levels(Image.new("RGBA", (3, 3))) == Levels()  # all transparent


def test_histogram_skips_transparent_pixels():
    img = Image.new("RGBA", (4, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    h = adjust.histogram(img)
    assert h["r"][255] == 1 and h["r"].sum() == 1 and h["rgb"][76] == 1


def _doc():
    img = Image.new("RGBA", (6, 4), (100, 100, 100, 255))
    return Document(6, 4, [Layer("bg", img)])


def test_apply_to_document_inside_selection_with_undo():
    doc = _doc()
    doc.selection = Rect(0, 0, 3, 4)
    h = History(doc)
    adj = Adjustments(brightness=50)
    preview = adjust.preview(doc, adj)
    assert doc.layers[0].image.getpixel((0, 0))[0] == 100  # preview doesn't touch the layer
    h.push(adjust.apply_to_document(doc, adj, "Quick fix"))
    img = doc.layers[0].image
    assert img.getpixel((0, 0))[0] == 228 and img.getpixel((5, 0))[0] == 100
    assert preview.tobytes() == img.tobytes()
    assert h.undo_label == "Quick fix"
    h.undo()
    assert doc.layers[0].image.getpixel((0, 0))[0] == 100
    assert adjust.apply_to_document(doc, Adjustments()) is None
