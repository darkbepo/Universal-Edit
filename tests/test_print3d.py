"""3D print tools: arrange (``umc.editor3d.arrange``), check & repair (``meshcheck``), filament.
web/tests/print3d.test.ts checks the same shared cases against the website's ports."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from umc.editor3d import arrange, filament, meshcheck
from umc.editor3d import scene as S
from umc.editor3d.mesh import ViewModel

pytest.importorskip("mapbox_earcut")

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "print3d_cases.json"


def data() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def indexed(flat):
    v = np.array(flat, np.float64).reshape(-1, 3)
    return v, np.arange(len(v)).reshape(-1, 3)


def test_shared_cases_are_current(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mk", ROOT / "scripts" / "make_print3d_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path / "cases.json")
    mod.main()
    assert json.loads((tmp_path / "cases.json").read_text(encoding="utf-8")) == data()


def test_every_repaired_model_is_clean_or_says_why():
    for c in data()["models"]:
        v, f = indexed(c["soup"])
        x = meshcheck.repair(v, f)
        after = meshcheck.check(x.vertices, x.faces)
        if c["name"] == "fin":  # folds into itself: not something Repair can mend
            assert after.problems and "see the check" in meshcheck.repaired_note(x, after)
        else:
            assert not after.problems, c["name"]
            assert after.closed, c["name"]


def test_repair_keeps_a_clean_model_as_it_is():
    v, f = indexed(data()["models"][0]["soup"])
    x = meshcheck.repair(v, f)
    assert not x.changed and len(x.faces) == 12 and len(x.vertices) == 8


def test_weld_merges_negative_zero():
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [-0.0, 0, 0], [0, 1, 0], [1, 1, 0]])
    wv, wf = meshcheck.weld(v, np.arange(6).reshape(2, 3))
    assert len(wv) == 4 and wf.tolist() == [[0, 1, 2], [0, 2, 3]]


def test_overhang_angle_setting():
    v, f = indexed(next(c for c in data()["models"] if c["name"] == "overhang")["soup"])
    assert meshcheck.check(v, f).overhang_area == pytest.approx(150)
    # a 60° slope: needs support at 45°, not at 70°
    tri = [[0, 0, 5], [10, 0, 5], [0, 10, 5 + 10 * np.tan(np.radians(30))]]
    v2 = np.array(tri[::-1], float)
    f2 = np.array([[0, 1, 2]])
    v2 = np.vstack([v2, [[0, 0, 0], [1, 0, 0], [0, 1, 0]]])  # something on the bed
    f2 = np.vstack([f2, [[3, 4, 5]]])
    assert meshcheck.check(v2, f2, 45).overhang_faces == [0]
    assert meshcheck.check(v2, f2, 70).overhang_faces == []


def test_arrange_rotations_keep_the_model_on_the_bed():
    v, f = indexed(data()["models"][0]["soup"])
    for axis in "xyz":
        for deg in (90, -90, 180, 33):
            m = arrange.settle(v, arrange.rotate(axis, deg, arrange.centre_of(v)))
            nv, nf = arrange.apply(v, f, m)
            lo, hi = arrange.bounds(nv)
            assert lo[2] == pytest.approx(0, abs=1e-9)
            assert (lo[0] + hi[0]) / 2 == pytest.approx(0, abs=1e-9)
            assert meshcheck.check(nv, nf).volume == pytest.approx(1000)


def test_lay_flat_puts_the_face_down():
    for n in ([1, 0, 0], [0, 0, 1], [0, 0, -1], [0.3, -0.4, 0.5], [-1, 2, 0]):
        m = arrange.lay_flat(n)
        out = np.array(arrange.apply_normal(m, n))
        assert out == pytest.approx([0, 0, -1], abs=1e-9)


def test_mirror_keeps_triangles_facing_out():
    v, f = indexed(data()["models"][0]["soup"])
    nv, nf = arrange.apply(v, f, arrange.mirror("z", arrange.centre_of(v)))
    r = meshcheck.check(nv, nf)
    assert not r.problems and r.volume == pytest.approx(1000)


def test_scene_arranged_moves_logos_too():
    v, f = indexed(data()["models"][0]["soup"])
    base = ViewModel("cube", v, f, watertight=True, volume_mm3=1000.0)
    pic = S.Picture("dot.png", bytes([0, 0, 0, 255] * 16), 4, 4)
    lg = S.build_logo(pic, S.LogoSettings(width=4), (0, 0, 10), (0, 0, 1), base)
    sc = S.Scene(base, (lg,))
    m = arrange.settle(v, arrange.rotate("x", 90, arrange.centre_of(v)))
    out = sc.arranged(m)
    assert out.logos[0].normal == pytest.approx((0, -1, 0), abs=1e-9)
    assert out.base.volume_mm3 == pytest.approx(1000)
    # the logo's mesh moved with the model: its points are on the new -Y side
    assert out.logos[0].meshes[0].vertices[:, 1].min() < -5
    # scaling 2× changes the volume 8×
    big = sc.arranged(arrange.scale(2, 2, 2))
    assert big.base.volume_mm3 == pytest.approx(8000)


def test_fit_and_units():
    assert arrange.fit_note((0, 0, 0), (300, 10, 10), (256, 256, 256)) == (
        "Too big for the bed: 44.0 mm too wide."
    )
    assert arrange.fit_note((100, 0, 0), (140, 10, 10), (256, 256, 256)).startswith("Hangs 12.0")
    assert arrange.UNIT_FIXES["inch"] == 25.4
    assert [b[0] for b in arrange.BEDS][:2] == ["Prusa MK4", "Prusa MINI"]


def test_filament_estimate():
    e = filament.estimate(1000, 0, "PLA", 0)
    assert e["grams"] == 0 and e["solid_grams"] == pytest.approx(1.24)
    e = filament.estimate(1000, 10_000, "PLA", 0.15)  # walls hold more than the volume
    assert e["grams"] == pytest.approx(1.24)
    assert filament.estimate(1000, 0, "PLA", 1)["metres"] == pytest.approx(
        1000 / (np.pi * 0.875**2) / 1000
    )


def _bounds(meshes):
    v = np.concatenate([m.vertices for m in meshes])
    return v.min(axis=0), v.max(axis=0)


def test_change_after_arrange_rebuilds_the_same_logo():
    """Review: after turning / scaling, Change… snapped the logo back to its old size and
    direction. Rebuilding from the stored settings must give the logo that's there."""
    v, f = indexed(box_soup(-20, -10, 0, 20, 10, 10))
    base = ViewModel("box", v, f, watertight=True, volume_mm3=8000.0)
    pic = S.Picture("bar.png", bytes([0, 0, 0, 255] * 8), 4, 2)  # a 2:1 bar shows its direction
    lg = S.build_logo(pic, S.LogoSettings(width=30, thickness=2), (0, 0, 10), (0, 0, 1), base)
    sc = S.Scene(base, (lg,))
    for m in (
        arrange.rotate("z", 90, arrange.centre_of(v)),
        arrange.scale(2, 2, 2),
        arrange.rotate("x", -90, arrange.centre_of(v)),
    ):
        sc = sc.arranged(arrange.settle(sc.positions(), m))
    now = sc.logos[0]
    assert now.settings.width == pytest.approx(60) and now.width == pytest.approx(60)
    again = S.build_logo(now.picture, now.settings, now.point, now.normal, sc.base)
    lo1, hi1 = _bounds(now.meshes)
    lo2, hi2 = _bounds(again.meshes)
    assert lo1 == pytest.approx(lo2, abs=1e-6) and hi1 == pytest.approx(hi2, abs=1e-6)


def test_mirroring_makes_logos_again_unmirrored():
    v, f = indexed(box_soup(-20, -10, 0, 20, 10, 10))
    base = ViewModel("box", v, f, watertight=True, volume_mm3=8000.0)
    pic = S.Picture("bar.png", bytes([0, 0, 0, 255] * 8), 4, 2)
    lg = S.build_logo(pic, S.LogoSettings(width=20, rotation=30), (5, 0, 10), (0, 0, 1), base)
    out = S.Scene(base, (lg,)).arranged(arrange.mirror("x", arrange.centre_of(v)))
    now = out.logos[0]
    assert now.point == pytest.approx((-5, 0, 10))
    fresh = S.build_logo(now.picture, now.settings, now.point, now.normal, out.base)
    assert np.allclose(now.meshes[0].vertices, fresh.meshes[0].vertices)  # made, not mirrored
    assert meshcheck.check(now.meshes[0].vertices, now.meshes[0].faces).volume > 0


def test_undo_keeps_a_bounded_amount_of_models(monkeypatch):
    monkeypatch.setattr(S, "UNDO_BYTES", 60_000)
    v, f = indexed(box_soup(-5, -5, 0, 5, 5, 10))
    big = np.repeat(v, 20, axis=0)  # ~ 18 kB a step
    h = S.History(S.Scene(ViewModel("m", big, f)))
    for i in range(10):
        h.push(h.scene.arranged(arrange.translate(i + 1, 0, 0)))
    assert 0 < len(h.undo_stack) < 4 and h.undo()


def box_soup(x0, y0, z0, x1, y1, z1):
    c = [[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return [x for a, b, cc, d in quads for t in ((a, b, cc), (a, cc, d)) for i in t for x in c[i]]
