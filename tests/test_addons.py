"""Editor add-ons: building, checking, installing, switching on, downloading, the screens."""

import hashlib
import importlib
import json
import sys
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from umc import __version__
from umc.core import addons
from umc.core.errors import ConversionError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def addon_env(tmp_path, monkeypatch):
    """As in the built app (nothing counts as installed) with a private add-ons folder."""
    monkeypatch.setenv("UMC_ADDONS_TEST", "1")
    monkeypatch.setenv("UMC_ADDONS_DIR", str(tmp_path / "addons"))
    monkeypatch.setattr(addons, "_activated", set())
    saved_path = list(sys.path)
    yield tmp_path
    sys.path[:] = saved_path
    for name in [m for m in sys.modules if m.startswith(("umc.gui.fakeaddon", "fakesitepkg"))]:
        del sys.modules[name]
    import umc.gui

    umc.gui.__path__[:] = [p for p in umc.gui.__path__ if str(tmp_path) not in p]


def _fake_addon(monkeypatch, tmp_path, version=__version__, extra=None) -> tuple[Path, str]:
    monkeypatch.setitem(
        addons.ADDONS,
        "fake",
        addons.Addon(
            "fake", "Fake editor", ("umc.gui.fakeaddon",), ("fakesitepkg",), "umc.gui.fakeaddon"
        ),
    )
    zip_path = tmp_path / "fake.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("addon.json", json.dumps({"id": "fake", "version": version}))
        zf.writestr("umc/gui/fakeaddon/__init__.py", "import fakesitepkg\nVALUE = fakesitepkg.X\n")
        zf.writestr("site/fakesitepkg/__init__.py", "X = 42\n")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return zip_path, hashlib.sha256(zip_path.read_bytes()).hexdigest()


def test_from_source_everything_is_installed(monkeypatch):
    monkeypatch.delenv("UMC_ADDONS_TEST", raising=False)
    assert addons.installed("image") and addons.ready("model") and addons.ready("document")


def test_install_switches_on_without_restart(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env)
    assert not addons.installed("fake")
    addons.install_from_zip("fake", zip_path, digest)
    assert addons.installed("fake")
    assert importlib.import_module("umc.gui.fakeaddon").VALUE == 42  # own code + its package
    addons.remove("fake")
    assert not addons.installed("fake")


def test_wrong_fingerprint_is_refused(addon_env, monkeypatch):
    zip_path, _digest = _fake_addon(monkeypatch, addon_env)
    with pytest.raises(ConversionError, match="fingerprint"):
        addons.install_from_zip("fake", zip_path, "0" * 64)
    assert not addons.installed("fake")


def test_unsafe_names_are_refused(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env, extra={"../evil.py": "x"})
    with pytest.raises(ConversionError, match="damaged"):
        addons.install_from_zip("fake", zip_path, digest)
    assert not (addon_env / "evil.py").exists()


def test_links_inside_are_refused(addon_env, monkeypatch):
    zip_path, _ = _fake_addon(monkeypatch, addon_env)
    with zipfile.ZipFile(zip_path, "a") as zf:
        link = zipfile.ZipInfo("umc/gui/fakeaddon/link")
        link.external_attr = 0o120777 << 16  # a symbolic link
        zf.writestr(link, "/etc/passwd")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with pytest.raises(ConversionError, match="links"):
        addons.install_from_zip("fake", zip_path, digest)


def test_a_zip_without_description_is_refused(addon_env, monkeypatch):
    _fake_addon(monkeypatch, addon_env)
    bad = addon_env / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("umc/gui/fakeaddon/__init__.py", "")
    digest = hashlib.sha256(bad.read_bytes()).hexdigest()
    with pytest.raises(ConversionError, match="damaged"):
        addons.install_from_zip("fake", bad, digest)


def test_an_add_on_for_another_version_is_refused(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env, version="0.0.1")
    with pytest.raises(ConversionError, match="version"):
        addons.install_from_zip("fake", zip_path, digest)


def test_a_version_change_needs_the_new_add_on(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env)
    addons.install_from_zip("fake", zip_path, digest)
    manifest = addons.addon_dir("fake") / "addon.json"
    manifest.write_text(json.dumps({"id": "fake", "version": "9.9.9"}))
    assert not addons.installed("fake")


def test_download_from_the_release(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env)
    info = {"fake": {"file": zip_path.name, "sha256": digest, "size": zip_path.stat().st_size}}
    monkeypatch.setattr(addons, "known_downloads", lambda: info)
    handler = partial(SimpleHTTPRequestHandler, directory=str(addon_env))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("UMC_ADDONS_URL", f"http://127.0.0.1:{server.server_port}")
        seen = []
        addons.download("fake", seen.append)
        assert addons.installed("fake") and seen and seen[-1] == 1.0
    finally:
        server.shutdown()


def test_cancelling_a_download_installs_nothing(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env)
    info = {"fake": {"file": zip_path.name, "sha256": digest, "size": zip_path.stat().st_size}}
    monkeypatch.setattr(addons, "known_downloads", lambda: info)
    handler = partial(SimpleHTTPRequestHandler, directory=str(addon_env))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("UMC_ADDONS_URL", f"http://127.0.0.1:{server.server_port}")
        cancel = threading.Event()
        cancel.set()
        from umc.core.errors import ConversionCancelled

        with pytest.raises(ConversionCancelled):
            addons.download("fake", cancel=cancel)
        assert not addons.installed("fake")
    finally:
        server.shutdown()


def test_installer_command_usage(addon_env):
    assert addons.run_cli(["--install-addon"]) == 2
    assert addons.run_cli(["--install-addon", "nope", str(addon_env / "x.zip")]) == 1


def test_a_build_without_downloads_says_so(addon_env, monkeypatch):
    monkeypatch.setattr(addons, "known_downloads", lambda: {})
    with pytest.raises(ConversionError, match="no editor downloads"):
        addons.download("image")


def test_installer_command_takes_the_zips_that_are_there(addon_env, monkeypatch):
    zip_path, digest = _fake_addon(monkeypatch, addon_env)
    folder = addon_env / "chosen"
    folder.mkdir()
    zip_path.rename(folder / "fake.zip")
    info = {
        "fake": {"file": "fake.zip", "sha256": digest, "size": 1},
        "image": {"file": "not-chosen.zip", "sha256": "0" * 64, "size": 1},
    }
    monkeypatch.setattr(addons, "known_downloads", lambda: info)
    assert addons.run_cli(["--install-addon", "all", str(folder)]) == 0
    assert addons.installed("fake") and not addons.installed("image")
    assert addons.run_cli(["--install-addon", "all", str(addon_env / "empty")]) == 1


def test_build_script_makes_a_checked_zip(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_addons
    finally:
        sys.path.pop(0)
    hashes = build_addons.build(tmp_path, only=["model"], hashes_file=tmp_path / "h.json")
    info = hashes["model"]
    zip_path = tmp_path / info["file"]
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == info["sha256"]
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert json.loads(zf.read("addon.json"))["version"] == __version__
    assert "umc/gui/editor3d/viewer_page.py" in names
    assert not any("__pycache__" in n for n in names)
    # the same sources give the same zip: a later build can use an earlier release's add-on
    again = build_addons.build(tmp_path / "again", only=["model"], hashes_file=tmp_path / "h2.json")
    assert again["model"]["sha256"] == info["sha256"]


# --------------------------------------------------------------------------- screens
pytest.importorskip("pytestqt")


@pytest.fixture
def window(qtbot, addon_env):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1280, 800)
    w.show()
    return w


def _pretend_install(addon_id, progress=None, cancel=None):
    folder = addons.addon_dir(addon_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "addon.json").write_text(json.dumps({"id": addon_id, "version": __version__}))


def test_tiles_say_install_until_installed(window, monkeypatch):
    tile = window.hub.tiles["model"]
    assert tile.needs_install and tile.badge.isVisible() and tile.badge.text() == "Install"
    assert not window.hub.tiles["video"].needs_install  # "Coming soon" is something else
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(addons, "download", _pretend_install)
    assert window.open_editor("model")  # asked, installed, opened
    assert window.stack.currentWidget() is window.viewer3d
    assert not tile.needs_install and not tile.badge.isVisible()


def test_saying_no_opens_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    called = []
    monkeypatch.setattr(addons, "download", lambda *a, **k: called.append(a))
    assert not window.open_editor("image")
    assert window.stack.currentIndex() == 0 and not called


def test_edit_asks_for_the_image_editor_first(qtbot, window, media, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = []

    def question(*a, **k):
        asked.append(a)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", question)
    window.add_files([str(media / "img0.png")])
    window.enter_edit()
    assert asked and window.stack.currentIndex() == 0 and window.convert_mode.isChecked()
    qtbot.waitUntil(lambda: not window.file_list.thumbnails_pending(), timeout=10000)


def test_settings_lists_the_editors(qtbot, addon_env, monkeypatch):
    from umc.gui.settings_dialog import SettingsDialog

    d = SettingsDialog(registry=None)
    qtbot.addWidget(d)
    state, button = d.addon_rows["image"]
    assert "not installed" in state.text() and button.text() == "Install"
    monkeypatch.setattr(addons, "download", _pretend_install)
    button.click()
    assert state.text() == "installed" and button.text() == "Remove"
    button.click()
    assert "not installed" in state.text() and "removed" in d.message.text()
