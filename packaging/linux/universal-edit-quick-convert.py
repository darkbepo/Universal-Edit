"""Universal Edit — "Quick Convert" in Nautilus's right-click menu (GNOME Files).

Installed by the universal-edit .deb into /usr/share/nautilus-python/extensions (needs the
python3-nautilus package). The menus come from /usr/share/universal-edit/quick-convert.json,
written by the app's build (umc.tools.linux_filemanagers.quick_table): picking a format runs
``universal-edit --quick-convert <format> FILE…``, which converts with the default settings and
saves next to each file. Works with Nautilus 3.0 (GTK 3) and 4.0 (GTK 4) extension APIs."""

import contextlib
import json
import os
import subprocess

import gi

try:
    gi.require_version("Nautilus", "4.0")
except ValueError:
    gi.require_version("Nautilus", "3.0")
from gi.repository import GObject, Nautilus  # noqa: E402

TABLE = "/usr/share/universal-edit/quick-convert.json"


def _load():
    try:
        with open(TABLE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _ext(name):
    """The lowercase extension Universal Edit keys its menus by ("photo.JPG" → "jpg")."""
    base, dot, ext = name.rpartition(".")
    return ext.lower() if dot and base else ""


def _local_path(f):
    if f.get_uri_scheme() != "file":
        return None
    location = f.get_location()
    return location.get_path() if location is not None else None


def _run(_item, args):
    with contextlib.suppress(OSError):
        subprocess.Popen(args, start_new_session=True)


class UniversalEditQuickConvert(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        self.table = _load()

    def get_file_items(self, *args):
        files = args[-1]  # Nautilus 3 passes (window, files), 4 passes (files)
        table = self.table
        if not table or not files:
            return []
        paths, keys = [], set()
        for f in files:
            path = _local_path(f)
            key = table["extensions"].get(_ext(os.path.basename(path or "")))
            if path is None or key is None:
                return []
            paths.append(path)
            keys.add(key)
        if len(keys) != 1:  # different kinds of file: their menus differ
            return []
        command = table["command"]
        top = Nautilus.MenuItem(
            name="UniversalEdit::QuickConvert", label=table["menu_text"], icon="universal-edit"
        )
        menu = Nautilus.Menu()
        top.set_submenu(menu)
        item = Nautilus.MenuItem(name="UniversalEdit::Open", label=table["open_text"])
        item.connect("activate", _run, [command, "--convert", *paths])
        menu.append_item(item)
        for n, (group, items) in enumerate(table["menus"][keys.pop()]):
            if group:
                parent = Nautilus.MenuItem(name=f"UniversalEdit::Group{n}", label=group)
                target = Nautilus.Menu()
                parent.set_submenu(target)
                menu.append_item(parent)
            else:
                target = menu
            for target_id, label in items:
                item = Nautilus.MenuItem(name=f"UniversalEdit::{target_id}", label=label)
                item.connect("activate", _run, [command, "--quick-convert", target_id, *paths])
                target.append_item(item)
        return [top]

    def get_background_items(self, *args):
        return []
