"""The Update button: finding what's new (app, editors, tools) and updating it."""

import threading
from pathlib import Path

import pytest

from umc import __version__
from umc.tools import installer, updater


def test_versions():
    assert updater.parse_version("v1.2.10") == (1, 2, 10)
    assert updater.parse_version("gs10031") == (10031,)
    assert updater.parse_version("nothing") == ()
    assert updater.is_newer("0.2.0", "0.1.9")
    assert updater.is_newer("1.10", "1.9")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("24.8.2", "24.8.2.1")  # LibreOffice: only the listed parts
    assert updater.is_newer("24.8.3", "24.8.2.1")
    assert updater.is_newer("3.6", "3.5.0")
    assert not updater.is_newer("3.6", None)  # unknown: no guessing
    assert not updater.is_newer("", "1.0")


RELEASE = {
    "tag_name": "v99.0.0",
    "assets": [
        {"name": "UniversalEdit-99.0.0-windows.zip", "browser_download_url": "https://x/zip"},
        {
            "name": "UniversalEdit-setup.exe",
            "browser_download_url": "https://x/setup.exe",
            "digest": "sha256:" + "a" * 64,
        },
        {
            "name": "universal-edit_99.0.0_amd64.deb",
            "browser_download_url": "https://x/ue.deb",
            "digest": "sha256:" + "b" * 64,
        },
    ],
}


def test_release_asset_per_system():
    win = updater.pick_app_asset(RELEASE, "win32")
    assert (win.version, win.url, win.sha256) == ("99.0.0", "https://x/setup.exe", "a" * 64)
    deb = updater.pick_app_asset(RELEASE, "linux")
    assert deb.name == "universal-edit_99.0.0_amd64.deb" and deb.sha256 == "b" * 64
    assert updater.pick_app_asset(RELEASE, "darwin") is None
    assert updater.pick_app_asset({"tag_name": "v1", "assets": []}, "win32") is None


def test_check_app(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_get_json", lambda url: RELEASE)
    assert updater.check_app().version == "99.0.0"
    assert updater.check_app(current="100.0") is None  # already newer
    monkeypatch.setattr(updater, "_get_json", lambda url: {**RELEASE, "prerelease": True})
    assert updater.check_app() is None


def test_report_problems_in_plain_words(monkeypatch):
    def offline(url):
        raise installer.InstallError("Couldn't reach the download server.")

    monkeypatch.setattr(updater, "_get_json", offline)
    report = updater.check_all()
    assert report.app is None and "Couldn't reach the download server." in report.problems[0]


def test_tool_versions_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("UMC_TOOLS_DIR", str(tmp_path))
    assert installer.recorded_versions() == {}
    installer.record_version("Pandoc", "3.5")
    assert installer.installed_version("Pandoc") == "3.5"
    assert installer.private_tools() == []
    (tmp_path / "pandoc").mkdir()
    assert installer.private_tools() == ["Pandoc"]


def test_tools_with_a_newer_version(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(installer, "private_tools", lambda: ["Pandoc", "LibreOffice", "Calibre"])
    have = {"Pandoc": "3.5", "LibreOffice": "24.8.2.1", "Calibre": None}
    monkeypatch.setattr(installer, "installed_version", have.get)
    latest = {"Pandoc": "3.6", "LibreOffice": "24.8.2", "Calibre": "8.0"}
    monkeypatch.setattr(installer, "latest_version", latest.get)
    found = updater.check_tools()
    assert [(t.tool, t.installed, t.latest) for t in found] == [("Pandoc", "3.5", "3.6")]
    done = []
    monkeypatch.setitem(installer.INSTALLERS, "Pandoc", lambda p, c: done.append("Pandoc"))
    assert updater.update_tools(found, lambda f, m: None, threading.Event()) == []
    assert done == ["Pandoc"]


def test_editors_made_for_another_version(monkeypatch):
    from umc.core import addons

    monkeypatch.setattr(addons, "from_source", lambda: False)
    versions = {"image": "0.0.1", "audio": __version__}
    monkeypatch.setattr(
        addons, "manifest", lambda i: {"id": i, "version": versions[i]} if i in versions else None
    )
    assert updater.outdated_addons() == ["image"]


def test_windows_hand_over_to_the_installer():
    cmd = updater.windows_restart_command(
        Path(r"C:\T\UniversalEdit-setup.exe"), Path(r"C:\P\UniversalEdit.exe")
    )
    assert cmd.startswith('cmd /s /c "start "" /wait "C:\\T\\UniversalEdit-setup.exe" /SILENT')
    assert '& start "" "C:\\P\\UniversalEdit.exe""' in cmd and "/CLOSEAPPLICATIONS" in cmd


def test_download_needs_a_checksum():
    bare = updater.AppUpdate("99.0.0", "https://x/setup.exe", None, "UniversalEdit-setup.exe")
    with pytest.raises(updater.UpdateError, match="no checksum"):
        updater.download_app(bare, lambda f, m: None, threading.Event())


def test_linux_update(monkeypatch, tmp_path):
    script = updater.linux_update_script(tmp_path / "ue.deb", ["universal-edit", "pandoc"])
    assert "apt-get update" in script and f"apt-get install -y -q '{tmp_path / 'ue.deb'}'" in script
    assert "--only-upgrade universal-edit pandoc" in script
    assert "--only-upgrade" not in updater.linux_update_script(tmp_path / "x.deb", [])
    report = updater.Report(app=updater.AppUpdate("9", "u", "s", "n"), linux_packages=["ffmpeg"])
    monkeypatch.setattr(updater, "has_apt_source", lambda: True)
    assert updater.linux_packages_for(report) == ["universal-edit", "ffmpeg"]
    monkeypatch.setattr(updater, "has_apt_source", lambda: False)
    assert updater.linux_packages_for(report) == ["ffmpeg"]  # the .deb is installed instead
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    with pytest.raises(updater.UpdateError, match="sudo apt update && sudo apt install"):
        updater.run_linux_update(None, ["ffmpeg"], threading.Event())


def test_apt_dry_run(monkeypatch):
    class Done:
        stdout = "Inst ffmpeg [7:6.1] (7:6.1.1 Ubuntu)\nConf ffmpeg\nInst pandoc [3.1] (3.1.3)\n"

    monkeypatch.setattr(updater.shutil, "which", lambda name: "/usr/bin/apt-get")
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: Done())
    assert updater.apt_upgradable() == ["ffmpeg", "pandoc"]


pytest.importorskip("pytestqt")


def test_dialog_updates_everything_on_windows(qtbot, monkeypatch, tmp_path):
    from umc.gui import update_dialog

    monkeypatch.setattr(update_dialog.sys, "platform", "win32")
    report = updater.Report(
        app=updater.AppUpdate("99.0.0", "u", "s", "UniversalEdit-setup.exe"),
        tools=[updater.ToolUpdate("Pandoc", "3.5", "3.6")],
    )
    calls = []
    monkeypatch.setattr(updater, "update_tools", lambda u, p, c: calls.append("tools") or [])
    setup = tmp_path / "UniversalEdit-setup.exe"
    monkeypatch.setattr(updater, "download_app", lambda u, p, c: calls.append("app") or setup)
    monkeypatch.setattr(updater, "start_windows_update", lambda s: calls.append(("run", s)))
    monkeypatch.setattr(update_dialog.QApplication, "quit", lambda: calls.append("quit"))
    d = update_dialog.UpdateDialog(report=report)
    qtbot.addWidget(d)
    assert d.title.text() == "Updates are ready"
    assert "Pandoc 3.5 → 3.6" in d.lines.text() and "→ 99.0.0" in d.lines.text()
    assert not d.update_button.isHidden()
    d.update_button.click()
    qtbot.waitUntil(lambda: "quit" in calls, timeout=5000)
    assert calls == ["tools", "app", ("run", setup), "quit"]


def test_dialog_when_nothing_is_new(qtbot, monkeypatch):
    from umc.gui import update_dialog

    monkeypatch.setattr(updater, "check_all", lambda: updater.Report())
    d = update_dialog.UpdateDialog()
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.title.text() == "Everything is up to date", timeout=5000)
    assert d.update_button.isHidden() and not d.check_button.isHidden()


def test_daily_check_shows_the_button(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui import update_dialog
    from umc.gui.launcher import AppController

    report = updater.Report(app=updater.AppUpdate("99.0.0", "u", "s", "n"))
    monkeypatch.setattr(updater, "check_all", lambda: report)
    monkeypatch.setattr(update_dialog, "due", lambda now=None: True)
    c = AppController(listen=False)
    w = c.show_main()
    qtbot.addWidget(w)
    assert w.update_available.isHidden()
    c.start_update_check()
    qtbot.waitUntil(lambda: not w.update_available.isHidden(), timeout=5000)
    later = c.free_main()  # a window opened afterwards shows it too
    if later is not w:
        qtbot.addWidget(later)
        assert not later.update_available.isHidden()
