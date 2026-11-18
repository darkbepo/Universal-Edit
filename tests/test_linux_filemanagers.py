"""Quick Convert in Linux file managers: Nautilus extension data, Dolphin service menus, Nemo's
action and Thunar's per-user actions (uca.xml)."""

import importlib.util
import json
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from umc.core import quickconvert as qc
from umc.tools import linux_filemanagers as lf

ROOT = Path(__file__).resolve().parent.parent


def test_nautilus_table_matches_the_menus():
    table = lf.quick_table()
    assert table["command"] == "universal-edit"
    png_key = table["extensions"]["png"]
    assert png_key == table["extensions"]["png"] and png_key != table["extensions"]["jpg"]
    entries = table["menus"][png_key]
    assert entries == [
        [g, [[i.target_id, i.label] for i in items]] for g, items in qc.menu_for("png").entries()
    ]
    json.dumps(table)  # plain JSON


def test_dolphin_service_menus():
    files = lf.dolphin_menus()
    assert all(name.startswith("universal-edit-quick-convert-") for name in files)
    png = next(text for text in files.values() if "image/png;" in text)
    assert "X-KDE-Submenu=Quick Convert" in png and "KonqPopupMenu/Plugin" in png
    assert "Exec=universal-edit --quick-convert jpg %F" in png
    assert "Exec=universal-edit --convert %F" in png
    assert "Actions=ue_open;_SEPARATOR_;ue_0;" in png
    mp4 = next(text for text in files.values() if "video/mp4;" in text)
    assert "Name=Audio only: MP3" in mp4  # long menus: group names in the labels
    seen = {}
    for name, text in files.items():  # a MIME type gets one Quick Convert menu, never two
        mime_line = next(line for line in text.splitlines() if line.startswith("MimeType="))
        for t in filter(None, mime_line[len("MimeType=") :].split(";")):
            assert t not in seen, (t, name, seen.get(t))
            seen[t] = name


def test_nemo_action_opens_the_picker():
    text = lf.nemo_action()
    assert "Exec=universal-edit --quick-choose %F" in text
    assert "Selection=notnone" in text and ";png;" in text and "Name=Quick Convert…" in text


def test_thunar_actions_cover_each_format_once_per_label():
    actions = lf.thunar_actions()
    assert actions[0]["command"] == "universal-edit --convert %F"
    jpg = [a for a in actions if a["command"] == "universal-edit --quick-convert jpg %F"]
    assert len(jpg) == 1 and "*.png" in jpg[0]["patterns"] and "*.PNG" in jpg[0]["patterns"]
    assert "*.jpg;" not in jpg[0]["patterns"] + ";"  # JPG files don't get "→ JPG"
    ids = [a["id"] for a in actions]
    assert len(ids) == len(set(ids)) and all(i.startswith(lf.THUNAR_ID) for i in ids)


def test_thunar_uca_add_and_remove_keeps_other_actions(tmp_path):
    uca = tmp_path / "Thunar" / "uca.xml"
    uca.parent.mkdir()
    uca.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n<action>'
        "<icon>utilities-terminal</icon><name>Open Terminal Here</name>"
        "<unique-id>1-1</unique-id><command>exo-open --launch TerminalEmulator</command>"
        "<patterns>*</patterns><directories/></action>\n</actions>\n",
        encoding="utf-8",
    )
    assert not lf.thunar_registered(uca)
    assert lf.set_thunar(True, uca)
    assert lf.thunar_registered(uca)
    root = ET.parse(uca).getroot()
    ours = [a for a in root.findall("action") if a.findtext("unique-id").startswith(lf.THUNAR_ID)]
    assert ours and all(a.findtext("submenu") == "Quick Convert" for a in ours)
    assert ours[0].find("image-files") is not None
    assert lf.set_thunar(True, uca)  # again: replaced, not doubled
    again = ET.parse(uca).getroot().findall("action")
    assert len(again) == len(root.findall("action"))
    assert lf.set_thunar(False, uca)
    left = ET.parse(uca).getroot().findall("action")
    assert [a.findtext("name") for a in left] == ["Open Terminal Here"]
    assert not lf.thunar_registered(uca)


def test_thunar_new_file_and_broken_file(tmp_path):
    fresh = tmp_path / "a" / "Thunar" / "uca.xml"
    assert lf.set_thunar(False, fresh) and not fresh.exists()  # nothing to remove
    assert lf.set_thunar(True, fresh) and lf.thunar_registered(fresh)
    broken = tmp_path / "b.xml"
    broken.write_text("<actions><action>", encoding="utf-8")
    assert not lf.set_thunar(True, broken)
    assert broken.read_text(encoding="utf-8") == "<actions><action>"  # never overwritten


def test_system_files_for_the_package(tmp_path):
    written = lf.write_system_files(tmp_path)
    rel = {str(p.relative_to(tmp_path)) for p in written}
    assert "usr/share/universal-edit/quick-convert.json" in rel
    assert "usr/share/nautilus-python/extensions/universal-edit-quick-convert.py" in rel
    assert "usr/share/nemo/actions/universal-edit-quick-convert.nemo_action" in rel
    assert any(r.startswith("usr/share/kio/servicemenus/") for r in rel)
    assert not any(r.startswith("usr/share/kservices5/") for r in rel)  # would show twice


# --------------------------------------------------------------------------- the Nautilus extension
class _Item:
    def __init__(self, name, label, icon=None):
        self.name, self.label, self.submenu, self.handlers = name, label, None, []

    def set_submenu(self, menu):
        self.submenu = menu

    def connect(self, _signal, fn, *args):
        self.handlers.append((fn, args))


class _Menu:
    def __init__(self):
        self.items = []

    def append_item(self, item):
        self.items.append(item)


class _File:
    def __init__(self, path, scheme="file"):
        self.path, self.scheme = path, scheme

    def get_uri_scheme(self):
        return self.scheme

    def get_location(self):
        return types.SimpleNamespace(get_path=lambda: self.path)


@pytest.fixture
def nautilus_ext(monkeypatch, tmp_path):
    """The extension loaded with a stand-in ``gi`` (python3-nautilus isn't needed)."""
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    repo = types.ModuleType("gi.repository")
    repo.GObject = types.SimpleNamespace(GObject=type("GObject", (), {}))
    provider = type("MenuProvider", (), {})
    repo.Nautilus = types.SimpleNamespace(MenuProvider=provider, MenuItem=_Item, Menu=_Menu)
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    table = tmp_path / "quick-convert.json"
    table.write_text(json.dumps(lf.quick_table()), encoding="utf-8")
    source = ROOT / "packaging" / "linux" / lf.NAUTILUS_EXT_NAME
    spec = importlib.util.spec_from_file_location("ue_nautilus_ext", source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TABLE", str(table))
    return mod


def test_nautilus_extension_menu(nautilus_ext, monkeypatch):
    runs = []
    monkeypatch.setattr(nautilus_ext.subprocess, "Popen", lambda args, **_k: runs.append(args))
    ext = nautilus_ext.UniversalEditQuickConvert()
    (top,) = ext.get_file_items(None, [_File("/h/a.PNG"), _File("/h/b.png")])  # Nautilus 3
    assert top.label == "Quick Convert"
    labels = [i.label for i in top.submenu.items]
    assert labels[0] == "Open in Universal Edit…" and "Image" in labels
    image = next(i for i in top.submenu.items if i.label == "Image")
    jpg = next(i for i in image.submenu.items if i.label == "JPG")
    fn, args = jpg.handlers[0]
    fn(jpg, *args)
    assert runs == [["universal-edit", "--quick-convert", "jpg", "/h/a.PNG", "/h/b.png"]]
    assert ext.get_file_items([_File("/h/a.png"), _File("/h/c.mp4")]) == []  # mixed kinds
    assert ext.get_file_items([_File("/h/notes.unknownext")]) == []
    assert ext.get_file_items([_File("/h/a.png", scheme="sftp")]) == []
    assert ext.get_file_items([]) == []
