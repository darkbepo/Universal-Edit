"""3D viewer: adding logos (offscreen). The view has no OpenGL here, so clicks on the model are
made with ``place_on`` / ``move_logo_along_ray``, which the real click / drag handlers call."""

import numpy as np
import pytest

pytest.importorskip("pytestqt")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("mapbox_earcut")

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

from umc.editor3d import pick  # noqa: E402


@pytest.fixture
def box_stl(tmp_path):
    p = tmp_path / "box.stl"
    p.write_bytes(trimesh.creation.box((40, 40, 10)).export(file_type="stl"))
    return p


@pytest.fixture
def ring_png(tmp_path):
    p = tmp_path / "ring.png"
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 190, 190), fill="black")
    d.ellipse((60, 60, 140, 140), fill=(0, 0, 0, 0))
    im.save(p)
    return p


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
        for h in w.viewer3d.histories.values():
            h.mark_saved()
        w.viewer3d.wait_idle()


def open_model(window, qtbot, path):
    window.add_files([str(path)])
    window.edit_mode.click()
    page = window.viewer3d
    qtbot.waitUntil(lambda: bool(page.info()), timeout=30000)
    return page


def built(page, qtbot):
    with qtbot.waitSignal(page.logo_built, timeout=30000):
        pass


def add_picture(page, qtbot, png):
    with qtbot.waitSignal(page.logo_built, timeout=30000):
        page.load_picture(png)


def click_top(page):
    base = page.base
    found = pick.hit(base.vertices, base.faces, [2, 3, 100], [0, 0, -1])
    assert found is not None
    page.place_on(found[0], found[1], np.array([0, 0, -1.0]))


def test_logo_on_the_top_face(window, qtbot, box_stl, ring_png):
    page = open_model(window, qtbot, box_stl)
    assert page.logo_button.isEnabled() and not page.save_button.isEnabled()
    page.logo_button.click()
    assert page.editing is not None and page.side.currentWidget() is page.logo_panel
    assert not page.logo_button.isEnabled() and not page.file_box.isEnabled()
    add_picture(page, qtbot, ring_png)
    panel = page.logo_panel
    assert panel.mode.currentData() == "alpha"  # the picture has transparency
    assert page.editing.logo is None and "Click a flat face" in panel.note.text()
    panel.width_box.setValue(20)
    built(page, qtbot)  # re-traced at the new width (after the short pause)
    click_top(page)
    lg = page.editing.logo
    assert lg is not None and lg.overhang == 0 and lg.width == pytest.approx(20)
    v = page.editing.preview.vertices
    assert v[:, 2].min() == pytest.approx(10 - 0.2) and v[:, 2].max() == pytest.approx(12)
    assert v[:, 0].mean() == pytest.approx(2, abs=0.5)
    assert "20.0 × 20.0 mm" in panel.note.text()

    # drag: slide over the plane to (10, -5)
    page.move_logo_along_ray(np.array([10.0, -5.0, 50.0]), np.array([0, 0, -1.0]))
    assert page.editing.point == pytest.approx((10, -5, 10))
    page.move_logo_along_ray(np.array([30.0, 0, 50.0]), np.array([0, 0, -1.0]))  # off the edge
    assert page.editing.logo.overhang > 0 and "hangs over the edge" in panel.note.text()
    page.move_logo_along_ray(np.array([0.0, 0, 50.0]), np.array([0, 0, -1.0]))

    panel.apply_button.click()
    assert page.editing is None and page.side.currentIndex() == 0
    info = page.info()
    assert info["Height (Z)"] == "12.0 mm" and info["Parts"] == "2"
    assert page.logo_list.count() == 1 and page.save_button.isEnabled()
    assert page.history.dirty

    page.undo_button.click()
    assert page.info()["Height (Z)"] == "10.0 mm" and page.logo_list.count() == 0
    page.redo_button.click()
    assert page.info()["Height (Z)"] == "12.0 mm"


def test_change_and_remove(window, qtbot, box_stl, ring_png):
    page = open_model(window, qtbot, box_stl)
    page.start_logo()
    add_picture(page, qtbot, ring_png)
    page.logo_panel.width_box.setValue(10)
    built(page, qtbot)
    click_top(page)
    page.apply_logo()
    page.logo_list.setCurrentRow(0)
    page.edit_selected_logo()
    assert page.editing.index == 0 and page.editing.picture is not None
    built(page, qtbot)  # traced again for the preview
    assert page.view.model is not None and len(page.view.model.faces) == 12  # just the box
    page.logo_panel.set_rotation(45)
    built(page, qtbot)
    page.apply_logo()
    lg = page.history.scene.logos[0]
    assert lg.settings.rotation == 45 and page.logo_list.count() == 1
    page.remove_selected_logo()
    assert page.logo_list.count() == 0 and page.info()["Parts"] == "1"


def test_cancel_and_escape_change_nothing(window, qtbot, box_stl, ring_png):
    page = open_model(window, qtbot, box_stl)
    page.start_logo()
    add_picture(page, qtbot, ring_png)
    click_top(page)
    assert page.editing.logo is not None
    page.logo_panel.cancel_button.click()
    assert page.editing is None and not page.history.dirty and page.view.overlay is None


def test_badge_from_the_empty_viewer_and_save(window, qtbot, ring_png, tmp_path):
    assert window.open_editor("model")  # the start screen's 3D tile
    page = window.viewer3d
    assert page.history is not None and page.base is None
    page.start_logo()
    panel = page.logo_panel
    assert not panel.where.isVisible() and panel.settings().where == "badge"
    add_picture(page, qtbot, ring_png)
    panel.plate.setCurrentIndex(panel.plate.findData("rounded"))
    panel.hole.setChecked(True)
    page._rebuild()
    assert len(page.editing.logo.meshes) == 2
    page.apply_logo()
    assert page.info()["Parts"] == "2" and page.info()["Watertight"] == "Yes"

    out = tmp_path / "badge.3mf"
    page.ask_save_path = lambda suggested: str(out)
    with qtbot.waitSignal(page.saved, timeout=30000) as sig:
        assert page.save_as()
    assert sig.args == [str(out)] and not page.dirty
    loaded = trimesh.load(out, force="mesh")
    assert loaded.bounds[1][2] == pytest.approx(4.0, abs=1e-3)  # plate 2 + logo 2


@pytest.mark.parametrize("ext", ["stl", "obj", "ply", "glb"])
def test_save_formats(window, qtbot, box_stl, ring_png, tmp_path, ext):
    page = open_model(window, qtbot, box_stl)
    page.start_logo()
    add_picture(page, qtbot, ring_png)
    page.logo_panel.width_box.setValue(20)
    built(page, qtbot)
    click_top(page)
    page.apply_logo()
    out = tmp_path / f"out.{ext}"
    page.ask_save_path = lambda suggested: str(out)
    with qtbot.waitSignal(page.saved, timeout=30000):
        page.save_as()
    m = trimesh.load(out, force="mesh")
    scale = 1000 if ext == "glb" else 1
    assert m.bounds[1][2] * scale == pytest.approx(12, abs=1e-3)


def test_leaving_with_unsaved_logos_asks(window, qtbot, box_stl, ring_png):
    page = open_model(window, qtbot, box_stl)
    page.start_logo()
    add_picture(page, qtbot, ring_png)
    click_top(page)
    page.apply_logo()
    asked = []
    page.ask = lambda title, text, buttons: asked.append(text) or "cancel"
    assert not page.maybe_leave()
    assert asked and "box.stl" in asked[0]
    page.ask = lambda *a: "discard"
    assert page.maybe_leave() and not page.dirty


def test_unreadable_picture_says_so(window, qtbot, box_stl, tmp_path):
    page = open_model(window, qtbot, box_stl)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a picture")
    page.start_logo()
    with qtbot.waitSignal(page.logo_built, timeout=30000):
        page.load_picture(bad)
    assert "Couldn't read" in page.logo_panel.note.text()


def test_picture_without_shape_says_so(window, qtbot, box_stl, tmp_path):
    page = open_model(window, qtbot, box_stl)
    blank = tmp_path / "blank.png"
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(blank)
    page.start_logo()
    add_picture(page, qtbot, blank)
    assert "no clear shape" in page.logo_panel.note.text()
    page.apply_logo()
    assert page.editing is not None  # nothing to apply: the panel stays


def test_svg_logo(window, qtbot, box_stl, tmp_path):
    page = open_model(window, qtbot, box_stl)
    svg = tmp_path / "star.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<polygon points="50,5 61,40 98,40 68,62 79,96 50,75 21,96 32,62 2,40 39,40"/></svg>'
    )
    page.start_logo()
    add_picture(page, qtbot, svg)
    assert page.editing.picture.w == 1024 and page.editing.shape is not None


def test_real_click_and_drag_in_the_gl_view(window, qtbot, box_stl, ring_png):
    """Needs OpenGL (runs under xvfb + xcb; skipped offscreen)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor

    from umc.gui import theme

    page = open_model(window, qtbot, box_stl)
    if page.view.failed:
        pytest.skip(f"no OpenGL here: {page.view.failed}")
    qtbot.waitUntil(lambda: page.view._ready, timeout=5000)
    page.view_buttons["top"].click()
    page.start_logo()
    add_picture(page, qtbot, ring_png)
    page.logo_panel.width_box.setValue(16)
    built(page, qtbot)
    view = page.view
    centre = QPoint(view.width() // 2, view.height() // 2)
    with qtbot.waitSignal(page.logo_built, timeout=10000):
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=centre)
    assert page.editing.point == pytest.approx((0, 0, 10), abs=1.0)
    view.repaint()
    img = view.grabFramebuffer()
    accent = QColor(theme.palette()["accent"])
    ring = img.pixelColor(centre.x() + int(view.width() * 0.0), centre.y())
    assert ring != img.pixelColor(2, 2)
    # drag the logo (press on its ring, not the hole) a few pixels to the right
    px_per_mm = view.height() / (2 * view.camera.distance * 0.3153)  # tan(17.5°)
    start = QPoint(centre.x() + int(6 * px_per_mm), centre.y())
    assert page._grab_test(start.x(), start.y())
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view, start + QPoint(20, 0))
    qtbot.mouseMove(view, start + QPoint(40, 0))
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
    # the logo keeps the spot it was grabbed by under the pointer: it moved by the drag only
    x, _y, z = page.editing.point
    assert 1 < x < 40 / px_per_mm + 1 and z == pytest.approx(10)
    assert accent.isValid()
