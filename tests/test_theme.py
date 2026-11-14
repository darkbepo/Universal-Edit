"""The System / Light / Dark switch: cycling, remembering, and what it repaints."""

import pytest

pytest.importorskip("pytestqt")


@pytest.fixture
def choice(qapp):
    """Own settings, and the theme back on "system" afterwards (it is module state)."""
    from PySide6.QtCore import QCoreApplication, QSettings

    from umc.gui import theme

    old_org = QCoreApplication.organizationName()
    QCoreApplication.setOrganizationName("UMC-tests")
    QSettings().remove(theme.SETTING)
    theme.set_choice("system", save=False)
    yield theme
    theme.set_choice("system", save=False)
    QSettings().remove(theme.SETTING)
    QSettings().sync()
    QCoreApplication.setOrganizationName(old_org)


def test_cycle_order():
    from umc.gui import theme

    assert theme.next_choice("system") == "light"
    assert theme.next_choice("light") == "dark"
    assert theme.next_choice("dark") == "system"
    assert theme.next_choice("nonsense") == "system"


def test_choice_picks_the_palette(choice):
    choice.set_choice("dark", save=False)
    assert choice.is_dark() and choice.palette() is choice.DARK
    assert choice.DARK["bg"] in choice.stylesheet()
    choice.set_choice("light", save=False)
    assert not choice.is_dark() and choice.palette() is choice.LIGHT


def test_choice_is_remembered(choice):
    from PySide6.QtCore import QSettings

    assert choice.saved_choice() == "system"
    choice.set_choice("dark")
    assert QSettings().value(choice.SETTING) == "dark"
    choice.set_choice("system", save=False)
    choice.load_choice()  # what app.py does at the next start
    assert choice.choice() == "dark"
    QSettings().setValue(choice.SETTING, "purple")  # a damaged value falls back
    assert choice.saved_choice() == "system"


def test_changed_fires_only_when_the_look_changes(choice, qtbot):
    n = choice.notifier()
    choice.set_choice("light", save=False)
    with qtbot.waitSignal(n.changed, timeout=500):
        choice.set_choice("dark", save=False)
    with qtbot.assertNotEmitted(n.changed):
        choice.set_choice("dark", save=False)
    with qtbot.waitSignal(n.choice_changed, timeout=500) as got:
        choice.set_choice("light", save=False)
    assert got.args == ["light"]


def test_top_bar_button_cycles_and_settings_window_follows(choice, qtbot, tmp_path):
    from PySide6.QtCore import QSettings, Qt

    from umc.gui.main_window import MainWindow
    from umc.gui.settings_dialog import SettingsDialog

    w = MainWindow()
    w._set_output_dir(tmp_path)
    qtbot.addWidget(w)
    w.show()
    assert "Same as the computer" in w.theme_button.toolTip()
    qtbot.mouseClick(w.theme_button, Qt.MouseButton.LeftButton)
    assert choice.choice() == "light" and not choice.is_dark()
    assert w.theme_button.accessibleName() == "Theme: Light"
    qtbot.mouseClick(w.theme_button, Qt.MouseButton.LeftButton)
    assert choice.choice() == "dark" and choice.is_dark()
    assert QSettings().value(choice.SETTING) == "dark"
    assert w.theme_button.toolTip().endswith("click for same as the computer")

    dlg = SettingsDialog(w, registry=None)
    qtbot.addWidget(dlg)
    assert dlg.theme_buttons["dark"].isChecked()
    dlg.theme_buttons["light"].click()
    assert choice.choice() == "light"
    assert w.theme_button.accessibleName() == "Theme: Light"  # the top bar follows
    w.close()


def test_pages_built_earlier_follow_the_switch(choice, qtbot):
    """Icons and colours baked in when a page was built (tool rail, a label's pixmap, a list
    item, a widget stylesheet) are redrawn in the other theme's colours."""
    from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QToolButton

    from umc.gui import icons
    from umc.gui.editor.tool_rail import ToolRail

    choice.set_choice("light", save=False)
    light, dark = choice.LIGHT, choice.DARK
    rail = ToolRail()
    qtbot.addWidget(rail)
    label = QLabel()
    label.setPixmap(icons.pixmap("info-circle", 18, light["accent"]))
    qtbot.addWidget(label)
    lst = QListWidget()
    qtbot.addWidget(lst)
    item = QListWidgetItem("x")
    item.setIcon(icons.icon("photo", light["muted"], 16))
    lst.addItem(item)
    banner = QToolButton()
    banner.setStyleSheet(f"QToolButton {{ background: {light['accent_soft']}; color: #123456; }}")
    qtbot.addWidget(banner)
    keys = {k: b.icon().cacheKey() for k, b in rail.buttons.items()}

    choice.set_choice("dark", save=False)
    some = next(iter(rail.buttons))
    assert icons._made_icons[rail.buttons[some].icon().cacheKey()][1] == dark["text"]
    assert all(b.icon().cacheKey() != keys[k] for k, b in rail.buttons.items())
    assert icons._made_pixmaps[label.pixmap().cacheKey()][2] == dark["accent"]
    assert icons._made_icons[lst.item(0).icon().cacheKey()][1] == dark["muted"]
    assert dark["accent_soft"] in banner.styleSheet()
    assert "#123456" in banner.styleSheet()  # not a palette colour: left alone

    choice.set_choice("light", save=False)
    assert icons._made_icons[rail.buttons[some].icon().cacheKey()][1] == light["text"]
    assert light["accent_soft"] in banner.styleSheet()
