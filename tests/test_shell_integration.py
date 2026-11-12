"""Right-click "Convert" and "Open with" registration, against a fake HKCU."""

import pytest

from umc.tools import shell_integration as shell


class FakeRegistry:
    def __init__(self) -> None:
        self.keys: dict[str, dict[str, str]] = {}

    def _k(self, path: str) -> str:
        return path.lower()

    def set_value(self, path, name, value):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):  # parent keys exist too, like in the real registry
            self.keys.setdefault(self._k("\\".join(parts[:i])), {})
        self.keys[self._k(path)][name] = value

    def set_empty(self, path, name):
        self.set_value(path, name, "")

    def get_value(self, path, name):
        return self.keys.get(self._k(path), {}).get(name)

    def delete_value(self, path, name):
        self.keys.get(self._k(path), {}).pop(name, None)

    def delete_tree(self, path):
        p = self._k(path)
        for k in [k for k in self.keys if k == p or k.startswith(p + "\\")]:
            del self.keys[k]

    def has_key(self, path):
        return self._k(path) in self.keys

    def subkeys(self, path):
        p = self._k(path) + "\\"
        return sorted({k[len(p) :].split("\\")[0] for k in self.keys if k.startswith(p)})


@pytest.fixture
def reg():
    return FakeRegistry()


def test_extension_lists():
    conv = shell.convert_extensions()
    for ext in ("png", "jpeg", "mp4", "mp3", "docx", "pdf", "stl", "zip", "ttf", "srt", "dxf"):
        assert ext in conv
    assert all("." not in e for e in conv)  # no "tar.gz": Explorer keys by the last suffix
    view = shell.view_extensions()
    assert "png" in view and "mp4" in view and "pdf" in view and "stl" in view
    assert "zip" not in view and "ttf" not in view  # nothing to show for those


def test_context_menu(reg):
    shell.register_context_menu(reg)
    key = r"Software\Classes\SystemFileAssociations\.png\shell\UniversalEdit.Convert"
    assert reg.get_value(key, "MUIVerb") == "Convert with Universal Edit"
    assert reg.get_value(key, "MultiSelectModel") == "Player"
    command = reg.get_value(key + r"\command", "")
    assert command.endswith('--convert "%1"') and "umc" in command  # from a checkout: -m umc
    assert shell.context_menu_registered(reg)
    other = r"Software\Classes\SystemFileAssociations\.png\shell\SomeoneElse"
    reg.set_value(other, "MUIVerb", "theirs")
    shell.unregister_context_menu(reg)
    assert not shell.context_menu_registered(reg)
    assert reg.get_value(other, "MUIVerb") == "theirs"  # others' entries untouched


def test_open_with(reg):
    reg.set_value(r"Software\Classes\.png\OpenWithProgids", "Photos.File", "")
    shell.register_open_with(reg)
    assert reg.get_value(r"Software\Classes\.png\OpenWithProgids", shell.PROGID) == ""
    cmd = reg.get_value(r"Software\Classes\UniversalEdit.View\shell\open\command", "")
    assert cmd.endswith('--view "%1"')
    caps = shell.CAPABILITIES + r"\FileAssociations"
    assert reg.get_value(caps, ".mp4") == shell.PROGID
    assert reg.get_value(shell.REGISTERED_APPS, "Universal Edit") == shell.CAPABILITIES
    assert shell.open_with_registered(reg)
    shell.unregister_open_with(reg)
    assert not shell.open_with_registered(reg)
    assert reg.get_value(r"Software\Classes\.png\OpenWithProgids", shell.PROGID) is None
    assert reg.get_value(r"Software\Classes\.png\OpenWithProgids", "Photos.File") == ""  # kept
    assert not reg.has_key(r"Software\Classes\UniversalEdit.View")
    assert not reg.has_key(shell.APP_KEY)


def test_unregister_keeps_the_apps_own_settings(reg):
    # QSettings (organisation "UniversalEdit") lives right next to the integration keys
    settings = r"Software\UniversalEdit\Universal Edit"
    reg.set_value(settings, "last_target", "png")
    shell.register_open_with(reg)
    shell.register_context_menu(reg)
    shell.unregister_open_with(reg)
    shell.unregister_context_menu(reg)
    assert reg.get_value(settings, "last_target") == "png"


def test_unregister_removes_what_an_older_version_registered(reg):
    # an extension this version no longer lists
    old = r"Software\Classes\SystemFileAssociations\.oldfmt\shell\UniversalEdit.Convert"
    reg.set_value(old + r"\command", "", "old")
    reg.set_value(r"Software\Classes\.oldfmt\OpenWithProgids", shell.PROGID, "")
    reg.set_value(r"Software\Classes\.oldfmt\OpenWithProgids", "Other.App", "")
    shell.register_context_menu(reg)  # registering clears old entries first
    assert not reg.has_key(old)
    shell.unregister_open_with(reg)
    assert reg.get_value(r"Software\Classes\.oldfmt\OpenWithProgids", shell.PROGID) is None
    assert reg.get_value(r"Software\Classes\.oldfmt\OpenWithProgids", "Other.App") == ""


def test_frozen_exe_command(reg, monkeypatch):
    monkeypatch.setattr(shell.sys, "frozen", True, raising=False)
    monkeypatch.setattr(shell.sys, "executable", r"C:\Apps\Universal Edit\UniversalEdit.exe")
    assert shell.launch_command("--view", '"%1"') == (
        r'"C:\Apps\Universal Edit\UniversalEdit.exe" --view "%1"'
    )
    shell.register_context_menu(reg)
    key = r"Software\Classes\SystemFileAssociations\.mp4\shell\UniversalEdit.Convert"
    assert reg.get_value(key, "Icon") == r"C:\Apps\Universal Edit\UniversalEdit.exe,0"


def test_cli(reg, monkeypatch):
    monkeypatch.setattr(shell, "registry", lambda: reg)
    assert shell.run_cli(["--register", "all"]) == 0
    assert shell.feature_on("context-menu", reg) and shell.feature_on("open-with", reg)
    assert shell.run_cli(["--unregister", "open-with"]) == 0
    assert shell.feature_on("context-menu", reg) and not shell.feature_on("open-with", reg)
    assert shell.run_cli(["--unregister"]) == 0  # = all
    assert not reg.keys or not shell.feature_on("context-menu", reg)
    assert shell.run_cli(["--register", "nonsense"]) == 2


def test_cli_off_windows_fails_cleanly(monkeypatch):
    monkeypatch.setattr(shell, "registry", lambda: None)
    assert shell.run_cli(["--register", "all"]) == 1


pytest.importorskip("pytestqt")


def test_settings_window_toggles(qtbot, reg):
    from umc.gui.settings_dialog import SettingsDialog

    shell.register_context_menu(reg)
    d = SettingsDialog(registry=reg)
    qtbot.addWidget(d)
    assert d.boxes["context-menu"].isChecked() and not d.boxes["open-with"].isChecked()
    d.boxes["open-with"].setChecked(True)
    assert shell.open_with_registered(reg)
    d.boxes["context-menu"].setChecked(False)
    assert not shell.context_menu_registered(reg)


def test_settings_window_off_windows(qtbot, monkeypatch):
    from umc.gui.settings_dialog import SettingsDialog

    monkeypatch.setattr(shell, "registry", lambda: None)
    d = SettingsDialog()
    qtbot.addWidget(d)
    assert not d.boxes["open-with"].isEnabled()
    assert "only available on Windows" in d.message.text()


def test_right_clicked_files_share_one_window(qtbot, media):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui import launcher

    c = launcher.AppController(listen=False)
    c.convert([str(media / "img0.png")])
    c.convert([str(media / "img1.png")])  # Explorer's second start, a moment later
    assert len(c.windows) == 1
    w = c.windows[0]
    qtbot.addWidget(w)
    assert [f.name for f in w.files] == ["img0.png", "img1.png"]
    c._on_message({"convert": [str(media / "img2.png")]})  # what a forwarded start sends
    assert [f.name for f in w.files] == ["img0.png", "img1.png", "img2.png"]
    c._convert_time -= launcher.GROUP_SECONDS + 1  # much later: a new window
    c.convert([str(media / "img3.png")])
    assert len(c.windows) == 2
    qtbot.addWidget(c.windows[1])
    assert [f.name for f in c.windows[1].files] == ["img3.png"]
    _thumbnails_done(qtbot, c.windows)


def test_no_grouping_into_a_busy_window(qtbot, media, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui import launcher
    from umc.gui.main_window import MainWindow

    c = launcher.AppController(listen=False)
    c.convert([str(media / "img0.png")])
    qtbot.addWidget(c.windows[0])
    monkeypatch.setattr(MainWindow, "busy", property(lambda self: self is c.windows[0]))
    c.convert([str(media / "img1.png")])  # the first one is converting: don't disturb it
    assert len(c.windows) == 2
    qtbot.addWidget(c.windows[1])
    _thumbnails_done(qtbot, c.windows)


def test_copies_started_together_end_up_in_one(qtbot, tmp_path):
    """Explorer starts one copy per selected file at once: one takes the lock and becomes the
    window, the rest wait until it listens and hand their file over."""
    import threading
    import uuid

    from umc.gui.single_instance import InstanceServer, claim

    name = f"umc-test-{uuid.uuid4().hex[:8]}"
    lock = str(tmp_path / "i.lock")
    handed, held = claim({"convert": ["a"]}, name=name, lock_path=lock)
    assert not handed and held is not None  # the first: becomes the window
    results = []
    t = threading.Thread(
        target=lambda: results.append(claim({"convert": ["b"]}, name=name, lock_path=lock))
    )
    t.start()  # the second, started while the first isn't listening yet
    qtbot.wait(300)
    server = InstanceServer(name=name, lock_path=lock)
    got = []
    server.message_received.connect(got.append)
    assert server.start(held)  # the first one now listens
    # keep this (the window's) event loop running until the other copy has its answer
    qtbot.waitUntil(lambda: bool(got == [{"convert": ["b"]}] and results), timeout=10000)
    t.join(5)
    assert results and results[0][0] is True
    server.server.close()  # don't leave a listening server for Python's final clean-up
    held.unlock()


def test_where_the_running_copy_listens(monkeypatch):
    from umc.gui import single_instance as si

    monkeypatch.setattr(si.sys, "platform", "win32")
    assert si._server_address("UniversalEdit-me") == r"\\.\pipe\UniversalEdit-me"


def _thumbnails_done(qtbot, windows):
    """Converter windows make thumbnails on worker threads: let them finish before the
    windows go away (a late thumbnail landing on a deleted window crashes Qt)."""
    for w in windows:
        qtbot.waitUntil(lambda w=w: not w.file_list.thumbnails_pending(), timeout=10000)
