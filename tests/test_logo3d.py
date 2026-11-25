"""3D logo model (``umc.editor3d.logo`` / ``pick``); web/tests/logo3d.test.ts checks the same
shared cases against the website's ports."""

import json
from pathlib import Path

import numpy as np
import pytest

from umc.editor3d import logo, pick

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("mapbox_earcut")

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "logo3d_cases.json"


def grid(rows):
    return np.array([[c == "#" for c in r] for r in rows], np.uint8)


def test_shared_cases_are_current(tmp_path, monkeypatch):
    """The checked-in cases are what the model makes today (regenerate only on purpose)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_cases", ROOT / "scripts" / "make_logo3d_cases.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path / "cases.json")
    mod.main()
    assert json.loads((tmp_path / "cases.json").read_text()) == json.loads(CASES.read_text())


def _solid(m: logo.Mesh) -> trimesh.Trimesh:
    return trimesh.Trimesh(m.vertices, m.faces, process=True)


def test_mask_modes():
    rgba = bytes([0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 0, 128, 128, 128, 255])
    assert logo.default_mode(rgba, 4, 1) == "alpha"
    assert logo.mask(rgba, 4, 1, "alpha").tolist() == [[1, 1, 0, 1]]
    assert logo.mask(rgba, 4, 1, "dark").tolist() == [[1, 0, 0, 0]]
    assert logo.mask(rgba, 4, 1, "light").tolist() == [[0, 1, 0, 0]]
    assert logo.mask(rgba, 4, 1, "dark", invert=True).tolist() == [[0, 1, 1, 1]]


def test_square_corners_stay_square_and_holes_nest():
    m = np.zeros((14, 14), np.uint8)
    m[2:12, 2:12] = 1
    m[5:9, 5:9] = 0
    rings = logo.trace(m)
    assert sorted(logo.area(r) for r in rings) == [-16.0, 100.0]
    s = logo.shape(rings, 50)
    assert len(s.polygons) == 1 and len(s.polygons[0].holes) == 1
    assert s.width == pytest.approx(50) and s.height == pytest.approx(50)
    assert s.area == pytest.approx(50 * 50 * 84 / 100)


def test_pixels_touching_at_a_corner_stay_separate():
    rings = logo.trace(grid(["##..", "##..", "..##", "..##"]))
    assert len(rings) == 2 and all(logo.area(r) == pytest.approx(4, abs=0.2) for r in rings)
    a, b = ({p for p in r} for r in rings)
    assert not a & b  # moved apart: no shared corner


def test_touching_parts_make_clean_solids():
    """Every edge in exactly one triangle each way — also where parts touched at a corner
    (trimesh's own check is lenient about that)."""
    from collections import Counter

    s = logo.shape(logo.trace(grid(["#.#.", ".#.#", "#.#.", "####"])), 20)
    m = logo.logo_mesh(s, 2)
    key = [tuple(v) for v in m.vertices.tolist()]
    edges = Counter((key[f[k]], key[f[(k + 1) % 3]]) for f in m.faces.tolist() for k in range(3))
    assert all(n == 1 and edges[(b, a)] == 1 for (a, b), n in edges.items())


def test_specks_are_left_out_and_counted():
    rows = ["#" * 40] * 20 + ["." * 40] * 3 + ["." * 30 + "#" + "." * 9]
    s = logo.shape(logo.trace(grid(rows)), 40)
    assert len(s.polygons) == 1 and s.dropped == 1


def test_no_shape_is_a_plain_error():
    with pytest.raises(logo.LogoError, match="no clear shape"):
        logo.shape(logo.trace(np.zeros((4, 4), np.uint8)), 40)
    with pytest.raises(logo.LogoError, match="too small"):
        logo.shape(logo.trace(grid(["#.", ".."])), 0.2)


def _picture() -> tuple[bytes, int, int]:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGBA", (600, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((20, 20, 280, 280), fill="black")
    d.ellipse((100, 100, 200, 200), fill=(0, 0, 0, 0))
    d.text((300, 60), "Ab8", fill="black", font=ImageFont.load_default(150))
    return im.tobytes(), 600, 300


@pytest.mark.parametrize("plate,hole", [("none", False), ("rectangle", False), ("rounded", True)])
def test_badges_are_watertight_solids(plate, hole):
    rgba, w, h = _picture()
    s = logo.shape_from_picture(rgba, w, h, 80)
    assert s.width == pytest.approx(80)
    parts = logo.badge(s, 2, plate, 3, 2, hole)
    assert len(parts) == (1 if plate == "none" else 2)
    for m in parts:
        t = _solid(m)
        assert t.is_watertight and t.is_winding_consistent and t.volume > 0
        assert m.volume == pytest.approx(t.volume)
    lo, hi = parts[-1].vertices.min(axis=0), parts[-1].vertices.max(axis=0)
    if plate == "none":
        assert lo[2] == 0 and hi[2] == 2
        assert parts[0].volume == pytest.approx(s.area * 2)
    else:
        assert lo[2] == pytest.approx(2 - logo.SINK_MM) and hi[2] == pytest.approx(4)


def test_hole_tab_sits_left_of_the_logo():
    s = logo.shape(logo.trace(grid(["####", "####"])), 20)
    outer, hole = logo.plate_outline(s, 3, "rectangle", True)
    hx = [p[0] for p in hole]
    assert min(p[0] for p in outer) < min(hx) and max(hx) < -s.width / 2
    assert logo.area(hole) < 0


def _box():
    box = trimesh.creation.box((40, 40, 10))
    box.apply_translation((0, 0, 5))
    soup = box.vertices[box.faces].reshape(-1, 3)  # STL-like: corners repeated
    return soup, np.arange(len(soup)).reshape(-1, 3)


def test_click_on_top_finds_the_whole_face_and_places_the_logo():
    v, f = _box()
    tri, point = pick.hit(v, f, [3, 4, 100], [0, 0, -1])
    assert point == pytest.approx([3, 4, 10])
    face = pick.flat_face(v, f, tri)
    assert len(face.triangles) == 2 and face.normal == pytest.approx([0, 0, 1])
    assert face.area(v, f) == pytest.approx(1600)
    s = logo.shape(logo.trace(grid(["####", "#..#", "####"])), 20)
    placed = logo.placed_logo(s, 1.5, point, face.normal)
    assert placed.vertices[:, 2].min() == pytest.approx(10 - logo.SINK_MM)
    assert placed.vertices[:, 2].max() == pytest.approx(11.5)
    assert _solid(placed).is_watertight
    assert pick.overhang(logo.footprint(s, point, face.normal), v, f, face) == 0
    big = logo.shape(logo.trace(grid(["####", "#..#", "####"])), 60)
    assert pick.overhang(logo.footprint(big, point, face.normal), v, f, face) > 0


def test_logo_on_a_wall_stands_upright():
    v, f = _box()
    tri, point = pick.hit(v, f, [0, -100, 5], [0, 1, 0])
    face = pick.flat_face(v, f, tri)
    x, y, n = logo.frame(face.normal)
    assert n == pytest.approx([0, -1, 0]) and x == pytest.approx([1, 0, 0])
    assert y == pytest.approx([0, 0, 1])
    s = logo.shape(logo.trace(grid(["##", "##"])), 4)
    placed = logo.placed_logo(s, 1, point, face.normal)
    assert placed.vertices[:, 1].min() == pytest.approx(-21) and _solid(placed).volume > 0


def test_ray_through_the_centre_looks_at_the_target():
    from umc.editor3d.camera import OrbitCamera

    cam = OrbitCamera(target=np.array([1.0, 2.0, 3.0]), distance=50)
    o, d = pick.ray(cam, 200, 150, 400, 300)
    assert o == pytest.approx(cam.eye()) and d == pytest.approx(-cam.direction())


def test_miss_returns_none():
    v, f = _box()
    assert pick.hit(v, f, [100, 100, 100], [0, 0, 1]) is None
