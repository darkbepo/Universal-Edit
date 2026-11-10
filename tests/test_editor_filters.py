"""Blur, sharpen and one-click looks (model only)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from umc.editor import effects, filters
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer

ROOT = Path(__file__).resolve().parents[1]


def _edge(w=20, h=10):
    """Left half black, right half white, opaque."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    img.paste((255, 255, 255, 255), (w // 2, 0, w, h))
    return img


def test_blur_softens_edges_and_keeps_flat_areas():
    out = filters.Blur(2)(_edge())
    row = [out.getpixel((x, 5))[0] for x in range(20)]
    assert row[0] == 0 and row[-1] == 255
    assert 0 < row[9] < 255 and 0 < row[10] < 255  # the edge is soft now
    flat = Image.new("RGBA", (8, 8), (40, 80, 120, 255))
    assert filters.Blur(3)(flat).tobytes() == flat.tobytes()
    assert filters.Blur(0).identity and not filters.Blur(0.5).identity


def test_blur_has_no_dark_fringe_at_transparent_edges():
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (5, 5, 15, 15))
    out = filters.Blur(2)(img)
    r, g, b, a = out.getpixel((4, 10))
    assert 0 < a < 255 and r > 240 and g == b == 0  # still red, only see-through


def test_blur_scale_shrinks_the_radius_for_previews():
    big, small = filters.Blur(4)(_edge(), 1.0), filters.Blur(4)(_edge(), 0.25)
    soft = lambda im: sum(0 < im.getpixel((x, 5))[0] < 255 for x in range(20))  # noqa: E731
    assert soft(big) > soft(small)


def test_sharpen_raises_edge_contrast_and_threshold_keeps_smooth_areas():
    soft = filters.Blur(1.5)(_edge())
    sharp = filters.Sharpen(200, 2)(soft)
    before = [soft.getpixel((x, 5))[0] for x in (8, 11)]
    after = [sharp.getpixel((x, 5))[0] for x in (8, 11)]
    assert after[0] < before[0] and after[1] > before[1]
    noise = Image.new("RGBA", (10, 10), (100, 100, 100, 255))
    noise.putpixel((5, 5), (104, 104, 104, 255))
    assert filters.Sharpen(300, 1, threshold=10)(noise).tobytes() == noise.tobytes()
    assert filters.Sharpen(0).identity


def test_looks_match_the_websites_list():
    web = json.loads((ROOT / "web" / "src" / "editor" / "looks.json").read_text())
    web.pop("_note")
    ours = {
        k: {"label": label, "steps": list(steps)} for k, (label, steps) in filters.LOOKS.items()
    }
    assert ours == web


@pytest.mark.parametrize("name", sorted(filters.LOOKS))
def test_every_look_changes_a_photo_and_keeps_alpha(name):
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 256, (16, 16, 4), dtype=np.uint8)
    img = Image.fromarray(arr, "RGBA")
    out = filters.Look(name)(img)
    assert out.tobytes() != img.tobytes()
    assert (np.asarray(out)[..., 3] == arr[..., 3]).all()


def test_black_and_white_looks_are_grey_and_strength_mixes():
    img = Image.new("RGBA", (4, 4), (200, 60, 30, 255))
    for name in ("bw", "noir"):
        r, g, b, _ = filters.Look(name)(img).getpixel((0, 0))
        assert r == g == b
    r, g, b, _ = filters.Look("sepia")(img).getpixel((0, 0))
    assert r > g > b  # brownish
    half = filters.Look("bw", 0.5)(img).getpixel((0, 0))
    full = filters.Look("bw")(img).getpixel((0, 0))
    assert abs(half[0] - (200 + full[0]) / 2) <= 1
    assert filters.Look("bw", 0).identity and filters.Look("nope").identity


def test_filters_apply_inside_the_selection_with_undo():
    from umc.editor.geometry import Rect

    doc = Document(20, 10, [Layer("bg", _edge())])
    doc.selection = Rect(0, 0, 10, 10)
    h = History(doc)
    h.push(effects.apply_to_document(doc, filters.Blur(3), "Blur"))
    img = doc.layers[0].image
    assert 0 < img.getpixel((9, 5))[0] < 255  # blurred inside (white bleeds in)
    assert img.getpixel((12, 5))[0] == 255  # outside untouched
    h.undo()
    assert doc.layers[0].image.getpixel((9, 5))[0] == 0


def test_sharpen_keeps_a_cut_outs_colour_and_alpha():
    img = Image.new("RGBA", (12, 1), (0, 0, 0, 0))
    for x, a in enumerate((60, 120, 180, 240, 255, 255, 255, 255)):
        img.putpixel((x + 2, 0), (200, 200, 200, a))
    out = filters.Sharpen(200, 1)(img)
    for x in range(2, 10):
        r, g, b, a = out.getpixel((x, 0))
        # Pillow blurs premultiplied colour in 8-bit steps, so faint pixels may be off by a
        # step or two (was +14: a bright fringe); alpha is never touched
        assert max(abs(v - 200) for v in (r, g, b)) <= 3 and a == img.getpixel((x, 0))[3]


def test_small_radius_and_preview_scale_still_change_the_picture():
    soft = filters.Blur(1)(_edge())
    assert filters.Sharpen(200, 0.3)(soft).tobytes() != soft.tobytes()
    assert filters.Blur(2)(_edge(), 0.25).tobytes() != _edge().tobytes()


def _offset_doc():
    """A 30×20 canvas with a 10×10 layer at (12, 6): left half black, right half white."""
    layer_img = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    layer_img.paste((255, 255, 255, 255), (5, 0, 10, 10))
    return Document(30, 20, [Layer("small", layer_img, x=12, y=6)])


def test_blur_on_an_offset_layer_with_a_selection_at_its_edge():
    from umc.editor.geometry import Rect

    doc = _offset_doc()
    doc.selection = Rect(12, 6, 17, 16)  # the layer's left (black) half, touching its border
    h = History(doc)
    h.push(effects.apply_to_document(doc, filters.Blur(40), "Blur"))  # radius ≫ layer
    img = doc.layers[0].image
    assert 0 < img.getpixel((4, 5))[0] < 255  # white bled in from beyond the selection
    assert img.getpixel((7, 5))[0] == 255  # outside the selection untouched
    h.undo()
    assert doc.layers[0].image.getpixel((4, 5))[0] == 0


def test_reduced_blur_preview_softens_edges_like_apply():
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (50, 50, 150, 150))
    doc = Document(200, 200, [Layer("bg", img)])
    fast = effects.preview(doc, filters.Blur(6), max_pixels=2_000)
    exact = effects.preview(doc, filters.Blur(6))
    assert fast.getpixel((47, 100))[3] > 0 and exact.getpixel((47, 100))[3] > 0
    diff = np.abs(np.asarray(fast, dtype=int) - np.asarray(exact, dtype=int))[..., 3]
    assert diff.mean() < 8
