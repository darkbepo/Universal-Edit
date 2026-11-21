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
    assert enabled == ["image", "video", "audio", "document", "code", "model", "subtitle"]
    for soon in ("vector", "font", "data"):
        assert not hub.tiles[soon].isEnabled() and not hub.tiles[soon].badge.isHidden()
    assert hub.tiles["video"].badge.isHidden()
    assert hub.tiles["document"].badge.isHidden()
    assert hub.tiles["code"].badge.isHidden()
    assert hub.tiles["subtitle"].badge.isHidden()
    window.add_files([str(media / "img0.png")])
    assert not hub.isVisible()
    window.clear_files()
    assert hub.isVisible()


def _rows(tiles) -> list[int]:
    counts: list[int] = []
    last = None
    for t in tiles:
        top = t.mapTo(t.window(), t.rect().topLeft()).y()
        if top == last:
            counts[-1] += 1
        else:
            counts.append(1)
        last = top
    return counts


def test_tiles_fill_the_width_in_even_rows_with_the_icon_on_the_left(window, qtbot):
    from umc.core.editors import hub_rows

    hub = window.hub
    tiles = list(hub.tiles.values())
    assert len(tiles) == 10
    for width, expected in ((1920, [5, 5]), (1280, [4, 3, 3])):
        window.resize(width, 1000)
        qtbot.waitUntil(lambda e=expected: _rows(tiles) == e)
        holder = tiles[0].parentWidget()
        assert hub_rows(10, holder.width()) == expected
        left = min(t.geometry().left() for t in tiles)
        right = max(t.geometry().right() for t in tiles)
        assert left <= 1 and right >= holder.width() - 2  # no empty sides
        last = [t for t in tiles if t.geometry().top() == tiles[-1].geometry().top()]
        centre = (last[0].geometry().left() + last[-1].geometry().right()) / 2
        assert abs(centre - holder.width() / 2) <= 2  # a shorter last row is centred
        assert window.drop.height() > hub.height()  # the drop zone gets the spare height
    for tile in tiles:
        assert tile.icon.geometry().right() < tile.title.geometry().left()  # icon left of text


def test_a_short_window_scrolls_the_tiles_instead_of_cutting_rows_off(window, qtbot):
    hub = window.hub
    window.resize(1280, 520)
    bar = hub.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    inner = hub.scroll.widget()
    bottom = max(t.geometry().bottom() for t in hub.tiles.values())
    assert bottom <= inner.height()  # every row is inside what the scroll area can show
    window.resize(1280, 1000)
    qtbot.waitUntil(lambda: bar.maximum() == 0)


def test_shared_row_cases():
    import json
    from pathlib import Path

    from umc.core.editors import hub_rows, hub_tile_width

    path = Path(__file__).parent / "fixtures" / "hub_rows_cases.json"
    for case in json.loads(path.read_text()):
        rows = hub_rows(case["count"], case["width"])
        assert rows == case["rows"], case
        assert abs(hub_tile_width(rows, case["width"]) - case["tile"]) < 1e-3


def test_icons_sit_on_a_tinted_square(window):
    from PySide6.QtWidgets import QApplication

    from umc.gui import theme

    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(theme.stylesheet())  # what app.py does at start
    app.processEvents()

    def box_colour(tile):  # left edge of the icon square, away from the glyph
        g = tile.icon.geometry()
        return tile.grab().toImage().pixelColor(g.left() + 3, g.center().y()).name()

    pal = theme.palette()
    try:
        assert box_colour(window.hub.tiles["image"]) == pal["accent_soft"]
        window.hub.tiles["image"].icon.setProperty("soon", True)  # as a not-ready tile is
        app.setStyleSheet(theme.stylesheet())
        app.processEvents()
        assert box_colour(window.hub.tiles["image"]) == pal["surface"]
    finally:
        app.setStyleSheet(old)


def test_code_tile_opens_an_untitled_file(window):
    assert window.open_editor("code")
    page = window.code_editor
    assert window.stack.currentWidget() is page and window.edit_mode.isChecked()
    assert page.tabs.count() == 1 and page.current.title == "Untitled"
    window.leave_edit()  # nothing typed: no question
    assert window.stack.currentIndex() == 0


def test_coming_soon_tiles_are_greyed_out_and_open_nothing(qtbot, monkeypatch):
    import dataclasses

    from PySide6.QtCore import QCoreApplication

    import umc.core.editors as editors
    import umc.gui.widgets.editor_hub as hub_module

    soon = tuple(dataclasses.replace(e, available=False) if e.id == "video" else e for e in EDITORS)
    monkeypatch.setattr(editors, "EDITORS", soon)
    monkeypatch.setattr(hub_module, "EDITORS", soon)
    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    tile = w.hub.tiles["video"]
    assert not tile.isEnabled()
    assert tile.badge.text() == "Coming soon" and not tile.badge.isHidden()
    assert "coming soon" in tile.toolTip()
    assert w.open_editor("video") is False
    assert w.stack.currentIndex() == 0


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


def _dejavu_family() -> str:
    """DejaVu Sans, the wide face Linux falls back to (no Segoe UI there)."""
    from PySide6.QtGui import QFontDatabase

    from umc.converters.subtitles import fonts_dir

    fonts = fonts_dir()
    if fonts is None:
        pytest.skip("bundled font not fetched (scripts/fetch_font.py)")
    families = QFontDatabase.applicationFontFamilies(
        QFontDatabase.addApplicationFont(str(fonts / "DejaVuSans.ttf"))
    )
    if not families:
        pytest.skip("DejaVu Sans could not be loaded")
    return families[0]


@pytest.mark.parametrize("font", ["system", "dejavu"])
@pytest.mark.parametrize("width", [1100, 1280, 1440, 1920])
def test_coming_soon_badges_are_never_cut_off(qtbot, window, width, font):
    """At five tiles a row the title and badge were too wide together: the badge was clipped
    ("Coming soo"). Now the badge keeps its size and shortens to "Soon" when the title needs
    the room — also with Linux's wider fallback font, and when the font or stylesheet changes
    without the tile changing size (1280 px is the fixture's own width: on Linux the title
    was squeezed there)."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from umc.gui import theme

    app = QApplication.instance()
    before, before_font = app.styleSheet(), app.font()
    if font == "dejavu":
        app.setFont(QFont(_dejavu_family()))
    app.setStyleSheet(theme.stylesheet())  # its padding is what made the badge too wide
    try:
        window.resize(width, 900)
        qtbot.waitUntil(lambda: window.hub.tiles["vector"].width() > 0, timeout=3000)
        for _ in range(5):
            QApplication.processEvents()
        for tile in window.hub.tiles.values():
            if tile.badge.isVisible() or not tile.info.available:
                assert tile.title.width() >= tile.title.sizeHint().width(), (width, tile.info.id)
            if tile.badge.isVisible():
                assert tile.badge.width() >= tile.badge.sizeHint().width(), (width, tile.info.id)
                if not tile.info.available:
                    assert tile.badge.text() in ("Coming soon", "Soon")
        window.resize(1920, 900)
        for _ in range(5):
            QApplication.processEvents()
        vector = window.hub.tiles["vector"]
        room = vector.text_room() - vector.title.sizeHint().width() - vector._head.spacing()
        if vector.badge_width("Coming soon") <= room:  # the font leaves room for all of it
            assert vector.badge.isVisible() and vector.badge.text() == "Coming soon"
    finally:
        app.setStyleSheet(before)
        app.setFont(before_font)
