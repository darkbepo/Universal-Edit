"""3D viewer GUI (offscreen): Convert | Edit for 3D models, the info panel, view controls."""

import numpy as np
import pytest

pytest.importorskip("pytestqt")
trimesh = pytest.importorskip("trimesh")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from umc.gui.editor3d.gl_view import grid_lines  # noqa: E402


@pytest.fixture
def models(tmp_path):
    box = tmp_path / "box.stl"
    box.write_bytes(trimesh.creation.box((20, 10, 5)).export(file_type="stl"))
    ball = tmp_path / "ball.obj"
    ball.write_text(trimesh.creation.icosphere(radius=8).export(file_type="obj"))
    return box, ball


@pytest.fixture
def window(qtbot, tmp_path):
    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    w._set_output_dir(tmp_path)
    qtbot.addWidget(w)
    w.show()
    yield w
    if w.viewer3d is not None:
        w.viewer3d.wait_idle()


def open_viewer(window, qtbot, paths):
    window.add_files([str(p) for p in paths])
    assert window.edit_mode.isEnabled()
    window.edit_mode.click()
    page = window.viewer3d
    qtbot.waitUntil(lambda: bool(page.info()), timeout=30000)
    return page


def test_edit_enabled_for_models_only(window, models, media):
    window.add_files([str(models[0])])
    assert window.edit_mode.isEnabled()
    assert "3D viewer" in window.edit_mode.toolTip()
    window.add_files([str(media / "img0.png")])  # mixed → nothing to edit
    assert not window.edit_mode.isEnabled()


def test_open_view_and_back(window, models, qtbot):
    window.resize(620, 780)
    page = open_viewer(window, qtbot, models)
    assert window.stack.currentWidget() is page
    info = page.info()
    assert info["Width (X)"] == "20.0 mm" and info["Watertight"] == "Yes"
    assert page.file_box.isVisibleTo(page) and page.file_box.count() == 2
    page.file_box.setCurrentIndex(1)  # the sphere
    qtbot.waitUntil(lambda: page.info().get("Width (X)") != "20.0 mm", timeout=30000)
    assert page.title.text() == "ball.obj"
    page.back_requested.emit()
    assert window.stack.currentIndex() == 0 and window.convert_mode.isChecked()
    assert (window.width(), window.height()) == (620, 780)


def test_view_controls(window, models, qtbot):
    page = open_viewer(window, qtbot, models[:1])
    cam = page.view.camera
    page.view_buttons["top"].click()
    assert cam.pitch == 90
    page.view_buttons["front"].click()
    assert (cam.yaw, cam.pitch) == (-90, 0)
    page.style_buttons["wireframe"].click()
    assert page.view.style == "wireframe"
    page.grid_box.setChecked(False)
    assert not page.view.show_grid
    d = cam.distance
    page.view.camera.zoom(3)
    page.fit_button.click()
    assert cam.distance == pytest.approx(d)


def test_bad_file_shows_a_message(window, tmp_path, qtbot):
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"nonsense")
    window.add_files([str(bad)])
    window.edit_mode.click()
    page = window.viewer3d
    qtbot.waitUntil(lambda: "bad.stl" in page.message.text(), timeout=30000)
    assert page.center.currentWidget() is page.placeholder
    assert not page.info()


def test_renders_pixels(window, models, qtbot):
    page = open_viewer(window, qtbot, models[:1])
    if page.view.failed:
        assert page.center.currentWidget() is page.placeholder  # says why, no blank area
        assert "OpenGL" in page.placeholder.text()
        pytest.skip(f"no OpenGL here: {page.view.failed}")
    qtbot.waitUntil(lambda: page.view._ready, timeout=5000)
    img = page.view.grabFramebuffer()
    bg = img.pixelColor(2, 2)
    centre = img.pixelColor(img.width() // 2, img.height() // 2)
    assert centre != bg  # the model is drawn in the middle


def test_grid_lines_cover_the_model():
    minor, major, step = grid_lines(np.array([-60, -10, 0]), np.array([60, 10, 5]))
    assert step == 10
    half = np.abs(np.concatenate([minor, major])).max()
    assert half >= 60 and np.all(minor[:, 2] == 0)
