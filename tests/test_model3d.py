from pathlib import Path

import numpy as np
import pytest
import trimesh

from tests.conftest import convert
from umc.core.detect import MediaFamily, detect
from umc.core.registry import targets_for

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def box_stl(tmp_path):
    p = tmp_path / "box.stl"
    p.write_bytes(trimesh.creation.box((20, 10, 5)).export(file_type="stl"))
    return p


def extents(path):
    return np.round(trimesh.load(path, force="mesh").extents, 3).tolist()


def test_detect_and_targets(box_stl):
    assert detect(box_stl).family is MediaFamily.MODEL
    assert detect(FIXTURES / "cube-10mm.stp").ext == "step"
    ids = {t.id for t in targets_for([detect(box_stl)])}
    assert ids == {"3mf", "obj", "ply", "glb"}


@pytest.mark.parametrize("fmt", ["3mf", "obj", "ply", "glb"])
def test_round_trip_keeps_size(box_stl, tmp_path, fmt):
    [out] = convert([box_stl], fmt, tmp_path)
    [back] = convert([out], "stl", tmp_path / "back")
    assert extents(back) == [20, 10, 5]


def test_units_inches_to_mm(box_stl, tmp_path):
    [out] = convert([box_stl], "3mf", tmp_path, units="inch")
    assert extents(out) == [508, 254, 127]


def test_centre_on_bed_and_scale(box_stl, tmp_path):
    [out] = convert([box_stl], "obj", tmp_path, scale_percent=50)
    mesh = trimesh.load(out, force="mesh")
    assert np.allclose(mesh.extents, [10, 5, 2.5])
    assert np.allclose(mesh.bounds[0], [-5, -2.5, 0])


def test_ascii_stl(box_stl, tmp_path):
    [out] = convert([box_stl], "obj", tmp_path)
    [stl] = convert([out], "stl", tmp_path / "s", stl_format="ascii")
    assert stl.read_bytes().startswith(b"solid")


@pytest.mark.parametrize("name", ["cube-10mm.stp", "cube-10mm.igs"])
def test_cad_to_printable_mesh(name, tmp_path):
    [out] = convert([FIXTURES / name], "stl", tmp_path)
    mesh = trimesh.load(out, force="mesh")
    assert np.allclose(mesh.extents, [10, 10, 10], atol=1e-3)
    assert mesh.is_watertight


def test_cad_to_3mf(tmp_path):
    [out] = convert([FIXTURES / "cube-10mm.stp"], "3mf", tmp_path, detail="fine")
    assert extents(out) == [10, 10, 10]
