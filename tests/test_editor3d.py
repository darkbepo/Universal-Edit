"""3D viewer: the Qt-free model (loading, measurements, buffers) and the orbit camera."""

import math

import numpy as np
import pytest

from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.editor3d.camera import VIEWS, OrbitCamera
from umc.editor3d.mesh import describe, load_view_model

trimesh = pytest.importorskip("trimesh")
FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture
def box_stl(tmp_path):
    p = tmp_path / "box.stl"
    mesh = trimesh.creation.box((20, 10, 5))
    mesh.apply_translation((100, 50, 30))  # off the bed on purpose
    p.write_bytes(mesh.export(file_type="stl"))
    return p


def test_load_stands_on_the_bed(box_stl):
    m = load_view_model(box_stl, "stl")
    lo, hi = m.bounds
    assert np.allclose(lo, (-10, -5, 0)) and np.allclose(hi, (10, 5, 5))
    assert m.size == pytest.approx((20, 10, 5))
    assert m.triangles == 12 and m.parts == 1
    assert m.watertight and m.volume_mm3 == pytest.approx(1000)


def test_describe_rows(box_stl):
    rows = dict(describe(load_view_model(box_stl, "stl")))
    assert rows["Width (X)"] == "20.0 mm" and rows["Height (Z)"] == "5.0 mm"
    assert rows["Triangles"] == "12" and rows["Watertight"] == "Yes"
    assert rows["Volume"] == "1.00 cm³"


def test_open_mesh_is_not_watertight(tmp_path):
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.update_faces(np.arange(len(mesh.faces)) != 0)  # punch a hole
    p = tmp_path / "open.stl"
    p.write_bytes(mesh.export(file_type="stl"))
    m = load_view_model(p, "stl")
    assert not m.watertight and m.volume_mm3 is None
    assert "Volume" not in dict(describe(m))


def test_several_parts_glb(tmp_path):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box((0.01, 0.01, 0.01)), node_name="a")
    scene.add_geometry(
        trimesh.creation.icosphere(radius=0.005),
        node_name="b",
        transform=trimesh.transformations.translation_matrix((0.03, 0, 0)),
    )
    p = tmp_path / "two.glb"
    p.write_bytes(scene.export(file_type="glb"))
    m = load_view_model(p, "glb")
    assert m.parts == 2
    assert m.size[0] == pytest.approx(10 / 2 + 30 + 5, abs=0.01)  # metres → mm


def test_step_fixture():
    f = detect(FIXTURES / "cube-10mm.stp")
    pytest.importorskip("cascadio")
    m = load_view_model(f.path, f.ext)
    assert m.size == pytest.approx((10, 10, 10), abs=0.01)


def test_buffers(box_stl):
    m = load_view_model(box_stl, "stl")
    tri = m.triangle_buffer()
    assert tri.shape == (36, 6) and tri.dtype == np.float32
    assert np.allclose(np.linalg.norm(tri[:, 3:], axis=1), 1)
    edges = m.edge_buffer()
    assert edges.shape == (2 * 18, 3)  # a triangulated box has 12 + 6 diagonal edges


def test_not_a_model(tmp_path):
    p = tmp_path / "bad.stl"
    p.write_bytes(b"not a model at all")
    with pytest.raises(ConversionError):
        load_view_model(p, "stl")


# ---------------------------------------------------------------- camera


def project(cam: OrbitCamera, point, aspect=1.0):
    clip = cam.projection_matrix(aspect) @ cam.view_matrix() @ np.array([*point, 1.0])
    return clip[:3] / clip[3]


def test_target_is_in_the_middle_of_the_screen():
    cam = OrbitCamera()
    cam.fit((-10, -10, 0), (10, 10, 20))
    assert np.allclose(project(cam, cam.target)[:2], 0, atol=1e-9)


@pytest.mark.parametrize("view", list(VIEWS))
def test_fit_keeps_the_box_on_screen(view):
    cam = OrbitCamera()
    cam.set_view(view)
    lo, hi = np.array([-30, -5, 0]), np.array([30, 5, 12])
    for aspect in (0.5, 1.0, 2.0):
        cam.fit(lo, hi, aspect)
        for corner in np.array(np.meshgrid(*zip(lo, hi, strict=True))).T.reshape(-1, 3):
            ndc = project(cam, corner, aspect)
            assert np.all(np.abs(ndc) <= 1.0), (view, aspect, corner, ndc)


def test_presets_look_the_right_way():
    cam = OrbitCamera()
    cam.set_view("front")
    assert np.allclose(cam.direction(), (0, -1, 0), atol=1e-9)
    assert np.allclose(cam.up(), (0, 0, 1), atol=1e-9)
    cam.set_view("top")  # straight down, +Y up on screen
    assert np.allclose(cam.direction(), (0, 0, 1), atol=1e-9)
    assert np.allclose(cam.up(), (0, 1, 0), atol=1e-9)
    # +X is to the right in the front view
    cam.set_view("front")
    cam.fit((-1, -1, -1), (1, 1, 1))
    assert project(cam, (1, 0, 0))[0] > 0


def test_orbit_pan_zoom():
    cam = OrbitCamera()
    cam.fit((-10, -10, -10), (10, 10, 10))
    d = cam.distance
    cam.zoom(0.5)
    assert cam.distance == pytest.approx(d * 0.5)
    cam.zoom(1e-9)
    assert cam.distance == pytest.approx(cam.radius * 0.05)
    cam.orbit(0, 500)
    assert cam.pitch == 90
    cam.orbit(200, 0)
    assert -180 <= cam.yaw < 180
    cam.set_view("front")
    cam.fit((-10, -10, -10), (10, 10, 10))
    before = project(cam, (0, 0, 0))
    cam.pan(50, 0, 500)  # drag right: the scene follows the pointer
    assert project(cam, (0, 0, 0))[0] > before[0]
    assert math.isfinite(cam.distance)
