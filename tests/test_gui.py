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
    w.resize(1280, 800)
    w.show()
    return w


def _pick(window, target_id):
    tile = window.picker.tile_for(target_id)
    assert tile is not None, f"{target_id} not shown"
    tile.click()


def test_offers_formats_for_dropped_images(window, media):
    window.add_files([str(media / "img0.png"), str(media / "img1.png")])
    ids = {t.id for t in window.picker._targets.values()}
    assert "jpg" in ids and "slideshow:mp4" in ids


def test_mixed_files_offer_archive(window, media):
    window.add_files([str(media / "img0.png"), str(media / "doc.md")])
    assert not window.input_error.isVisible() and not window.input_error.text()
    assert {t.id for t in window.picker._targets.values()} == {"pack:zip", "pack:7z", "pack:tar.gz"}


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
    assert window.file_list.states() == ["done"]
    assert window.results_card.isVisible() and window.results_list.count() == 1
    assert window.results_list.item(0).text().startswith("img0.webp")
    assert "Results · 1 file" in window.results_label.text()


def test_empty_window_is_one_big_drop_zone(window, media):
    assert window.right.isHidden() and not window.files_card.isVisible()
    assert window.drop.height() > 400
    window.add_files([str(media / "img0.png")])
    assert window.right.isVisible() and window.files_card.isVisible()
    assert window.drop.compact and window.drop.height() < 60


def test_settings_tab_replaces_formats_with_a_strip(window, media):
    window.add_files([str(media / "img0.png")])
    _pick(window, "jpg")
    tabs = window.tabs
    assert tabs.convert_pane.isVisible() and tabs.strip.isHidden() and tabs.cog.isVisible()
    tabs.cog.click()
    assert tabs.settings_open and tabs.convert_pane.isHidden() and tabs.strip.isVisible()
    assert tabs.strip.text() == "JPG" and tabs.settings_title.text() == "Settings · JPG"
    assert window.settings_panel.isVisible()
    tabs.strip.click()
    assert not tabs.settings_open and tabs.convert_pane.isVisible() and tabs.strip.isHidden()


def test_no_settings_means_no_cog(window):
    window.tabs.set_target("ZIP", False)
    assert window.tabs.cog.isHidden()
    window.tabs.show_settings()
    assert not window.tabs.settings_open


def test_category_cards_fold_and_remember(window, media):
    window.add_files([str(media / "img0.png")])
    card = window.picker.card_for("Archive")
    assert card is not None and card.is_open and card.body.isVisible()
    card.header.click()
    assert not card.is_open and card.body.isHidden()
    assert card.badge.text() == str(len(card.tiles))
    window.clear_files()
    window.add_files([str(media / "img1.png")])
    card = window.picker.card_for("Archive")
    assert not card.is_open  # remembered
    card.header.click()
    assert card.is_open


def test_selected_format_shows_on_its_card(window, media):
    window.add_files([str(media / "img0.png")])
    _pick(window, "webp")
    card = window.picker.card_for(window.target.group)
    assert card.badge.text() == "WEBP" and card.badge.isVisible()


def test_queue_rows_show_progress_per_file(window, media):
    window.add_files([str(media / f"img{i}.png") for i in range(3)])
    q = window.file_list
    assert q.states() == ["idle"] * 3
    q.set_progress([1.0, 0.5, 0.0])
    assert q.states() == ["done", "running", "waiting"]
    q.mark_failed("Broken file")
    assert q.states() == ["done", "error", "idle"]
    q.reset_states()
    assert q.states() == ["idle"] * 3


def test_queue_remove_button(window, media):
    window.add_files([str(media / f"img{i}.png") for i in range(3)])
    window.remove_row(1)
    assert [f.name for f in window.files] == ["img0.png", "img2.png"]
    assert window.file_list.paths() == [str(f.path) for f in window.files]
    assert "2 images" in window.files_label.text()


def test_failed_conversion_marks_the_row(window, tmp_path, qtbot):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not really a png")
    window.add_files([str(bad)])
    _pick(window, "jpg")
    window.convert_button.click()
    qtbot.waitUntil(lambda: not window.busy, timeout=20000)
    assert window.file_list.states() == ["error"]
    assert window.status.objectName() == "Error" and window.results_card.isHidden()


def test_narrow_window_stacks_into_one_column(window, media, qtbot):
    def side_by_side():
        return window.left.geometry().right() < window.right.geometry().left()

    def stacked():
        return window.left.geometry().bottom() < window.right.geometry().top()

    window.add_files([str(media / "img0.png")])
    qtbot.waitUntil(side_by_side)
    window.resize(700, 900)
    assert window.narrow
    qtbot.waitUntil(stacked)
    window.resize(1280, 800)
    qtbot.waitUntil(side_by_side)
