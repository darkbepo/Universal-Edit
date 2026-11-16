"""Linux: missing programs are installed with apt (the command is shown to copy)."""

from __future__ import annotations

import pytest

from umc.tools import linux_packages


def test_every_tool_the_app_asks_for_has_a_command_or_a_download():
    from umc.gui import tool_dialog

    for tool in tool_dialog._CHECKS:
        cmd = linux_packages.apt_command(tool)
        if tool == "LibreDWG":  # not packaged by Ubuntu: the dialog offers the download
            assert cmd is None
        else:
            assert cmd is not None and cmd.startswith("sudo apt install ")


def test_commands():
    assert linux_packages.apt_command("Pandoc") == "sudo apt install pandoc"
    assert linux_packages.apt_command("FFmpeg (ffprobe)") == "sudo apt install ffmpeg"
    assert linux_packages.apt_command("Nothing") is None


@pytest.mark.parametrize("apt", [True, False])
def test_install_command_only_where_apt_is(monkeypatch, apt):
    monkeypatch.setattr(linux_packages, "has_apt", lambda: apt)
    expected = "sudo apt install ghostscript" if apt else None
    assert linux_packages.install_command("Ghostscript") == expected


def test_dialog_shows_the_command_and_copies_it(qtbot, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from umc.gui import tool_dialog
    from umc.tools import installer

    monkeypatch.setattr(installer, "supported", lambda tool: False)
    monkeypatch.setattr(linux_packages, "has_apt", lambda: True)
    dlg = tool_dialog.ToolMissingDialog("Pandoc", "https://pandoc.org", "Pandoc is needed.")
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.command_label.text() == "sudo apt install pandoc"
    assert dlg.command_label.isVisible() and dlg.copy_button.isVisible()
    assert not dlg.install_button.isVisible()
    dlg.copy_button.click()
    assert QGuiApplication.clipboard().text() == "sudo apt install pandoc"
    assert "Copied" in dlg.status.text()


def test_dialog_without_apt_offers_the_download(qtbot, monkeypatch):
    from umc.gui import tool_dialog
    from umc.tools import installer

    monkeypatch.setattr(installer, "supported", lambda tool: False)
    monkeypatch.setattr(linux_packages, "has_apt", lambda: False)
    dlg = tool_dialog.ToolMissingDialog("Pandoc", "https://pandoc.org", "Pandoc is needed.")
    qtbot.addWidget(dlg)
    dlg.show()
    assert not dlg.command_label.isVisible() and not dlg.copy_button.isVisible()
    assert dlg.manual_button.text() == "Download Pandoc"
