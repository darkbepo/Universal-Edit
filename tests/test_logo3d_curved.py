"""3D logos on curved surfaces: the surface sampler (``pick.surface``), cap refinement and
``logo.fitted_logo`` (Flat / Wrap, raised / engraved), and merging into one solid
(``editor3d.solid``, manifold3d). web/tests/logo3d.test.ts checks the shared cases too."""

from __future__ import annotations

import time

import numpy as np
import pytest

from umc.editor3d import logo, pick
from umc.editor3d import scene as S
from umc.editor3d.mesh import ViewModel

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("mapbox_earcut")

R = 30.0


def grid(rows):
    return np.array([[c == "#" for c in r] for r in rows], np.uint8)


def sphere(subdivisions: int = 6, radius: float = R):
    m = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)


def edges_closed(f: np.ndarray) -> bool:
    """Every edge used by exactly two triangles, once each way (trimesh's is_watertight
    accepts four)."""
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    pairs, counts = np.unique(e, axis=0, return_counts=True)
    if (counts != 1).any():
        return False
    fwd = {tuple(p) for p in pairs.tolist()}
    return all((b, a) in fwd for a, b in fwd)


LOGO = grid(["##########..########", "##########..#....###", "##########..########"])


def _fit(fit, style="raised", width=24.0, direction=(0.3, 0.2, 1.0), v=None, f=None):
    if v is None:
        v, f = sphere()
    shp = logo.prepare(logo.shape(logo.trace(LOGO), width))
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    point = d * R
    n = pick.area_normal(v, f, point, d, S.reach(shp))
    x, y, nn = logo.frame(n, 15)
    surf = pick.surface(v, f, point, np.array([x, y, nn]), S.reach(shp))
    return shp, n, logo.fitted_logo(shp, 2.0, point, n, 15, 0.2, fit, surf, style)


def test_area_normal_is_the_sphere_direction_and_a_flat_face_keeps_its_own():
    v, f = sphere()
    d = np.array([0.3, 0.2, 1.0]) / np.linalg.norm([0.3, 0.2, 1.0])
    n = pick.area_normal(v, f, d * R, [0, 0, 1], 8)  # a tilted click normal
    assert float(n @ d) > 0.999
    box = trimesh.creation.box((40, 40, 20))
    bn = pick.area_normal(box.vertices, box.faces, [0, 0, 10], [0, 0, 1], 15)
    assert bn == pytest.approx([0, 0, 1])


def test_surface_heights_on_a_box_and_misses_off_it():
    box = trimesh.creation.box((40, 40, 20))
    surf = pick.surface(box.vertices, box.faces, [0, 0, 10], np.eye(3), 30)
    h, n, hit = surf.sample(np.array([[0, 0], [19, -19], [25, 0]]))
    assert hit.tolist() == [True, True, False]
    assert h[:2] == pytest.approx([0, 0]) and n[0] == pytest.approx([0, 0, 1])


def test_surface_takes_the_hit_nearest_the_logo_plane():
    """Two plates under the click: the near one wins, never the far one."""
    a = trimesh.creation.box((20, 20, 2))
    b = a.copy()
    b.apply_translation([0, 0, -10])
    both = trimesh.util.concatenate([a, b])
    surf = pick.surface(both.vertices, both.faces, [0, 0, 1], np.eye(3), 5)
    h, _n, hit = surf.sample(np.array([[0.0, 0.0]]))
    assert hit[0] and h[0] == pytest.approx(0.0)


def _area(p: np.ndarray, tt: np.ndarray) -> float:
    a, b, c = p[tt[:, 0]], p[tt[:, 1]], p[tt[:, 2]]
    return 0.5 * float(
        np.sum(
            (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        )
    )


def test_refine_splits_long_edges_and_keeps_the_caps_closed():
    shp = logo.prepare(logo.shape(logo.trace(LOGO), 24))
    for poly in shp.polygons:
        xy = [p for r in (poly.outer, *poly.holes) for p in r]
        pts, tris = logo.refine(xy, poly.caps, 0.5)
        p = np.array(pts)
        t = np.array(tris)
        longest = max(
            np.linalg.norm(p[t[:, k]] - p[t[:, (k + 1) % 3]], axis=1).max() for k in range(3)
        )
        assert longest <= 0.5 + 1e-9
        assert _area(p, t) == pytest.approx(_area(p, np.array(poly.caps)))
        assert all(
            (p[t[:, 1], 0] - p[t[:, 0], 0]) * (p[t[:, 2], 1] - p[t[:, 0], 1])
            - (p[t[:, 1], 1] - p[t[:, 0], 1]) * (p[t[:, 2], 0] - p[t[:, 0], 0])
            > 0
        )
        # the outline is the polygon's rings, split into short pieces
        assert len(logo.outline_edges(tris)) >= len(xy)


@pytest.mark.parametrize("fit", ["flat", "wrap"])
def test_logo_on_a_sphere_sits_flush(fit):
    """The done check: no bottom point more than 0.05 mm (+ sink) off the sphere, closed."""
    _shp, _n, r = _fit(fit)
    m = r.mesh
    assert r.missed == 0 and r.folded == 0
    assert edges_closed(m.faces)
    rad = np.linalg.norm(m.vertices, axis=1)
    bottom, top = rad[rad < R], rad[rad >= R]  # bottoms sit below the surface, tops above
    assert len(bottom) == len(top) == len(rad) // 2
    assert np.abs(bottom - (R - 0.2)).max() < 0.05
    if fit == "wrap":
        assert np.abs(top - (R + 2.0)).max() < 0.05
    assert m.volume > 0


def test_flat_fit_keeps_a_flat_top_above_the_highest_point():
    _shp, n, r = _fit("flat")
    heights = (r.mesh.vertices - np.asarray(n) * R) @ n
    assert heights.max() == pytest.approx(2.0, abs=0.02)  # the click point is the top of a sphere


def test_wrap_refuses_a_logo_that_misses_the_model():
    with pytest.raises(logo.LogoError, match="miss the model"):
        _fit("wrap", width=80)
    _shp, _n, r = _fit("flat", width=80)
    assert r.missed > 0 and edges_closed(r.mesh.faces)


def heightfield(fn, size=30.0, steps=120):
    """An open surface z = fn(x, y) over a square, facing up."""
    xs = np.linspace(-size / 2, size / 2, steps + 1)
    gx, gy = np.meshgrid(xs, xs)
    v = np.stack([gx.ravel(), gy.ravel(), fn(gx, gy).ravel()], axis=1)
    i = np.arange(steps * (steps + 1)).reshape(steps, steps + 1)[:, :steps].ravel()
    k = steps + 1
    f = np.concatenate([np.stack([i, i + 1, i + k + 1], 1), np.stack([i, i + k + 1, i + k], 1)])
    return v, f


def test_wrap_warns_where_the_surface_turns_away():
    v, f = heightfield(lambda x, y: np.clip((x - 2) * 10, 0.0, 6.0))  # an 84° step
    shp = logo.prepare(logo.shape(logo.trace(LOGO), 12))
    surf = pick.surface(v, f, [0, 0, 0], np.eye(3), S.reach(shp))
    r = logo.fitted_logo(shp, 2.0, [0, 0, 0], [0, 0, 1], 0, 0.2, "wrap", surf)
    assert r.missed == 0 and r.folded > 0
    calm = logo.fitted_logo(shp, 2.0, [-8, 0, 0], [0, 0, 1], 0, 0.2, "wrap",
                            pick.surface(v, f, [-8, 0, 0], np.eye(3), S.reach(shp)))  # fmt: skip
    assert calm.folded == 0


@pytest.mark.parametrize("fit", ["flat", "wrap"])
def test_engraved_cutter_reaches_below_and_above_the_surface(fit):
    _shp, _n, r = _fit(fit, style="engraved")
    rad = np.linalg.norm(r.mesh.vertices, axis=1)
    assert rad.min() == pytest.approx(R - 2.0, abs=0.05)
    assert rad.max() >= R + logo.ENGRAVE_LIFT_MM - 0.05
    assert edges_closed(r.mesh.faces)


def _scene(fit="wrap", style="raised", closed=True):
    v, f = sphere()
    if not closed:
        f = f[1:]
    base = ViewModel("ball", v, f, watertight=closed, volume_mm3=4 / 3 * np.pi * R**3)
    pic = S.Picture("logo.png", bytes([0, 0, 0, 255] * 16), 4, 4)
    s = S.LogoSettings(width=12, thickness=2, fit=fit, style=style)
    lg = S.build_logo(pic, s, (0, 0, R), (0, 0, 1), base)
    return S.Scene(base, (lg,)), base


def test_build_logo_follows_the_surface_and_quick_does_not():
    sc, base = _scene("wrap")
    lg = sc.logos[0]
    assert np.linalg.norm(lg.meshes[0].vertices, axis=1).max() == pytest.approx(R + 2, abs=0.05)
    quick = S.build_logo(lg.picture, lg.settings, lg.point, lg.normal, base, quick=True)
    assert quick.meshes[0].vertices[:, 2].max() == pytest.approx(R + 2)


def _read(data: bytes):
    import io

    return trimesh.load(io.BytesIO(data), file_type="stl", force="mesh", process=True)


@pytest.mark.parametrize("style", ["raised", "engraved"])
def test_merged_export_is_one_watertight_body(style):
    pytest.importorskip("manifold3d")
    sc, _base = _scene("wrap", style)
    data, note = S.export_with_note(sc, "stl", merge=True)
    assert note == ""
    m = _read(data)
    assert m.is_watertight and len(m.split(only_watertight=False)) == 1
    ball = 4 / 3 * np.pi * R**3
    assert (m.volume > ball + 100) if style == "raised" else (m.volume < ball - 100)
    info = sc.combined()
    assert info.volume_mm3 == pytest.approx(m.volume, rel=0.02)


def test_merge_falls_back_on_an_open_model_and_engraving_refuses():
    pytest.importorskip("manifold3d")
    sc, _base = _scene("wrap", "raised", closed=False)
    data, note = S.export_with_note(sc, "stl", merge=True)
    assert note == S.MERGE_FAILED and len(data) > 0
    sc, _base = _scene("wrap", "engraved", closed=False)
    with pytest.raises(logo.LogoError, match="Engraving needs a closed model"):
        S.export_with_note(sc, "stl")


def test_placing_on_a_big_model_is_quick():
    v, f = sphere(8, 60)  # 1.3 M triangles
    shp = logo.prepare(logo.shape(logo.trace(LOGO), 40))
    t = time.perf_counter()
    point = np.array([0, 0, 60.0])
    n = pick.area_normal(v, f, point, [0, 0, 1], S.reach(shp))
    x, y, nn = logo.frame(n, 0)
    surf = pick.surface(v, f, point, np.array([x, y, nn]), S.reach(shp))
    logo.fitted_logo(shp, 2, point, n, 0, 0.2, "wrap", surf)
    assert time.perf_counter() - t < 2.0  # loose: ~0.15 s on a desktop
