"""Windows Explorer integration: "Quick Convert" in the right-click menu, and Universal Edit
in "Open with" (the viewer) — both for the current user only (HKCU, no admin).

Quick Convert is a cascading menu per file type (``core/quickconvert.py`` decides what's in
it): "Open in Universal Edit…" (the converter window, as before), then each format, which
converts at once with the default settings and saves next to the file. File types with the
same menu share one ``UniversalEdit.QuickConvert.<key>`` key (``ExtendedSubCommandsKey``);
long menus get one submenu per group (Image, Audio only, …).

Used by the installer (``UniversalEdit.exe --register …`` / ``--unregister all``) and by the
app's Settings window. Windows doesn't let a program make itself the default app: we register
as *able* to open the files (OpenWithProgids + Capabilities); the user picks it once in
Windows' "Default apps" page (``open_default_apps``). On Windows 11 the right-click entry is
under "Show more options".

All registry access goes through a ``Registry`` (``WinRegistry`` on Windows), so the key
layout is tested on any OS with a fake one."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from umc.core import detect as det
from umc.viewer import files as vf

log = logging.getLogger(__name__)

CLASSES = r"Software\Classes"
VERB = "UniversalEdit.Convert"
PROGID = "UniversalEdit.View"
# Not Software\UniversalEdit: QSettings keeps the app's own settings there (branding.ORG_NAME).
APP_KEY = r"Software\UniversalEdit.Integration"
CAPABILITIES = APP_KEY + r"\Capabilities"
REGISTERED_APPS = r"Software\RegisteredApplications"
APP_NAME = "Universal Edit"
EXE_NAME = "UniversalEdit.exe"
MENU_TEXT = "Convert with Universal Edit"  # Linux's "Open with" entry for the converter
QUICK_TEXT = "Quick Convert"
SUBMENU_PREFIX = "UniversalEdit.QuickConvert"
SEPARATOR_AFTER = 0x40  # ECF_SEPARATORAFTER in a sub-command's CommandFlags


class Registry(Protocol):
    """HKEY_CURRENT_USER; paths like ``Software\\Classes\\.png``; ``name`` "" = default value."""

    def set_value(self, path: str, name: str, value: str) -> None: ...
    def set_empty(self, path: str, name: str) -> None: ...  # REG_NONE (OpenWithProgids)
    def set_dword(self, path: str, name: str, value: int) -> None: ...
    def get_value(self, path: str, name: str) -> str | None: ...
    def delete_value(self, path: str, name: str) -> None: ...
    def delete_tree(self, path: str) -> None: ...
    def has_key(self, path: str) -> bool: ...
    def subkeys(self, path: str) -> list[str]: ...
    def values(self, path: str) -> dict[str, str]: ...  # name → data (as text)


class WinRegistry:
    """The real HKCU (Windows only)."""

    def __init__(self) -> None:
        import winreg

        self.w = winreg
        self.root = winreg.HKEY_CURRENT_USER

    def set_value(self, path: str, name: str, value: str) -> None:
        with self.w.CreateKeyEx(self.root, path, 0, self.w.KEY_WRITE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_SZ, value)

    def set_empty(self, path: str, name: str) -> None:
        with self.w.CreateKeyEx(self.root, path, 0, self.w.KEY_WRITE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_NONE, None)

    def set_dword(self, path: str, name: str, value: int) -> None:
        with self.w.CreateKeyEx(self.root, path, 0, self.w.KEY_WRITE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_DWORD, value)

    def get_value(self, path: str, name: str) -> str | None:
        try:
            with self.w.OpenKey(self.root, path) as k:
                return str(self.w.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def delete_value(self, path: str, name: str) -> None:
        try:
            with self.w.OpenKey(self.root, path, 0, self.w.KEY_SET_VALUE) as k:
                self.w.DeleteValue(k, name)
        except OSError:
            pass

    def delete_tree(self, path: str) -> None:
        for sub in self.subkeys(path):
            self.delete_tree(path + "\\" + sub)
        with contextlib.suppress(OSError):
            self.w.DeleteKey(self.root, path)

    def has_key(self, path: str) -> bool:
        try:
            with self.w.OpenKey(self.root, path):
                return True
        except OSError:
            return False

    def subkeys(self, path: str) -> list[str]:
        try:
            with self.w.OpenKey(self.root, path) as k:
                count = self.w.QueryInfoKey(k)[0]
                return [self.w.EnumKey(k, i) for i in range(count)]
        except OSError:
            return []

    def values(self, path: str) -> dict[str, str]:
        try:
            with self.w.OpenKey(self.root, path) as k:
                count = self.w.QueryInfoKey(k)[1]
                out = {}
                for i in range(count):
                    name, data, _kind = self.w.EnumValue(k, i)
                    out[name] = "" if data is None else str(data)
                return out
        except OSError:
            return {}


# --------------------------------------------------------------------------- which files
def known_extensions() -> list[str]:
    """Every extension the app reads ("tar.gz" style ones can't have a menu entry)."""
    exts = set()
    for name in dir(det):
        if name.endswith("_EXTS") and isinstance(getattr(det, name), set | frozenset):
            exts |= set(getattr(det, name))
    exts |= set(det.ALIASES)
    return sorted(e for e in exts if "." not in e)


def convert_extensions() -> list[str]:
    """Extensions that get the "Convert" right-click entry: not code files (nothing to convert
    a .py or .bat to; they're offered in Open with only)."""
    code_only = det.CODE_EXTS - {"ts"}  # .ts is also a video format
    return [e for e in known_extensions() if e not in code_only]


# Types Windows runs on double-click: a new "Open with" handler for them can bring up the "How
# do you want to open this file?" question, so the app leaves them alone.
RUNNABLE_EXTS = frozenset({"bat", "cmd", "ps1", "psm1", "py", "pyw", "sh", "bash", "zsh"})


def view_extensions() -> list[str]:
    """Extensions the viewer shows (Open with)."""
    return [
        e
        for e in known_extensions()
        if e not in RUNNABLE_EXTS and vf.kind_of_path(Path("x." + e)) != vf.OTHER
    ]


def launch_command(*args: str) -> str:
    """How Explorer starts us: the .exe when built, else pythonw -m umc (from a checkout)."""
    if getattr(sys, "frozen", False):
        head = f'"{sys.executable}"'
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        head = f'"{pythonw if pythonw.exists() else exe}" -m umc'
    return " ".join([head, *args])


def icon_ref() -> str:
    return f"{sys.executable},0" if getattr(sys, "frozen", False) else ""


# ------------------------------------------------------------------- right-click Quick Convert
def _key_name(index: int, text: str) -> str:
    """A sub-command key: numbered (Explorer lists them by name), no backslashes."""
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in text)
    return f"{index:02d}_{safe}"


def _command(reg: Registry, key: str, label: str, command: str) -> None:
    reg.set_value(key, "MUIVerb", label)
    reg.set_value(key, "MultiSelectModel", "Player")  # each selected file is converted
    reg.set_value(key + r"\command", "", command)


def _write_submenu(reg: Registry, name: str, menu) -> None:
    from umc.core import quickconvert as qc

    shell = rf"{CLASSES}\{name}\shell"
    first = shell + "\\" + _key_name(0, "open")
    _command(reg, first, qc.OPEN_TEXT, launch_command("--convert", '"%1"'))
    if icon_ref():
        reg.set_value(first, "Icon", icon_ref())
    if menu is None or not menu.items:
        return
    reg.set_dword(first, "CommandFlags", SEPARATOR_AFTER)

    def item(key: str, it) -> None:
        _command(reg, key, it.label, launch_command("--quick-convert", it.target_id, '"%1"'))

    for n, (group, items) in enumerate(menu.entries(), start=1):
        if not group:
            item(shell + "\\" + _key_name(n, items[0].target_id), items[0])
            continue
        sub = f"{name}.{n:02d}"
        key = shell + "\\" + _key_name(n, group)
        reg.set_value(key, "MUIVerb", group)
        reg.set_value(key, "ExtendedSubCommandsKey", sub)
        for m, it in enumerate(items, start=1):
            item(rf"{CLASSES}\{sub}\shell\{_key_name(m, it.target_id)}", it)


def register_context_menu(reg: Registry) -> None:
    from umc.core import quickconvert as qc

    unregister_context_menu(reg)  # also what an older version registered
    exts = convert_extensions()
    table = qc.menus(exts)
    written: set[str] = set()
    for ext in exts:
        menu = table.get(ext)
        name = f"{SUBMENU_PREFIX}.{menu.key if menu else 'open'}"
        key = rf"{CLASSES}\SystemFileAssociations\.{ext}\shell\{VERB}"
        reg.set_value(key, "MUIVerb", QUICK_TEXT)
        reg.set_value(key, "ExtendedSubCommandsKey", name)
        if icon_ref():
            reg.set_value(key, "Icon", icon_ref())
        if name not in written:
            _write_submenu(reg, name, menu)
            written.add(name)


def unregister_context_menu(reg: Registry) -> None:
    """Our verb wherever it is — also for extensions an older version listed and this one
    doesn't — and the shared submenus. Other programs' entries are never touched."""
    base = rf"{CLASSES}\SystemFileAssociations"
    exts = {"." + e for e in convert_extensions()} | set(reg.subkeys(base))
    for sub in sorted(exts):
        key = rf"{base}\{sub}\shell\{VERB}"
        if reg.has_key(key):
            reg.delete_tree(key)
    prefix = SUBMENU_PREFIX.lower() + "."
    for sub in reg.subkeys(CLASSES):
        if sub.lower().startswith(prefix):
            reg.delete_tree(rf"{CLASSES}\{sub}")


def context_menu_registered(reg: Registry) -> bool:
    key = rf"{CLASSES}\SystemFileAssociations\.png\shell\{VERB}"
    return reg.get_value(key, "ExtendedSubCommandsKey") is not None


# --------------------------------------------------------------------------- Open with (viewer)
def progid_for(ext: str) -> str:
    """The ProgID a file type opens with: one per kind of file (``UniversalEdit.View.image``
    …), so each kind shows its own icon once Universal Edit is its default app."""
    from umc.core import fileicons

    icon = fileicons.icon_for_ext(ext)
    return f"{PROGID}.{icon.kind}" if icon is not None else PROGID


def _kind_icon(kind: str) -> str:
    """DefaultIcon for a kind: its .ico when the build has it, else the app's icon."""
    from umc.core import fileicons

    path = fileicons.ico_path(kind)
    return str(path) if path.is_file() else icon_ref()


def _write_progid(reg: Registry, progid: str, title: str, icon: str, command: str) -> None:
    prog = rf"{CLASSES}\{progid}"
    reg.set_value(prog, "", title)
    reg.set_value(prog, "FriendlyTypeName", title)
    if icon:
        reg.set_value(prog + r"\DefaultIcon", "", icon)
    reg.set_value(prog + r"\shell\open\command", "", command)


def register_open_with(reg: Registry) -> None:
    from umc.core import fileicons

    unregister_open_with(reg, reset=False)  # also what an older version registered
    command = launch_command("--view", '"%1"')
    # The one ProgID older versions used: kept so a default chosen back then still opens.
    _write_progid(reg, PROGID, f"{APP_NAME} file", icon_ref(), command)
    for icon in fileicons.ICONS:
        title = f"{icon.title} ({APP_NAME})"
        _write_progid(reg, f"{PROGID}.{icon.kind}", title, _kind_icon(icon.kind), command)
    app = rf"{CLASSES}\Applications\{EXE_NAME}"
    reg.set_value(app, "FriendlyAppName", APP_NAME)
    reg.set_value(app + r"\shell\open\command", "", command)
    reg.set_value(CAPABILITIES, "ApplicationName", APP_NAME)
    reg.set_value(
        CAPABILITIES, "ApplicationDescription", "View, convert and edit pictures, video, …"
    )
    if icon_ref():
        reg.set_value(CAPABILITIES, "ApplicationIcon", icon_ref())
    for ext in view_extensions():
        progid = progid_for(ext)
        reg.set_empty(rf"{CLASSES}\.{ext}\OpenWithProgids", progid)
        reg.set_empty(app + r"\SupportedTypes", "." + ext)
        reg.set_value(CAPABILITIES + r"\FileAssociations", "." + ext, progid)
    reg.set_value(REGISTERED_APPS, APP_NAME, CAPABILITIES)


def _is_ours(progid: str) -> bool:
    p = progid.lower()
    return p == PROGID.lower() or p.startswith(PROGID.lower() + ".")


FILE_EXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"


def _points_to_us(progid: str | None) -> bool:
    if not progid:
        return False
    return _is_ours(progid) or progid.lower() == rf"applications\{EXE_NAME}".lower()


def reset_defaults(reg: Registry) -> list[str]:
    """Forget every "always open with Universal Edit" Windows remembers (the user's default-app
    choices and Explorer's Open with lists), so those files go back to Windows' own choice
    instead of to a program that's gone. Other programs' choices are never touched.

    Returns the extensions Windows wouldn't let go of: Windows 11 protects a few (``.pdf``,
    web links) so only its Settings can change them."""
    stuck: set[str] = set()
    for sub in reg.subkeys(FILE_EXTS):
        base = rf"{FILE_EXTS}\{sub}"
        for choice in ("UserChoice", "UserChoiceLatest"):
            key = rf"{base}\{choice}"
            progid = reg.get_value(key, "ProgId") or reg.get_value(key + r"\ProgId", "ProgId")
            if _points_to_us(progid):
                reg.delete_tree(key)
                if reg.has_key(key):
                    stuck.add(sub)
        owp = rf"{base}\OpenWithProgids"
        for name in [n for n in reg.values(owp) if _is_ours(n)]:
            reg.delete_value(owp, name)
        owl = rf"{base}\OpenWithList"
        vals = reg.values(owl)
        letters = [n for n, d in vals.items() if len(n) == 1 and d.lower() == EXE_NAME.lower()]
        for n in letters:
            reg.delete_value(owl, n)
        if letters and "MRUList" in vals:
            reg.set_value(owl, "MRUList", "".join(c for c in vals["MRUList"] if c not in letters))
    return sorted(stuck)


def unregister_open_with(reg: Registry, reset: bool = True) -> list[str]:
    """Everything "Open with" registered; with ``reset`` also the default-app choices that
    point to us (``reset_defaults`` — not when re-registering, which keeps them)."""
    exts = {"." + e for e in view_extensions()}
    exts |= {k for k in reg.subkeys(CLASSES) if k.startswith(".")}  # older versions' too
    for sub in sorted(exts):
        key = rf"{CLASSES}\{sub}\OpenWithProgids"
        for name in [n for n in reg.values(key) if _is_ours(n)]:
            reg.delete_value(key, name)
    for sub in reg.subkeys(CLASSES):
        if _is_ours(sub):
            reg.delete_tree(rf"{CLASSES}\{sub}")
    reg.delete_tree(rf"{CLASSES}\Applications\{EXE_NAME}")
    reg.delete_value(REGISTERED_APPS, APP_NAME)
    reg.delete_tree(APP_KEY)
    return reset_defaults(reg) if reset else []


def open_with_registered(reg: Registry) -> bool:
    return reg.get_value(REGISTERED_APPS, APP_NAME) == CAPABILITIES


# --------------------------------------------------------------------------- helpers
def registry() -> Registry | None:
    """The real registry on Windows, else None (nothing to integrate with)."""
    return WinRegistry() if sys.platform == "win32" else None


def notify_explorer() -> None:
    """Tell Explorer associations changed, so menus update without a sign-out."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        log.exception("SHChangeNotify failed")


def open_default_apps() -> None:
    """Windows' Default apps page, on Universal Edit's entry where Windows supports that."""
    if sys.platform == "win32":
        import os
        from urllib.parse import quote

        try:
            uri = f"ms-settings:defaultapps?registeredAppUser={quote(APP_NAME)}"
            os.startfile(uri)  # type: ignore[attr-defined]
        except OSError:
            subprocess.run(["explorer", "ms-settings:defaultapps"], check=False)


FEATURES = {
    "context-menu": (register_context_menu, unregister_context_menu, context_menu_registered),
    "open-with": (register_open_with, unregister_open_with, open_with_registered),
}


def set_feature(feature: str, on: bool, reg: Registry | None = None) -> list[str]:
    """Switch a feature; returns the extensions whose default app Windows kept (see
    ``reset_defaults``) — only switching "Open with" off can have any."""
    reg = reg if reg is not None else registry()
    if reg is None:
        raise OSError("This only works on Windows.")
    register, unregister, _ = FEATURES[feature]
    stuck = (register if on else unregister)(reg) or []
    notify_explorer()
    return stuck


def feature_on(feature: str, reg: Registry | None = None) -> bool:
    reg = reg if reg is not None else registry()
    return reg is not None and FEATURES[feature][2](reg)


STUCK_EXIT = 3  # --unregister: done, but Windows kept some defaults (the uninstaller says so)


def run_cli(args: list[str]) -> int:
    """``--register context-menu|open-with|all`` / ``--unregister …`` (the installer and the
    uninstaller run these; on Linux the .deb's removal runs ``--unregister all --all-users``)."""
    action, *names = args
    all_users = "--all-users" in names
    names = [n for n in names if n != "--all-users"] or ["all"]
    wanted = list(FEATURES) if "all" in names else names
    for name in wanted:
        if name not in FEATURES:
            print(f"unknown: {name}", file=sys.stderr)
            return 2
    if registry() is None and sys.platform.startswith("linux"):
        from umc.tools import linux_integration

        return linux_integration.run_cli(action == "--register", wanted, all_users)
    stuck: list[str] = []
    try:
        for name in wanted:
            stuck += set_feature(name, action == "--register")
    except OSError as exc:
        log.exception("shell integration failed")
        print(exc, file=sys.stderr)
        return 1
    if stuck:
        log.warning("Windows kept Universal Edit as the default for: %s", " ".join(stuck))
        print("Windows kept the default app for: " + " ".join(stuck), file=sys.stderr)
        return STUCK_EXIT
    return 0
