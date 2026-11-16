"""Right-click "Convert" and "Open with" registration, against a fake HKCU."""

import pytest

from umc.tools import shell_integration as shell


class FakeRegistry:
    def __init__(self) -> None:
        self.keys: dict[str, dict[str, str]] = {}
        self.protected: set[str] = set()  # keys Windows won't let go of (UCPD-protected)

    def _k(self, path: str) -> str:
        return path.lower()

    def set_value(self, path, name, value):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):  # parent keys exist too, like in the real registry
            self.keys.setdefault(self._k("\\".join(parts[:i])), {})
        self.keys[self._k(path)][name] = value

    def set_empty(self, path, name):
        self.set_value(path, name, "")

    def set_dword(self, path, name, value):
        self.set_value(path, name, int(value))

    def get_value(self, path, name):
        return self.keys.get(self._k(path), {}).get(name)

    def delete_value(self, path, name):
        self.keys.get(self._k(path), {}).pop(name, None)

    def delete_tree(self, path):
        p = self._k(path)
        if p in self.protected:
            return
        for k in [k for k in self.keys if k == p or k.startswith(p + "\\")]:
            del self.keys[k]

    def has_key(self, path):
        return self._k(path) in self.keys

    def values(self, path):
        return {k: str(v) for k, v in self.keys.get(self._k(path), {}).items()}

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


def test_code_files_are_open_with_only():
    # nothing to convert a .py / .bat to, and .bat / .cmd run on double-click
    conv, view = shell.convert_extensions(), shell.view_extensions()
    for ext in ("css", "js", "json", "sql"):
        assert ext not in conv or ext == "json"
        assert ext in view
    assert "ts" in conv  # still a video format


def test_runnable_scripts_are_left_alone():
    # registering a handler for a type Windows runs on double-click can bring up the
    # "How do you want to open this?" question; leave those types alone entirely
    conv, view = shell.convert_extensions(), shell.view_extensions()
    for ext in ("bat", "cmd", "ps1", "psm1", "py", "pyw", "sh", "bash", "zsh"):
        assert ext not in conv and ext not in view, ext


def test_code_files_get_no_convert_entry(reg):
    shell.register_context_menu(reg)
    shell.register_open_with(reg)
    menu = r"Software\Classes\SystemFileAssociations\.css\shell\UniversalEdit.Convert"
    assert not reg.has_key(menu)
    css = reg.get_value(r"Software\Classes\.css\OpenWithProgids", "UniversalEdit.View.code")
    assert css == ""


def test_context_menu(reg):
    shell.register_context_menu(reg)
    key = r"Software\Classes\SystemFileAssociations\.png\shell\UniversalEdit.Convert"
    assert reg.get_value(key, "MUIVerb") == "Quick Convert"
    sub = reg.get_value(key, "ExtendedSubCommandsKey")
    assert sub.startswith("UniversalEdit.QuickConvert.")
    jpg = r"Software\Classes\SystemFileAssociations\.jpg\shell\UniversalEdit.Convert"
    assert reg.get_value(jpg, "ExtendedSubCommandsKey") != sub  # JPG's menu has PNG, not JPG
    items = rf"Software\Classes\{sub}\shell"
    names = reg.subkeys(items)
    first = rf"{items}\{names[0]}"
    assert reg.get_value(first, "MUIVerb") == "Open in Universal Edit…"
    command = reg.get_value(first + r"\command", "")
    assert command.endswith('--convert "%1"') and "umc" in command  # from a checkout: -m umc
    assert reg.get_value(first, "CommandFlags") == shell.SEPARATOR_AFTER
    labels = {reg.get_value(rf"{items}\{n}", "MUIVerb"): n for n in names}
    assert "PNG" not in labels and "ZIP" not in labels  # same format / packing aren't offered
    assert "Searchable PDF" in labels  # small groups stay in the menu itself
    image = rf"{items}\{labels['Image']}"  # a long group gets its own submenu
    image_sub = reg.get_value(image, "ExtendedSubCommandsKey")
    jpg_items = rf"Software\Classes\{image_sub}\shell"
    jpg_cmd = {
        reg.get_value(rf"{jpg_items}\{n}", "MUIVerb"): reg.get_value(
            rf"{jpg_items}\{n}\command", ""
        )
        for n in reg.subkeys(jpg_items)
    }
    assert jpg_cmd["JPG"].endswith('--quick-convert jpg "%1"')
    assert reg.get_value(rf"{jpg_items}\{reg.subkeys(jpg_items)[0]}", "MultiSelectModel") == (
        "Player"
    )
    mp3 = r"Software\Classes\SystemFileAssociations\.mp3\shell\UniversalEdit.Convert"
    mp3_items = rf"Software\Classes\{reg.get_value(mp3, 'ExtendedSubCommandsKey')}\shell"
    mp3_labels = [reg.get_value(rf"{mp3_items}\{n}", "MUIVerb") for n in reg.subkeys(mp3_items)]
    assert mp3_labels[1:4] == ["M4A (AAC)", "WAV", "FLAC"]  # short menus stay flat
    assert shell.context_menu_registered(reg)
    other = r"Software\Classes\SystemFileAssociations\.png\shell\SomeoneElse"
    reg.set_value(other, "MUIVerb", "theirs")
    shell.unregister_context_menu(reg)
    assert not shell.context_menu_registered(reg)
    assert reg.get_value(other, "MUIVerb") == "theirs"  # others' entries untouched
    assert not [k for k in reg.keys if "quickconvert" in k]  # the shared submenus are gone too


def test_open_with(reg):
    reg.set_value(r"Software\Classes\.png\OpenWithProgids", "Photos.File", "")
    shell.register_open_with(reg)
    png = r"Software\Classes\.png\OpenWithProgids"
    assert reg.get_value(png, "UniversalEdit.View.image") == ""  # one ProgID per kind of file
    cmd = reg.get_value(r"Software\Classes\UniversalEdit.View.image\shell\open\command", "")
    assert cmd.endswith('--view "%1"')
    icon = reg.get_value(r"Software\Classes\UniversalEdit.View.image\DefaultIcon", "")
    assert icon.endswith("image.ico")  # its own icon
    assert reg.get_value(r"Software\Classes\UniversalEdit.View.image", "") == (
        "Picture (Universal Edit)"
    )
    caps = shell.CAPABILITIES + r"\FileAssociations"
    assert reg.get_value(caps, ".mp4") == "UniversalEdit.View.video"
    assert reg.get_value(caps, ".mp3") == "UniversalEdit.View.audio"
    assert reg.get_value(caps, ".pdf") == "UniversalEdit.View.document"
    # the one ProgID older versions used stays, so a default chosen back then still opens
    old = reg.get_value(r"Software\Classes\UniversalEdit.View\shell\open\command", "")
    assert old.endswith('--view "%1"')
    assert reg.get_value(shell.REGISTERED_APPS, "Universal Edit") == shell.CAPABILITIES
    assert shell.open_with_registered(reg)
    shell.unregister_open_with(reg)
    assert not shell.open_with_registered(reg)
    assert reg.get_value(png, "UniversalEdit.View.image") is None
    assert reg.get_value(png, "Photos.File") == ""  # kept
    assert not reg.has_key(r"Software\Classes\UniversalEdit.View")
    assert not reg.has_key(r"Software\Classes\UniversalEdit.View.image")
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
    monkeypatch.setattr(shell.sys, "platform", "darwin")  # (Linux has its own, below)
    assert shell.run_cli(["--register", "all"]) == 1


FILE_EXTS = shell.FILE_EXTS


def test_uninstall_gives_hijacked_defaults_back(reg):
    shell.register_open_with(reg)
    ours = rf"{FILE_EXTS}\.png\UserChoice"
    reg.set_value(ours, "ProgId", "UniversalEdit.View.image")
    reg.set_value(ours, "Hash", "abc")
    old = rf"{FILE_EXTS}\.gif\UserChoice"
    reg.set_value(old, "ProgId", "Applications\\UniversalEdit.exe")
    theirs = rf"{FILE_EXTS}\.jpg\UserChoice"
    reg.set_value(theirs, "ProgId", "Photos.File")
    latest = rf"{FILE_EXTS}\.webp\UserChoiceLatest"
    reg.set_value(latest + r"\ProgId", "ProgId", "UniversalEdit.View.image")
    owl = rf"{FILE_EXTS}\.png\OpenWithList"
    reg.set_value(owl, "a", "mspaint.exe")
    reg.set_value(owl, "b", "UniversalEdit.exe")
    reg.set_value(owl, "MRUList", "ba")
    owp = rf"{FILE_EXTS}\.png\OpenWithProgids"
    reg.set_empty(owp, "UniversalEdit.View")
    reg.set_empty(owp, "Paint.Picture")
    assert shell.unregister_open_with(reg) == []
    assert not reg.has_key(ours) and not reg.has_key(old) and not reg.has_key(latest)
    assert reg.get_value(theirs, "ProgId") == "Photos.File"  # another app's choice: kept
    assert reg.values(owl) == {"a": "mspaint.exe", "MRUList": "a"}
    assert reg.values(owp) == {"Paint.Picture": ""}


def test_protected_defaults_are_reported(reg, monkeypatch):
    monkeypatch.setattr(shell, "registry", lambda: reg)
    monkeypatch.setattr(shell, "notify_explorer", lambda: None)
    shell.register_open_with(reg)
    pdf = rf"{FILE_EXTS}\.pdf\UserChoice"
    reg.set_value(pdf, "ProgId", "UniversalEdit.View.document")
    reg.protected.add(pdf.lower())
    assert shell.run_cli(["--unregister", "all"]) == shell.STUCK_EXIT
    assert not shell.feature_on("open-with", reg)  # everything else is gone


def test_registering_again_keeps_the_users_defaults(reg):
    shell.register_open_with(reg)
    choice = rf"{FILE_EXTS}\.png\UserChoice"
    reg.set_value(choice, "ProgId", "UniversalEdit.View.image")
    shell.register_open_with(reg)  # an update re-registers: the choice stays
    assert reg.get_value(choice, "ProgId") == "UniversalEdit.View.image"


def test_every_viewer_kind_has_an_icon_file():
    from umc.core import fileicons

    kinds = {shell.progid_for(e) for e in shell.view_extensions()}
    assert kinds <= {f"UniversalEdit.View.{i.kind}" for i in fileicons.ICONS}
    for icon in fileicons.ICONS:
        assert fileicons.ico_path(icon.kind).is_file(), icon.kind  # scripts/make_file_icons.py


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
    if shell.sys.platform.startswith("linux"):  # Linux: Thunar's Quick Convert instead
        assert d.thunar_box is not None and d.boxes["open-with"].isHidden()
    else:
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


def test_copies_started_together_end_up_in_one(tmp_path):
    """Explorer starts one copy per selected file at once: one takes the lock and becomes the
    window (here: its own process, listening only after a second, like a slow start), the
    rest wait until it listens and hand their file over — once."""
    import json
    import uuid

    from tests.instance_window import start_window
    from umc.gui.single_instance import claim

    name = f"umc-test-{uuid.uuid4().hex[:8]}"
    lock = str(tmp_path / "i.lock")
    window = start_window(name, lock, listen_after=1.0)
    try:
        assert claim({"convert": ["b"]}, name=name, lock_path=lock) == (True, None)
        lines = [window.stdout.readline().strip(), window.stdout.readline().strip()]
        assert lines[0] == "ready" and json.loads(lines[1]) == {"convert": ["b"]}
    finally:
        window.wait(20)


def test_where_the_running_copy_listens(monkeypatch):
    from umc.gui import single_instance as si

    monkeypatch.setattr(si.sys, "platform", "win32")
    assert si._server_address("UniversalEdit-me") == r"\\.\pipe\UniversalEdit-me"


def _thumbnails_done(qtbot, windows):
    """Converter windows make thumbnails on worker threads: let them finish before the
    windows go away (a late thumbnail landing on a deleted window crashes Qt)."""
    for w in windows:
        qtbot.waitUntil(lambda w=w: not w.file_list.thumbnails_pending(), timeout=10000)


def test_a_busy_window_is_never_sent_the_file_twice(monkeypatch, tmp_path):
    from umc.gui import single_instance as si

    calls = []
    monkeypatch.setattr(si, "_offer", lambda m, n, t: calls.append(m) or "silent")
    assert si.claim({"view": "a"}, name="x", lock_path=str(tmp_path / "l")) == (False, None)
    assert len(calls) == 1  # connected but no answer: open our own window, don't resend
