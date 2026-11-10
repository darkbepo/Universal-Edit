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
