"""Installed Linux app, first start: "Open your files with Universal Edit?" (asked once)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from umc.gui import default_viewer_prompt as prompt
from umc.tools import linux_integration as li


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    yield s
    s.sync()


def test_only_the_installed_linux_app_asks(settings):
    assert prompt.wanted(settings, frozen=True, platform="linux")
    assert not prompt.wanted(settings, frozen=False, platform="linux")  # from source
    assert not prompt.wanted(settings, frozen=True, platform="win32")
    settings.setValue(prompt.ASKED, True)
    assert not prompt.wanted(settings, frozen=True, platform="linux")


def test_yes_makes_the_viewer_the_default(qapp, settings, tmp_path):
    assert prompt.offer(settings, ask=lambda parent: True, home=tmp_path)
    assert li.is_default_viewer(tmp_path)
    text = li.mimeapps_path(tmp_path).read_text(encoding="utf-8")
    for mime in ("text/markdown", "image/png", "application/pdf", "text/x-vue"):
        assert f"{mime}={li.VIEWER_ENTRY};" in text
    for mime in ("application/zip", "font/ttf", "application/x-shellscript"):
        assert f"{mime}=" not in text  # archives, fonts and scripts are left alone
    assert settings.value(prompt.ASKED, False, bool)
    assert li.restore_defaults(tmp_path)  # Settings switch / removal undo it
    assert not li.is_default_viewer(tmp_path)


def test_not_now_changes_nothing_and_never_asks_again(qapp, settings, tmp_path):
    assert not prompt.offer(settings, ask=lambda parent: False, home=tmp_path)
    assert not li.mimeapps_path(tmp_path).exists()
    assert not prompt.wanted(settings, frozen=True, platform="linux")


def test_already_the_default_is_not_asked(qapp, settings, tmp_path):
    li.set_default_viewer(home=tmp_path)

    def ask(parent):
        raise AssertionError("asked")

    assert not prompt.offer(settings, ask=ask, home=tmp_path)
