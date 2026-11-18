"""Linux file managers' right-click "Quick Convert" (no Qt). Linux has no one menu system, so
each file manager gets what it can show:

* **Nautilus** (GNOME, Debian's default): a Python extension (``packaging/linux/
  nautilus-quick-convert.py``, needs ``python3-nautilus``) reading ``quick-convert.json``
  (``quick_table``) — the same nested submenu as on Windows.
* **Dolphin** (KDE): one service menu per menu shape (``dolphin_menus``), matched by MIME type;
  a one-level submenu, long ones with group names in the labels ("Audio only: MP3").
* **Thunar** (Xfce): custom actions in the user's own ``~/.config/Thunar/uca.xml`` (there is
  no system folder for them), all in Thunar's "Quick Convert" submenu, each limited to the file
  patterns it fits (``thunar_actions``). Written / removed by ``set_thunar``.
* **Nemo** (Cinnamon): its actions can't be grouped, so one "Quick Convert…" opens the app's
  format picker (``--quick-choose``; ``nemo_action``).

The .deb installs the Nautilus, Dolphin and Nemo files system-wide (``scripts/build_deb.py``);
Thunar's entries are per user, added by the app (Settings) and removed by ``--unregister``."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from umc.core import quickconvert as qc
from umc.tools import linux_desktop, shell_integration

log = logging.getLogger(__name__)

APP = linux_desktop.APP_ID  # the command and icon name
DATA_DIR = f"/usr/share/{APP}"  # quick-convert.json for the Nautilus extension
TABLE_NAME = "quick-convert.json"
NAUTILUS_EXT_DIR = "/usr/share/nautilus-python/extensions"
NAUTILUS_EXT_NAME = f"{APP}-quick-convert.py"
# KF6 and KF5 ≥ 5.85 (Debian 12, Ubuntu 22.04) both read this; also writing the old
# kservices5 folder would show the menu twice on KF5.
DOLPHIN_DIRS = ("/usr/share/kio/servicemenus",)
NEMO_DIR = "/usr/share/nemo/actions"
NEMO_NAME = f"{APP}-quick-convert.nemo_action"
THUNAR_ID = f"{APP}-quick-convert"  # every action's unique-id starts with this


def _menus() -> dict[str, qc.Menu]:
    return qc.menus(shell_integration.convert_extensions())


# --------------------------------------------------------------------------- Nautilus
def quick_table(menus: dict[str, qc.Menu] | None = None) -> dict:
    """What the Nautilus extension needs: each menu's entries, and which extension has which."""
    menus = _menus() if menus is None else menus
    shapes: dict[str, list] = {}
    for menu in menus.values():
        shapes.setdefault(
            menu.key,
            [[group, [[i.target_id, i.label] for i in items]] for group, items in menu.entries()],
        )
    return {
        "menu_text": qc.MENU_TEXT,
        "open_text": qc.OPEN_TEXT,
        "command": APP,
        "menus": shapes,
        "extensions": {ext: m.key for ext, m in sorted(menus.items())},
    }


# --------------------------------------------------------------------------- Dolphin
def _mime_menus(menus: dict[str, qc.Menu], mime: dict[str, list[str]]) -> dict[str, qc.Menu]:
    """{MIME type: menu}. A type shared by extensions with different menus keeps the menu of
    the first extension (alphabetical), so no file ever gets two Quick Convert menus."""
    out: dict[str, qc.Menu] = {}
    for ext in sorted(menus):
        for t in mime.get(ext, ()):
            out.setdefault(t, menus[ext])
    return out


def dolphin_menus(
    menus: dict[str, qc.Menu] | None = None, mime: dict[str, list[str]] | None = None
) -> dict[str, str]:
    """{file name: service-menu text} — one per menu shape, for the MIME types that have it."""
    menus = _menus() if menus is None else menus
    mime = linux_desktop.known_mime() if mime is None else mime
    by_key: dict[str, tuple[qc.Menu, list[str]]] = {}
    for t, menu in sorted(_mime_menus(menus, mime).items()):
        by_key.setdefault(menu.key, (menu, []))[1].append(t)
    files = {}
    for key, (menu, types) in sorted(by_key.items()):
        labels = menu.flat_labels()
        actions = ["ue_open", "_SEPARATOR_"] + [f"ue_{n}" for n in range(len(labels))]
        lines = [
            "[Desktop Entry]",
            "Type=Service",
            "X-KDE-ServiceTypes=KonqPopupMenu/Plugin",
            f"MimeType={';'.join(types)};",
            f"Actions={';'.join(actions)};",
            f"X-KDE-Submenu={qc.MENU_TEXT}",
            f"Icon={APP}",
            "",
            "[Desktop Action ue_open]",
            f"Name={qc.OPEN_TEXT}",
            f"Icon={APP}",
            f"Exec={APP} --convert %F",
        ]
        for n, (item, label) in enumerate(labels):
            lines += [
                "",
                f"[Desktop Action ue_{n}]",
                f"Name={label}",
                f"Exec={APP} --quick-convert {item.target_id} %F",
            ]
        files[f"{APP}-quick-convert-{key}.desktop"] = "\n".join(lines) + "\n"
    return files


# --------------------------------------------------------------------------- Nemo
def nemo_action(menus: dict[str, qc.Menu] | None = None) -> str:
    menus = _menus() if menus is None else menus
    return (
        "\n".join(
            [
                "[Nemo Action]",
                f"Name={qc.MENU_TEXT}…",
                "Comment=Convert to another format, saved next to the original",
                f"Exec={APP} --quick-choose %F",
                f"Icon-Name={APP}",
                "Selection=notnone",
                f"Extensions={';'.join(sorted(menus))};",
            ]
        )
        + "\n"
    )


# --------------------------------------------------------------------------- Thunar
def _patterns(exts: list[str]) -> str:
    return ";".join(p for e in sorted(exts) for p in (f"*.{e}", f"*.{e.upper()}"))


def thunar_actions(menus: dict[str, qc.Menu] | None = None) -> list[dict[str, str]]:
    """One action per (format, label) — each for the extensions whose menu has it under that
    label — in the order the menus list them, "Open in Universal Edit…" first."""
    menus = _menus() if menus is None else menus
    order: list[tuple[str, str]] = []
    exts_of: dict[tuple[str, str], list[str]] = {}
    for ext, menu in sorted(menus.items()):
        for item, label in menu.flat_labels():
            key = (item.target_id, label)
            if key not in exts_of:
                exts_of[key] = []
                order.append(key)
            exts_of[key].append(ext)
    actions = [
        {
            "id": f"{THUNAR_ID}-open",
            "name": qc.OPEN_TEXT,
            "command": f"{APP} --convert %F",
            "patterns": _patterns(list(menus)),
        }
    ]
    for n, (target_id, label) in enumerate(order):
        actions.append(
            {
                "id": f"{THUNAR_ID}-{n:03d}",
                "name": label,
                "command": f"{APP} --quick-convert {target_id} %F",
                "patterns": _patterns(exts_of[(target_id, label)]),
            }
        )
    return actions


THUNAR_TYPES = ("image-files", "video-files", "audio-files", "text-files", "other-files")


def thunar_uca_path(home: Path | None = None) -> Path:
    if home is None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    else:
        base = home / ".config"
    return base / "Thunar" / "uca.xml"


def _read_uca(path: Path) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
        if root.tag == "actions":
            return root
        log.warning("%s isn't a Thunar actions file; leaving it alone", path)
        raise ValueError(path)
    except FileNotFoundError:
        return ET.Element("actions")


def _ours(action: ET.Element) -> bool:
    return (action.findtext("unique-id") or "").startswith(THUNAR_ID)


def refuse_links_as_root(path: Path) -> None:
    """Run as root for someone else's home (the package's removal): never follow a link the
    user made — it could point at a file only root may change."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        for p in (path, path.parent):
            if p.is_symlink():
                raise OSError(f"{p} is a link; not changed")


def _write_uca(path: Path, root: ET.Element) -> None:
    """Written next to it and swapped in (keeps the owner when run as root for a user)."""
    refuse_links_as_root(path)
    ET.indent(root, space="\t")
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".uca-", dir=path.parent)
    with os.fdopen(fd, "wb") as f:
        f.write(data + b"\n")
    if path.exists():
        st = os.lstat(path)
        shutil.copymode(path, tmp)
        if hasattr(os, "chown") and os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


def thunar_registered(path: Path | None = None) -> bool:
    path = thunar_uca_path() if path is None else path
    try:
        return any(_ours(a) for a in _read_uca(path).findall("action"))
    except (ValueError, ET.ParseError, OSError):
        return False


def set_thunar(on: bool, path: Path | None = None) -> bool:
    """Add (or remove) Quick Convert in Thunar's right-click menu; other actions are kept.
    False when the file can't be read (it is never overwritten then)."""
    path = thunar_uca_path() if path is None else path
    try:
        root = _read_uca(path)
    except (ValueError, ET.ParseError, OSError):
        log.warning("Thunar's %s can't be read; not changed", path)
        return False
    for action in [a for a in root.findall("action") if _ours(a)]:
        root.remove(action)
    if on:
        for a in thunar_actions():
            el = ET.SubElement(root, "action")
            for tag, text in (
                ("icon", APP),
                ("name", a["name"]),
                ("submenu", qc.MENU_TEXT),
                ("unique-id", a["id"]),
                ("command", a["command"]),
                ("description", "Universal Edit: converted next to the original"),
                ("range", "*"),
                ("patterns", a["patterns"]),
            ):
                ET.SubElement(el, tag).text = text
            for t in THUNAR_TYPES:
                ET.SubElement(el, t)
    elif not path.exists():
        return True
    try:
        _write_uca(path, root)
    except OSError:
        log.exception("couldn't write %s", path)
        return False
    return True


THUNAR_SETTING = "linux/thunar_quick_convert"  # QSettings: the user's choice (default on)
THUNAR_VERSION = "linux/thunar_quick_convert_version"  # the app version that last wrote it


def sync_thunar(wanted: bool, written_by: str | None, version: str) -> str | None:
    """At start-up of the installed app: Thunar's entries as the user wants them, rewritten
    when this version's menus may differ. Returns the version to remember (None = unchanged).
    Never adds anything when Thunar isn't there."""
    if not wanted or not thunar_present():
        return None
    if written_by == version and thunar_registered():
        return None
    return version if set_thunar(True) else None


def thunar_present() -> bool:
    """Thunar is installed, or its settings folder exists."""
    return shutil.which("thunar") is not None or thunar_uca_path().parent.is_dir()


def mime_icons(mime: dict[str, list[str]] | None = None) -> dict[str, str]:
    """{icon name ("image-png"): file-icon kind} for every type the viewer shows. Installed
    in the hicolor theme, they're used where the desktop's own theme has no icon of that exact
    name (Adwaita draws every picture with one generic icon), so kinds look different."""
    from umc.core import fileicons

    mime = linux_desktop.known_mime() if mime is None else mime
    out: dict[str, str] = {}
    for ext in sorted(shell_integration.view_extensions()):
        icon = fileicons.icon_for_ext(ext)
        if icon is None:
            continue
        for t in mime.get(ext, ()):
            out.setdefault(t.replace("/", "-"), icon.kind)
    return out


def write_system_files(root: Path, menus: dict[str, qc.Menu] | None = None) -> list[Path]:
    """The .deb's Nautilus / Dolphin / Nemo files and file-type icons under ``root`` (the
    future /)."""
    from umc.core import fileicons

    menus = _menus() if menus is None else menus
    written = []

    def put(rel: str, text: str) -> None:
        p = root / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        p.chmod(0o644)
        written.append(p)

    put(f"{DATA_DIR}/{TABLE_NAME}", json.dumps(quick_table(menus), indent=1) + "\n")
    source = Path(__file__).resolve().parents[3] / "packaging" / "linux" / NAUTILUS_EXT_NAME
    put(f"{NAUTILUS_EXT_DIR}/{NAUTILUS_EXT_NAME}", source.read_text(encoding="utf-8"))
    for folder in DOLPHIN_DIRS:
        for name, text in dolphin_menus(menus).items():
            put(f"{folder}/{name}", text)
    put(f"{NEMO_DIR}/{NEMO_NAME}", nemo_action(menus))
    svgs = {icon.kind: fileicons.svg(icon.kind) for icon in fileicons.ICONS}
    for name, kind in mime_icons().items():
        put(f"/usr/share/icons/hicolor/scalable/mimetypes/{name}.svg", svgs[kind])
    return written
