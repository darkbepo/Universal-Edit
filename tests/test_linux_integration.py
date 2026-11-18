"""Linux: making the viewer the default in mimeapps.list and giving the old choices back."""

import json
import sys

import pytest

from umc.tools import linux_integration as li

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux settings files")

BEFORE = """\
# my settings
[Default Applications]
image/png=org.gnome.Loupe.desktop;
text/plain=org.gnome.TextEditor.desktop;

[Added Associations]
image/png=org.gnome.Loupe.desktop;gimp.desktop;
"""


def test_rewrite_keeps_everything_else():
    text = li.rewrite(BEFORE, li.DEFAULTS, {"image/png": ["a.desktop"], "video/mp4": ["b.desktop"]})
    assert "# my settings" in text and "text/plain=org.gnome.TextEditor.desktop;" in text
    assert li.read_section(text, li.DEFAULTS)["image/png"] == ["a.desktop"]
    assert li.read_section(text, li.DEFAULTS)["video/mp4"] == ["b.desktop"]
    assert li.read_section(text, li.ADDED)["image/png"] == [
        "org.gnome.Loupe.desktop",
        "gimp.desktop",
    ]
    gone = li.rewrite(text, li.DEFAULTS, {"video/mp4": None})
    assert "video/mp4" not in gone
    fresh = li.rewrite("", li.DEFAULTS, {"image/png": ["x.desktop"]})
    assert fresh == "[Default Applications]\nimage/png=x.desktop;\n"


def test_default_viewer_and_back(tmp_path):
    home = tmp_path
    mimeapps = li.mimeapps_path(home)
    mimeapps.parent.mkdir(parents=True)
    mimeapps.write_text(BEFORE, encoding="utf-8")
    assert not li.is_default_viewer(home)
    li.set_default_viewer(["image/png", "video/mp4"], home)
    assert li.is_default_viewer(home)
    defaults = li.read_section(mimeapps.read_text(encoding="utf-8"), li.DEFAULTS)
    assert defaults["image/png"] == ["universal-edit.desktop", "org.gnome.Loupe.desktop"]
    assert defaults["video/mp4"] == ["universal-edit.desktop"]
    li.set_default_viewer(["image/png", "video/mp4"], home)  # again: the backup stays the first
    backup = json.loads((home / ".config" / li.BACKUP_NAME).read_text(encoding="utf-8"))
    assert backup == {"image/png": ["org.gnome.Loupe.desktop"]}
    # someone also added "Convert with" to a type by hand
    text = mimeapps.read_text(encoding="utf-8").replace(
        "image/png=org.gnome.Loupe.desktop;gimp.desktop;",
        "image/png=org.gnome.Loupe.desktop;gimp.desktop;universal-edit_convert.desktop;",
    )
    mimeapps.write_text(text, encoding="utf-8")
    assert li.restore_defaults(home)
    after = mimeapps.read_text(encoding="utf-8")
    assert "universal-edit" not in after
    assert li.read_section(after, li.DEFAULTS)["image/png"] == ["org.gnome.Loupe.desktop"]
    assert "video/mp4" not in li.read_section(after, li.DEFAULTS)
    assert li.read_section(after, li.ADDED)["image/png"] == [
        "org.gnome.Loupe.desktop",
        "gimp.desktop",
    ]
    assert "# my settings" in after
    assert not (home / ".config" / li.BACKUP_NAME).exists()
    assert not li.restore_defaults(home)  # nothing left to do


def test_unregister_for_a_user_cleans_thunar_and_defaults(tmp_path, monkeypatch):
    from umc.tools import linux_filemanagers as lf

    home = tmp_path / "alice"
    (home / ".config").mkdir(parents=True)
    li.set_default_viewer(["image/png"], home)
    uca = lf.thunar_uca_path(home)
    assert lf.set_thunar(True, uca)
    monkeypatch.setattr(li, "user_homes", lambda: [home])
    assert li.run_cli(False, ["context-menu", "open-with"], all_users=True) == 0
    assert not lf.thunar_registered(uca) and not li.is_default_viewer(home)
    assert li.run_cli(True, ["context-menu"], all_users=True) == 0
    assert lf.thunar_registered(uca)


def test_cli_goes_to_linux_without_a_registry(monkeypatch):
    from umc.tools import shell_integration as shell

    calls = []
    monkeypatch.setattr(shell, "registry", lambda: None)
    monkeypatch.setattr(shell.sys, "platform", "linux")
    monkeypatch.setattr(li, "run_cli", lambda *a: calls.append(a) or 0)
    assert shell.run_cli(["--unregister", "all", "--all-users"]) == 0
    assert calls == [(False, ["context-menu", "open-with"], True)]


def test_root_never_follows_a_users_link(tmp_path, monkeypatch):
    target = tmp_path / "root-only"
    target.write_text("[Default Applications]\nimage/png=universal-edit.desktop;\n")
    home = tmp_path / "bob"
    (home / ".config").mkdir(parents=True)
    li.mimeapps_path(home).symlink_to(target)
    monkeypatch.setattr(li.os, "geteuid", lambda: 0)
    with pytest.raises(OSError, match="is a link"):
        li.restore_defaults(home)
    assert "universal-edit" in target.read_text()  # untouched
