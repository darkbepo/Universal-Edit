"""3D viewer: the Arrange and Check panels, printer bed, filament rows (offscreen: face clicks go
through ``lay_flat_on``, which the real click handler calls)."""

import numpy as np
import pytest

pytest.importorskip("pytestqt")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("mapbox_earcut")

from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402

from umc.editor3d import arrange as A  # noqa: E402
from umc.editor3d import pick  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    QCoreApplication.setOrganizationName("UMC-tests")
    QSettings().remove("viewer3d")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    w._set_output_dir(tmp_path)
    qtbot.addWidget(w)
    w.show()
    yield w
    if w.viewer3d is not None:
        for h in w.viewer3d.histories.values():
            h.mark_saved()
        w.viewer3d.wait_idle()


def open_model(window, qtbot, path):
    window.add_files([str(path)])
    window.edit_mode.click()
    page = window.viewer3d
    qtbot.waitUntil(lambda: bool(page.info()), timeout=30000)
    return page


@pytest.fixture
def slab(tmp_path):
    p = tmp_path / "slab.stl"
    p.write_bytes(trimesh.creation.box((40, 20, 10)).export(file_type="stl"))
    return p


def size(page):
    i = page.info()
    return [float(i[k].split()[0]) for k in ("Width (X)", "Depth (Y)", "Height (Z)")]


def test_arrange_turn_scale_mirror_undo(window, qtbot, slab):
    page = open_model(window, qtbot, slab)
    page.arrange_button.click()
    assert page.panel == "arrange" and page.side.currentWidget() is page.arrange_panel
    ap = page.arrange_panel
    assert ap.size_boxes["x"].value() == pytest.approx(40)
    page.rotate("y", 90)  # stands up
    assert size(page) == pytest.approx([10, 20, 40])
    assert page.view.model.vertices[:, 2].min() == pytest.approx(0, abs=1e-9)
    assert ap.size_boxes["z"].value() == pytest.approx(40)
    ap.size_boxes["z"].setValue(20)  # typed: proportions kept
    assert size(page) == pytest.approx([5, 10, 20])
    page.units("inch")
    assert size(page) == pytest.approx([127, 254, 508])
    assert "too" in page.info()["Printer bed"]
    page.undo()
    page.undo()
    assert size(page) == pytest.approx([10, 20, 40])
    page.mirror("x")
    assert page.info()["Watertight"] == "Yes"
    assert page.base.volume_mm3 == pytest.approx(8000)
    page.move_to(30, -10)
    lo, hi = A.bounds(page.view.model.vertices)
    assert ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2) == pytest.approx((30, -10))
    page.centre()
    lo, hi = A.bounds(page.view.model.vertices)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(0, abs=1e-9)
    assert page.history.dirty
    ap.done.emit()
    assert page.panel == "" and page.side.currentIndex() == 0


def test_lay_flat_on_a_slanted_face(window, qtbot, tmp_path):
    wedge = trimesh.creation.box((20, 20, 20))
    wedge.apply_transform(trimesh.transformations.rotation_matrix(np.radians(30), [1, 0, 0]))
    p = tmp_path / "tilted.stl"
    p.write_bytes(wedge.export(file_type="stl"))
    page = open_model(window, qtbot, p)
    page.open_panel("arrange")
    page.arrange_panel.flat_button.click()
    assert page.laying_flat and "Click the face" in page.message.text()
    m = page.view.model
    centre = np.array(A.centre_of(m.vertices))
    hit = pick.hit(m.vertices, m.faces, centre + [0, 0, 100], [0, 0, -1])  # a top face
    page.lay_flat_on(hit[0], np.array([0, 0, -1.0]))
    assert not page.laying_flat and not page.arrange_panel.flat_button.isChecked()
    assert size(page) == pytest.approx([20, 20, 20], abs=1e-6)  # square again


def test_bed_choice_is_remembered(window, qtbot, slab):
    page = open_model(window, qtbot, slab)
    page.open_panel("arrange")
    ap = page.arrange_panel
    ap.bed_box.setCurrentIndex(ap.bed_box.count() - 1)  # custom
    assert ap.custom.isVisibleTo(ap)
    ap.bed_w.setValue(30)
    assert page.bed[1] == 30 and "10.0 mm too wide" in page.info()["Printer bed"]
    assert page.view.bed == (30.0, ap.bed_d.value(), ap.bed_h.value())
    from umc.gui.editor3d.viewer_page import load_bed

    assert load_bed()[1] == 30


def test_filament_rows(window, qtbot, slab):
    page = open_model(window, qtbot, slab)
    info = page.info()
    assert info["Filament (PLA, 15 % infill)"].startswith("≈ ")
    assert info["Printed solid"] == "≈ 10 g"  # 8 cm³ × 1.24
    page.material_box.setCurrentText("PETG")
    page.infill_box.setValue(100)
    assert page.info()["Filament (PETG, 100 % infill)"].startswith("≈ 10 g")


def test_check_and_repair(window, qtbot, tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    holed = trimesh.Trimesh(box.vertices, box.faces[2:], process=False)  # one side open
    p = tmp_path / "holed.stl"
    p.write_bytes(holed.export(file_type="stl"))
    page = open_model(window, qtbot, p)
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.check_button.click()
    cp = page.check_panel
    assert "hole" in cp.result.text() and cp.repair_button.isEnabled()
    with qtbot.waitSignal(page.repaired, timeout=30000):
        cp.repair_button.click()
    qtbot.waitUntil(lambda: "No problems" in cp.result.text(), timeout=30000)
    assert "closed 1 hole" in cp.repaired.text()
    assert page.info()["Watertight"] == "Yes" and not cp.repair_button.isEnabled()
    page.undo()
    qtbot.waitUntil(lambda: "hole" in cp.result.text(), timeout=30000)


def test_overhangs_in_red(window, qtbot, tmp_path):
    a = trimesh.creation.box((10, 10, 10))
    a.apply_translation((0, 0, 5))
    b = trimesh.creation.box((20, 10, 10))
    b.apply_translation((5, 0, 15))  # sticks out 10 mm over thin air
    p = tmp_path / "t.stl"
    p.write_bytes(trimesh.util.concatenate([a, b]).export(file_type="stl"))
    page = open_model(window, qtbot, p)
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.open_panel("check")
    assert page.view.highlight is not None and len(page.view.highlight.faces) >= 2
    assert "cm² of overhang" in page.check_panel.overhang.text()
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.check_panel.show_overhangs.setChecked(False)
    assert page.view.highlight is None
    page.close_panel()
    assert page.view.highlight is None


def test_panels_and_logo_editing_take_turns(window, qtbot, slab):
    page = open_model(window, qtbot, slab)
    page.open_panel("arrange")
    page.start_logo()
    assert page.panel == "" and page.editing is not None
    assert not page.arrange_button.isEnabled() and not page.check_button.isEnabled()
    page.cancel_logo()
    assert page.arrange_button.isEnabled()


def test_save_arranged_model(window, qtbot, slab, tmp_path):
    page = open_model(window, qtbot, slab)
    page.rotate("x", 90)
    out = tmp_path / "out.stl"
    names = []
    page.ask_save_path = lambda suggested: names.append(suggested.name) or str(out)
    with qtbot.waitSignal(page.saved, timeout=30000):
        assert page.save_as()
    assert names == ["slab edited.3mf"] and not page.dirty
    m = trimesh.load(out, force="mesh")
    assert m.extents == pytest.approx([40, 10, 20])


def test_nothing_moved_is_no_undo_step(window, qtbot, slab):
    page = open_model(window, qtbot, slab)
    page.rotate("z", 360)
    page.scale_by(1, 1, 1)
    page.arrange(A.identity())
    assert not page.history.undo_stack and not page.history.dirty


def test_repair_dropped_when_the_model_changed(window, qtbot, tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    holed = trimesh.Trimesh(box.vertices, box.faces[2:], process=False)
    p = tmp_path / "holed.stl"
    p.write_bytes(holed.export(file_type="stl"))
    page = open_model(window, qtbot, p)
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.open_panel("check")
    with qtbot.waitSignal(page.repaired, timeout=30000):
        page.repair()
        page.rotate("z", 90)  # changes the model before the repair comes back
    assert "press Repair again" in page.check_panel.repaired.text()
    assert page.check_panel.repair_button.isEnabled()
    assert len(page.history.undo_stack) == 1  # only the turn


def test_closing_the_panel_drops_a_running_repair(window, qtbot, tmp_path):
    box = trimesh.creation.box((20, 20, 20))
    holed = trimesh.Trimesh(box.vertices, box.faces[2:], process=False)
    p = tmp_path / "holed.stl"
    p.write_bytes(holed.export(file_type="stl"))
    page = open_model(window, qtbot, p)
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.open_panel("check")
    page.repair()
    page.close_panel()  # e.g. another file was chosen
    with qtbot.waitSignal(page.checked, timeout=30000):
        page.open_panel("check")
    cp = page.check_panel
    assert cp.repaired.text() == "" and cp.repair_button.isEnabled()
    qtbot.wait(500)  # the dropped repair finishes in the background: nothing is applied
    assert not page.history.undo_stack and "hole" in cp.result.text()
