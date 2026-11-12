"""Windows Explorer integration: "Convert with Universal Edit" in the right-click menu, and
Universal Edit in "Open with" (the viewer) — both for the current user only (HKCU, no admin).

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
MENU_TEXT = "Convert with Universal Edit"


class Registry(Protocol):
    """HKEY_CURRENT_USER; paths like ``Software\\Classes\\.png``; ``name`` "" = default value."""

    def set_value(self, path: str, name: str, value: str) -> None: ...
    def set_empty(self, path: str, name: str) -> None: ...  # REG_NONE (OpenWithProgids)
    def get_value(self, path: str, name: str) -> str | None: ...
    def delete_value(self, path: str, name: str) -> None: ...
    def delete_tree(self, path: str) -> None: ...
    def has_key(self, path: str) -> bool: ...
    def subkeys(self, path: str) -> list[str]: ...


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


# --------------------------------------------------------------------------- which files
def convert_extensions() -> list[str]:
    """Every extension the converter reads ("tar.gz" style ones can't have a menu entry)."""
    exts = set()
    for name in dir(det):
        if name.endswith("_EXTS") and isinstance(getattr(det, name), set | frozenset):
            exts |= set(getattr(det, name))
    exts |= set(det.ALIASES)
    return sorted(e for e in exts if "." not in e)


def view_extensions() -> list[str]:
    """Extensions the viewer shows (Open with)."""
    return [e for e in convert_extensions() if vf.kind_of_path(Path("x." + e)) != vf.OTHER]


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


# --------------------------------------------------------------------------- right-click Convert
def register_context_menu(reg: Registry) -> None:
    unregister_context_menu(reg)  # also what an older version registered
    command = launch_command("--convert", '"%1"')
    for ext in convert_extensions():
        key = rf"{CLASSES}\SystemFileAssociations\.{ext}\shell\{VERB}"
        reg.set_value(key, "MUIVerb", MENU_TEXT)
        reg.set_value(key, "MultiSelectModel", "Player")  # several files → one window
        if icon_ref():
            reg.set_value(key, "Icon", icon_ref())
        reg.set_value(key + r"\command", "", command)


def unregister_context_menu(reg: Registry) -> None:
    """Our verb wherever it is — also for extensions an older version listed and this one
    doesn't. Other programs' entries are never touched."""
    base = rf"{CLASSES}\SystemFileAssociations"
    exts = {"." + e for e in convert_extensions()} | set(reg.subkeys(base))
    for sub in sorted(exts):
        key = rf"{base}\{sub}\shell\{VERB}"
        if reg.has_key(key):
            reg.delete_tree(key)


def context_menu_registered(reg: Registry) -> bool:
    return reg.has_key(rf"{CLASSES}\SystemFileAssociations\.png\shell\{VERB}\command")


# --------------------------------------------------------------------------- Open with (viewer)
def register_open_with(reg: Registry) -> None:
    unregister_open_with(reg)  # also what an older version registered
    command = launch_command("--view", '"%1"')
    prog = rf"{CLASSES}\{PROGID}"
    reg.set_value(prog, "", f"File opened with {APP_NAME}")
    reg.set_value(prog, "FriendlyTypeName", f"{APP_NAME} file")
    if icon_ref():
        reg.set_value(prog + r"\DefaultIcon", "", icon_ref())
    reg.set_value(prog + r"\shell\open\command", "", command)
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
        reg.set_empty(rf"{CLASSES}\.{ext}\OpenWithProgids", PROGID)
        reg.set_empty(app + r"\SupportedTypes", "." + ext)
        reg.set_value(CAPABILITIES + r"\FileAssociations", "." + ext, PROGID)
    reg.set_value(REGISTERED_APPS, APP_NAME, CAPABILITIES)


def unregister_open_with(reg: Registry) -> None:
    exts = {"." + e for e in view_extensions()}
    exts |= {k for k in reg.subkeys(CLASSES) if k.startswith(".")}  # older versions' too
    for sub in sorted(exts):
        reg.delete_value(rf"{CLASSES}\{sub}\OpenWithProgids", PROGID)
    reg.delete_tree(rf"{CLASSES}\{PROGID}")
    reg.delete_tree(rf"{CLASSES}\Applications\{EXE_NAME}")
    reg.delete_value(REGISTERED_APPS, APP_NAME)
    reg.delete_tree(APP_KEY)


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


def set_feature(feature: str, on: bool, reg: Registry | None = None) -> None:
    reg = reg if reg is not None else registry()
    if reg is None:
        raise OSError("This only works on Windows.")
    register, unregister, _ = FEATURES[feature]
    (register if on else unregister)(reg)
    notify_explorer()


def feature_on(feature: str, reg: Registry | None = None) -> bool:
    reg = reg if reg is not None else registry()
    return reg is not None and FEATURES[feature][2](reg)


def run_cli(args: list[str]) -> int:
    """``--register context-menu|open-with|all`` / ``--unregister …`` (the installer runs these)."""
    action, *names = args
    names = names or ["all"]
    wanted = list(FEATURES) if "all" in names else names
    try:
        for name in wanted:
            if name not in FEATURES:
                print(f"unknown: {name}", file=sys.stderr)
                return 2
            set_feature(name, action == "--register")
    except OSError as exc:
        log.exception("shell integration failed")
        print(exc, file=sys.stderr)
        return 1
    return 0
