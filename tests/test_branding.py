"""Universal Edit name and logo; moving data saved under the old name (Universal Media
Converter)."""

from pathlib import Path

import pytest

from umc.core import paths

ROOT = Path(__file__).resolve().parents[1]


def test_website_logo_is_the_same_file():
    # scripts/make_icon.py copies it; run it again after editing src/umc/gui/logo.svg
    assert (ROOT / "web" / "public" / "icon.svg").read_bytes() == (
        ROOT / "src" / "umc" / "gui" / "logo.svg"
    ).read_bytes()


def test_windows_icon_has_small_and_large_sizes():
    from PIL import Image

    sizes = Image.open(ROOT / "packaging" / "icon.ico").info["sizes"]
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


@pytest.fixture(autouse=True)
def _fresh_migration():
    paths.migrated_dir.cache_clear()
    yield
    paths.migrated_dir.cache_clear()


def test_old_data_folder_is_moved(tmp_path):
    old = tmp_path / paths.OLD_APP_DIR_NAME
    (old / "tools" / "pandoc").mkdir(parents=True)
    (old / "tools" / "pandoc" / "pandoc.exe").write_text("x")
    got = paths.migrated_dir(tmp_path)
    assert got == tmp_path / "UniversalEdit"
    assert (got / "tools" / "pandoc" / "pandoc.exe").read_text() == "x"
    assert not old.exists()


def test_missing_entries_are_moved_when_the_new_folder_already_exists(tmp_path):
    old, new = tmp_path / paths.OLD_APP_DIR_NAME, tmp_path / "UniversalEdit"
    (old / "tools").mkdir(parents=True)
    (old / "umc.log").write_text("old log")
    (new / "cache").mkdir(parents=True)  # e.g. made by Qt, or another copy starting
    (new / "umc.log").write_text("new log")
    assert paths.migrated_dir(tmp_path) == new
    assert (new / "tools").is_dir() and (new / "cache").is_dir()
    assert (new / "umc.log").read_text() == "new log"  # never overwritten
    assert (old / "umc.log").read_text() == "old log"  # and never deleted
    assert not (old / "tools").exists()


def test_another_copy_moved_it_first(tmp_path, monkeypatch):
    old, new = tmp_path / paths.OLD_APP_DIR_NAME, tmp_path / "UniversalEdit"
    (old / "tools").mkdir(parents=True)
    real_rename = Path.rename

    def raced(self, target):  # the other process wins the rename between our check and ours
        real_rename(self, target)
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "rename", raced)
    assert paths.migrated_dir(tmp_path) == new
    assert (new / "tools").is_dir() and not old.exists()


def test_tools_in_use_are_still_found_in_the_old_folder(tmp_path, monkeypatch):
    old = tmp_path / paths.OLD_APP_DIR_NAME
    (old / "tools" / "libreoffice").mkdir(parents=True)

    def busy(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "rename", busy)
    monkeypatch.setattr(paths, "_data_base", lambda: tmp_path)
    monkeypatch.delenv("UMC_TOOLS_DIR", raising=False)
    assert paths.app_data_dir() == tmp_path / "UniversalEdit"
    assert paths.tools_dir() == old / "tools"
    assert any("could not move" in n for n in paths.MIGRATION_NOTES)


def test_fresh_install_makes_the_new_folder(tmp_path):
    assert paths.migrated_dir(tmp_path).is_dir()


pytest.importorskip("pytestqt")


@pytest.fixture
def ini_settings(tmp_path):
    from PySide6.QtCore import QCoreApplication, QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(tmp_path))
    names = QCoreApplication.organizationName(), QCoreApplication.applicationName()
    yield
    QCoreApplication.setOrganizationName(names[0])
    QCoreApplication.setApplicationName(names[1])
    QSettings.setDefaultFormat(QSettings.Format.NativeFormat)


def test_settings_copied_from_the_old_name_once(qapp, ini_settings):
    from PySide6.QtCore import QCoreApplication, QSettings

    from umc.gui import branding

    old = QSettings(branding.OLD_ORG_NAME, branding.OLD_APP_NAME)
    old.setValue("last_target", "png")
    old.setValue("window/size", "1200x700")
    old.sync()
    QCoreApplication.setOrganizationName(branding.ORG_NAME)
    QCoreApplication.setApplicationName(branding.APP_NAME)
    assert branding.migrate_settings() == 2
    assert QSettings().value("last_target") == "png"
    QSettings().setValue("last_target", "jpg")
    assert branding.migrate_settings() == 0  # already has settings: never overwritten
    assert QSettings().value("last_target") == "jpg"


def test_logo_and_window_icon_render(qapp):
    from umc.gui import branding

    img = branding.logo_image(64)
    assert img.pixelColor(32, 32).alpha() == 255
    assert img.pixelColor(0, 0).alpha() == 0  # rounded corner
    assert not branding.app_icon().isNull()


def test_name_is_centred_in_the_top_bar(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    assert w.windowTitle() == "Universal Edit" and w.title.text() == "Universal Edit"
    for width in (1280, 700):
        w.resize(width, 700)
        w.show()
        qtbot.wait(10)
        bar = w.brand.parentWidget()
        assert abs(w.brand.geometry().center().x() - bar.width() / 2) <= 1
        assert w.mode_bar.geometry().right() > w.brand.geometry().right()
