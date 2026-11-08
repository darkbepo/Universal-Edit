"""End-to-end through the real window (offscreen)."""

import pytest

pytest.importorskip("pytestqt")


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    w._set_output_dir(tmp_path)
    qtbot.addWidget(w)
    return w


def _pick(window, target_id):
    for b in window.picker._group.buttons():
        if window.picker._targets[window.picker._group.id(b)].id == target_id:
            b.click()
            return
    raise AssertionError(f"{target_id} not shown")


def test_offers_formats_for_dropped_images(window, media):
    window.add_files([str(media / "img0.png"), str(media / "img1.png")])
    ids = {t.id for t in window.picker._targets.values()}
    assert "jpg" in ids and "slideshow:mp4" in ids


def test_mixed_files_show_error(window, media):
    window.add_files([str(media / "img0.png"), str(media / "doc.md")])
    assert "one kind" in window.input_error.text()
    assert not window.picker._targets


def test_settings_panel_follows_target(window, media):
    window.add_files([str(media / "img0.png")])
    _pick(window, "jpg")
    assert "quality" in window.settings_panel.values()
    _pick(window, "slideshow:mp4")
    assert "seconds_per_image" in window.settings_panel.values()


def test_convert_end_to_end(window, media, tmp_path, qtbot):
    window.add_files([str(media / "img0.png")])
    _pick(window, "webp")
    window.convert_button.click()
    qtbot.waitUntil(lambda: not window.busy, timeout=20000)
    assert window.last_outputs
    assert (tmp_path / "img0.webp").exists()
    assert "Done" in window.status.text()
