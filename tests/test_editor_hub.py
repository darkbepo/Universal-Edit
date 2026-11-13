"""Editor tiles on the empty start screen, the New picture window, and parity with the web."""

import re
from pathlib import Path

import pytest

from umc.core.editors import EDITORS, WEB_LATER
from umc.core.errors import ConversionError
from umc.editor.blank import BACKGROUNDS, PRESETS, blank_document

ROOT = Path(__file__).resolve().parents[1]


def test_blank_document_fills_the_background():
    doc = blank_document(3, 2, "white")
    assert (doc.width, doc.height, len(doc.layers)) == (3, 2, 1)
    assert doc.layers[0].image.getpixel((2, 1)) == (255, 255, 255, 255)
    assert blank_document(1, 1, "transparent").layers[0].image.getpixel((0, 0))[3] == 0
    assert blank_document(1, 1, "black").layers[0].image.getpixel((0, 0)) == (0, 0, 0, 255)
    assert not doc.dirty  # leaving straight away asks nothing
    with pytest.raises(ConversionError):
        blank_document(0, 5)


def test_same_editors_and_presets_as_the_website():
    ts = (ROOT / "web" / "src" / "core" / "editors.ts").read_text(encoding="utf-8")
    web = re.findall(
        r'id: "(\w+)", title: "([^"]+)", blurb: "([^"]+)", icon: "([\w-]+)", available: (\w+)', ts
    )
    # editors the website doesn't have yet keep a "Coming soon" tile there
    assert web == [
        (e.id, e.title, e.blurb, e.icon, "false" if e.id in WEB_LATER else str(e.available).lower())
        for e in EDITORS
    ]
    presets = (ROOT / "web" / "src" / "editor" / "presets.ts").read_text(encoding="utf-8")
    assert re.findall(r'\["([^"]+)", (\d+), (\d+)\]', presets) == [
        (label, str(w), str(h)) for label, w, h in PRESETS
    ]
    assert re.findall(r'(\w+): \["(\w+)"', presets) == [(k, v[0]) for k, v in BACKGROUNDS.items()]


def test_editor_icons_exist_in_both_apps():
    from umc.gui.icon_data import ICONS

    web_icons = (ROOT / "web" / "src" / "ui" / "icon_data.ts").read_text(encoding="utf-8")
    for e in EDITORS:
        assert e.icon in ICONS and f'"{e.icon}":' in web_icons


pytest.importorskip("pytestqt")


@pytest.fixture
def window(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1280, 800)
    w.show()
    return w


def test_tiles_shown_until_files_are_added(window, media):
    hub = window.hub
    assert hub.isVisible()
    enabled = [t for t, tile in hub.tiles.items() if tile.isEnabled()]
    assert enabled == ["image", "model", "video", "audio", "document"]
    assert hub.tiles["video"].badge.isHidden()
    assert hub.tiles["document"].badge.isHidden()
    window.add_files([str(media / "img0.png")])
    assert not hub.isVisible()
    window.clear_files()
    assert hub.isVisible()


def test_unknown_editors_open_nothing(window):
    assert window.open_editor("nothing") is False
    assert window.stack.currentIndex() == 0


def test_new_picture_opens_the_image_editor(window):
    assert window.new_picture(320, 240, "black")
    assert window.stack.currentWidget() is window.editor
    assert window.edit_mode.isChecked()
    doc = window.editor.doc
    assert (doc.width, doc.height) == (320, 240)
    assert doc.layers[0].image.getpixel((0, 0)) == (0, 0, 0, 255)
    window.leave_edit()  # nothing drawn: no "unsaved changes?" question
    assert window.stack.currentIndex() == 0 and window.hub.isVisible()


def test_new_picture_too_big_is_a_plain_message(window):
    assert not window.new_picture(20000, 10, "white")
    assert window.stack.currentIndex() == 0
    assert "too large" in window.input_error.text()


def test_new_picture_window_follows_presets(qtbot):
    from umc.gui.widgets.editor_hub import CUSTOM, NewPictureDialog

    d = NewPictureDialog()
    qtbot.addWidget(d)
    assert d.values() == (1920, 1080, "white")
    d.preset.setCurrentIndex(1)
    assert d.values()[:2] == (1080, 1080)
    d.width_box.setValue(500)
    assert d.preset.currentText() == CUSTOM
    d.height_box.setValue(1080)
    d.width_box.setValue(1080)
    assert d.preset.currentIndex() == 1  # typed back to a preset's size


def test_3d_viewer_tile_opens_it_empty(window, tmp_path):
    assert window.open_editor("model")
    page = window.viewer3d
    assert window.stack.currentWidget() is page
    assert page.files == [] and "Open a 3D model" in page.placeholder.text()
    stl = tmp_path / "tri.stl"
    stl.write_text(
        "solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        "endloop\nendfacet\nendsolid t\n"
    )
    page.files_chosen.emit([str(stl)])  # what "Open…" sends
    assert [f.path for f in window.files] == [stl]
    assert [f.path for f in page.files] == [stl]
    page.wait_idle()
    window.leave_edit()
    assert window.stack.currentIndex() == 0


def test_document_tile_opens_an_empty_document(window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    assert window.open_editor("document")
    page = window.doc_editor
    assert window.stack.currentWidget() is page and window.edit_mode.isChecked()
    assert page.source is None and page.untitled and page.view.toPlainText() == ""
    assert not page.view.document().isModified() and not page.banner.isVisible()
    assert page.save_button.isEnabled() and page.save_as_button.isEnabled()

    def no_dialog(*a, **k):
        raise AssertionError("no question expected")

    monkeypatch.setattr(QMessageBox, "exec", no_dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", no_dialog)
    window.leave_edit()  # nothing typed: leaves without asking
    assert window.stack.currentIndex() == 0 and window.hub.isVisible()


def test_document_tile_stays_put_when_the_page_refuses(window, monkeypatch):
    from umc.gui.doceditor.doc_editor_page import DocEditorPage

    monkeypatch.setattr(DocEditorPage, "open_empty", lambda self: False)
    assert window.open_editor("document") is False
    assert window.stack.currentIndex() == 0 and not window.edit_mode.isChecked()
