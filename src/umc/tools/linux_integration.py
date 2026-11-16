"""Linux: making Universal Edit the default viewer, and undoing everything the app changed in
a user's own settings (no Qt).

Unlike Windows, Linux lets an app choose the default: ``set_default_viewer`` writes the
viewer into ``~/.config/mimeapps.list`` ([Default Applications]) for every type it shows, and
remembers what the user had chosen before in ``~/.config/universal-edit-defaults.json``.
``restore_defaults`` takes our entries out again (also from [Added Associations]) and puts
the remembered ones back, so nothing points to a removed app.

The .deb's removal runs ``universal-edit --unregister all --all-users`` as root: then every
user's mimeapps.list and Thunar actions (``linux_filemanagers``) are cleaned, keeping each
file's owner."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from umc.tools import linux_desktop, linux_filemanagers, shell_integration

log = logging.getLogger(__name__)

OUR_ENTRIES = (f"{linux_desktop.APP_ID}.desktop", f"{linux_desktop.CONVERT_ID}.desktop")
VIEWER_ENTRY = OUR_ENTRIES[0]
DEFAULTS = "Default Applications"
ADDED = "Added Associations"
BACKUP_NAME = "universal-edit-defaults.json"


def config_dir(home: Path | None = None) -> Path:
    if home is None:
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return home / ".config"


def mimeapps_path(home: Path | None = None) -> Path:
    return config_dir(home) / "mimeapps.list"


def viewer_types() -> list[str]:
    return linux_desktop.mime_types(shell_integration.view_extensions())


# --------------------------------------------------------------------------- mimeapps.list
def _parse(text: str) -> list[tuple[str | None, str]]:
    """Lines as (section, line) — kept as they are, so comments and other keys survive."""
    out, section = [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        out.append((section, line))
    return out


def _entries(value: str) -> list[str]:
    return [e for e in value.split(";") if e.strip()]


def read_section(text: str, section: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sec, line in _parse(text):
        if sec == section and "=" in line and not line.lstrip().startswith(("#", "[")):
            key, value = line.split("=", 1)
            out[key.strip()] = _entries(value)
    return out


def rewrite(text: str, section: str, changes: dict[str, list[str] | None]) -> str:
    """``text`` with ``section``'s keys set (a list) or removed (None); the section is made
    when missing. Everything else stays exactly as it was."""
    lines = _parse(text)
    done: set[str] = set()
    out: list[str] = []
    seen_section = False
    for i, (sec, line) in enumerate(lines):
        if sec == section and "=" in line and not line.lstrip().startswith(("#", "[")):
            key = line.split("=", 1)[0].strip()
            if key in changes:
                done.add(key)
                value = changes[key]
                if value:
                    out.append(f"{key}={';'.join(value)};")
                continue
        out.append(line)
        if sec == section:
            seen_section = True
        ends_section = i + 1 == len(lines) or lines[i + 1][0] != sec
        if sec == section and ends_section:
            out.extend(_new_lines(changes, done))
    if not seen_section:
        new = _new_lines(changes, done)
        if new:
            if out and out[-1].strip():
                out.append("")
            out += [f"[{section}]", *new]
    return "\n".join(out) + "\n" if out else ""


def _new_lines(changes: dict[str, list[str] | None], done: set[str]) -> list[str]:
    lines = []
    for key, value in changes.items():
        if key not in done and value:
            lines.append(f"{key}={';'.join(value)};")
            done.add(key)
    return lines


def _write(path: Path, text: str) -> None:
    """Written next to it and swapped in; a file of another user keeps its owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ue-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if path.exists():
        st = path.stat()
        shutil.copymode(path, tmp)
        if os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
    elif os.geteuid() == 0:
        st = path.parent.stat()
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- default viewer
def is_default_viewer(home: Path | None = None) -> bool:
    try:
        text = mimeapps_path(home).read_text(encoding="utf-8")
    except OSError:
        return False
    defaults = read_section(text, DEFAULTS)
    return any(VIEWER_ENTRY in defaults.get(t, [])[:1] for t in ("image/png", "image/jpeg"))


def set_default_viewer(types: list[str] | None = None, home: Path | None = None) -> None:
    """Make the viewer the default for ``types`` (all it shows), remembering the user's own
    earlier choices (only the first time: a second call mustn't remember ours)."""
    types = viewer_types() if types is None else types
    path = mimeapps_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    defaults = read_section(text, DEFAULTS)
    backup = config_dir(home) / BACKUP_NAME
    if not backup.exists():
        before = {t: defaults[t] for t in types if t in defaults}
        _write(backup, json.dumps(before, indent=1, sort_keys=True) + "\n")
    changes: dict[str, list[str] | None] = {}
    for t in types:
        rest = [e for e in defaults.get(t, []) if e not in OUR_ENTRIES]
        changes[t] = [VIEWER_ENTRY, *rest]
    _write(path, rewrite(text, DEFAULTS, changes))


def restore_defaults(home: Path | None = None) -> bool:
    """Take Universal Edit out of the user's mimeapps.list and put back what was there before.
    True when something changed."""
    path = mimeapps_path(home)
    backup = config_dir(home) / BACKUP_NAME
    try:
        before = json.loads(backup.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    new = text
    for section in (DEFAULTS, ADDED):
        current = read_section(new, section)
        changes: dict[str, list[str] | None] = {}
        for key, entries in current.items():
            if any(e in OUR_ENTRIES for e in entries):
                kept = [e for e in entries if e not in OUR_ENTRIES]
                if section == DEFAULTS and key in before:
                    kept = before[key] + [e for e in kept if e not in before[key]]
                changes[key] = kept or None
        if changes:
            new = rewrite(new, section, changes)
    changed = new != text
    if changed:
        _write(path, new)
    if backup.exists():
        backup.unlink()
    return changed


# --------------------------------------------------------------------------- --unregister
def user_homes() -> list[Path]:
    """Everyone's home folder (ordinary accounts), for the package's removal."""
    import pwd  # Unix only

    homes = []
    for entry in pwd.getpwall():
        if 1000 <= entry.pw_uid < 65534 and Path(entry.pw_dir).is_dir():
            homes.append(Path(entry.pw_dir))
    return homes


def clean_home(home: Path | None) -> None:
    restore_defaults(home)
    linux_filemanagers.set_thunar(False, linux_filemanagers.thunar_uca_path(home))


def run_cli(register: bool, features: list[str], all_users: bool) -> int:
    """``--register`` / ``--unregister`` on Linux: Quick Convert in Thunar ("context-menu") and
    the default viewer ("open-with"; registering doesn't make it the default — Settings
    does). ``all_users`` (as root): every user's settings."""
    homes: list[Path | None] = list(user_homes()) if all_users else [None]
    ok = True
    for home in homes:
        try:
            if "context-menu" in features:
                uca = linux_filemanagers.thunar_uca_path(home)
                if register:
                    ok &= linux_filemanagers.set_thunar(True, uca)
                elif uca.exists():
                    ok &= linux_filemanagers.set_thunar(False, uca)
            if "open-with" in features and not register:
                restore_defaults(home)
        except OSError:
            log.exception("cleaning %s failed", home or "this user")
            ok = False
    return 0 if ok else 1
