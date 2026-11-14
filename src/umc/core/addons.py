"""Editors as add-ons (desktop): the built app ships the converter; each editor is a small zip
downloaded (or picked in the installer) and unpacked into the app-data folder.

An add-on zip holds Python modules under ``umc/…`` (e.g. ``umc/gui/editor/``) and, in
``site/``, third-party packages only that editor needs (the image editor's ``onnxruntime``).
``addon.json`` inside names its id and the app version it belongs to. ``activate`` makes an
installed add-on importable without a restart: ``site/`` goes on ``sys.path`` and each
module folder is appended to its (frozen) parent package's ``__path__``.

Running from source, everything is already there: every add-on counts as installed.

``scripts/build_addons.py`` makes the zips and ``umc/addon_hashes.json`` (bundled into the
app): the SHA-256 every download and installer copy is checked against."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from umc import __version__
from umc.core.errors import ConversionCancelled, ConversionError

log = logging.getLogger(__name__)

RELEASES = "https://github.com/darkbepo/Universal-Edit/releases/download"
HASHES_FILE = Path(__file__).resolve().parent.parent / "addon_hashes.json"
MANIFEST = "addon.json"


@dataclass(frozen=True)
class Addon:
    id: str  # same ids as core/editors.py
    title: str
    # Python modules / packages (dotted) that live only in the add-on, never in the app
    modules: tuple[str, ...]
    # third-party top-level packages shipped in the add-on's site/ folder
    packages: tuple[str, ...]
    # imported to check the add-on works once it's on the path
    entry: str


ADDONS: dict[str, Addon] = {
    "image": Addon(
        "image",
        "Image editor",
        modules=("umc.gui.editor",),
        packages=("onnxruntime",),  # background removal
        entry="umc.gui.editor.editor_page",
    ),
    "model": Addon(
        "model",
        "3D viewer",
        modules=("umc.gui.editor3d.viewer_page",),
        packages=(),
        entry="umc.gui.editor3d.viewer_page",
    ),
    "audio": Addon(
        "audio",
        "Audio editor",
        modules=("umc.gui.audioeditor",),  # the Qt-free model umc.audioedit stays in the app
        packages=(),
        entry="umc.gui.audioeditor.audio_editor_page",
    ),
    "video": Addon(
        "video",
        "Video editor",
        modules=("umc.gui.videoedit",),  # the Qt-free model umc.videoedit stays in the app
        packages=(),
        entry="umc.gui.videoedit.editor_page",
    ),
    "code": Addon(
        "code",
        "Code editor",
        modules=("umc.gui.codeeditor",),  # the Qt-free model umc.codeedit stays in the app
        packages=("pygments",),  # colours
        entry="umc.gui.codeeditor.code_editor_page",
    ),
}

_activated: set[str] = set()
_lock = threading.Lock()


def from_source() -> bool:
    """Running from a checkout (not the built .exe): every add-on is simply there."""
    return not getattr(sys, "frozen", False) and not os.environ.get("UMC_ADDONS_TEST")


def addons_root() -> Path:
    override = os.environ.get("UMC_ADDONS_DIR")  # tests
    if override:
        return Path(override)
    from umc.core.paths import app_data_dir

    return app_data_dir() / "addons"


def addon_dir(addon_id: str) -> Path:
    return addons_root() / addon_id


def manifest(addon_id: str) -> dict | None:
    try:
        data = json.loads((addon_dir(addon_id) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def installed(addon_id: str) -> bool:
    """Present and made for this app version (an older one must be replaced)."""
    if from_source():
        return True
    data = manifest(addon_id)
    return bool(data) and data.get("id") == addon_id and data.get("version") == __version__


def is_addon(editor_id: str) -> bool:
    return editor_id in ADDONS


def ready(editor_id: str) -> bool:
    """The editor can open now (not an add-on, or installed — then also switched on)."""
    if editor_id not in ADDONS:
        return True
    if not installed(editor_id):
        return False
    activate(editor_id)
    return True


def activate(addon_id: str) -> None:
    """Make an installed add-on importable in this process (idempotent)."""
    if from_source():
        return
    with _lock:
        if addon_id in _activated:
            return
        root = addon_dir(addon_id)
        site = root / "site"
        if site.is_dir() and str(site) not in sys.path:
            sys.path.insert(0, str(site))  # (native libraries load from beside their .pyd)
        for module in ADDONS[addon_id].modules:
            parent_name, _, _ = module.rpartition(".")
            parent = importlib.import_module(parent_name)
            folder = str(root.joinpath(*parent_name.split(".")))
            path = getattr(parent, "__path__", None)
            if path is not None and folder not in list(path):
                path.append(folder)
        importlib.invalidate_caches()
        _activated.add(addon_id)


def activate_all() -> None:
    """At startup: switch on every installed add-on (a broken one is logged, not fatal)."""
    for addon_id in ADDONS:
        if installed(addon_id):
            try:
                activate(addon_id)
            except Exception:
                log.exception("add-on %s couldn't be switched on", addon_id)


# --------------------------------------------------------------------------- installing
def known_downloads() -> dict:
    """{id: {"file", "sha256", "size"}} for this build (empty when built without add-ons)."""
    try:
        data = json.loads(HASHES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def download_size(addon_id: str) -> int:
    return int(known_downloads().get(addon_id, {}).get("size", 0))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for info in zf.infolist():
        name = PurePosixPath(info.filename.replace("\\", "/"))
        if name.is_absolute() or ".." in name.parts or ":" in info.filename:
            raise ConversionError("The add-on download is damaged (unsafe file names).")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:  # a symbolic link
            raise ConversionError("The add-on download is damaged (links inside).")
        members.append(info)
    return members


_install_locks: dict[str, threading.Lock] = {}


def install_from_zip(addon_id: str, zip_path: Path, expected_sha256: str | None = None) -> None:
    """Check, unpack into the add-ons folder (replacing an older copy), switch on. One install
    of an add-on at a time (two windows may ask together); a finished one wins."""
    with _lock:
        lock = _install_locks.setdefault(addon_id, threading.Lock())
    with lock:
        if installed(addon_id) and addon_dir(addon_id).exists() and addon_id in _activated:
            return  # another window just installed it
        _install(addon_id, zip_path, expected_sha256)


def _install(addon_id: str, zip_path: Path, expected_sha256: str | None) -> None:
    if addon_id not in ADDONS:
        raise ConversionError(f"There's no add-on called {addon_id!r}.")
    expected = expected_sha256 or known_downloads().get(addon_id, {}).get("sha256")
    if not expected:
        raise ConversionError(
            "This build doesn't know the add-on's fingerprint, so it won't install it."
        )
    if sha256_of(zip_path) != expected.lower():
        raise ConversionError("The add-on download doesn't match its fingerprint. Try again.")
    root = addons_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"umc-addon-{addon_id}-", dir=root) as tmp:
        staging = Path(tmp) / addon_id
        with zipfile.ZipFile(zip_path) as zf:
            members = _safe_members(zf)
            zf.extractall(staging, members)
        try:
            data = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConversionError(
                "The add-on download is damaged (no add-on description)."
            ) from exc
        if not isinstance(data, dict):
            raise ConversionError("The add-on download is damaged (no add-on description).")
        if data.get("id") != addon_id or data.get("version") != __version__:
            raise ConversionError(
                f"That add-on is for version {data.get('version')}, not {__version__}."
            )
        target = addon_dir(addon_id)
        if target.exists():
            if addon_id in _activated:  # in use by this process: finish after a restart
                raise ConversionError("Restart Universal Edit to update this editor.")
            shutil.rmtree(target)
        staging.rename(target)
    activate(addon_id)
    importlib.import_module(ADDONS[addon_id].entry)  # proves it loads


def download(
    addon_id: str,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Download this version's add-on from the GitHub release, then install it."""
    info = known_downloads().get(addon_id)
    if not info:
        raise ConversionError(
            "This copy of Universal Edit has no editor downloads. Use the installer, or a "
            "release build."
        )
    url = os.environ.get("UMC_ADDONS_URL", f"{RELEASES}/v{__version__}") + "/" + info["file"]
    root = addons_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="umc-download-", dir=root) as tmp:
        dest = Path(tmp) / info["file"]
        try:
            with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
                total = int(resp.headers.get("Content-Length") or info.get("size") or 0)
                done = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise ConversionCancelled()
                    block = resp.read(1 << 16)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if progress and total:
                        progress(min(done / total, 1.0))
        except OSError as exc:
            log.warning("add-on download failed: %s (%s)", url, exc)
            raise ConversionError(
                "The editor couldn't be downloaded. Check the internet connection and try again."
            ) from exc
        install_from_zip(addon_id, dest, info["sha256"])


def remove(addon_id: str) -> None:
    """Delete an installed add-on (if it's in use, it's gone after the next start)."""
    target = addon_dir(addon_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def run_cli(args: list[str]) -> int:
    """``--install-addon ID ZIP`` / ``--install-addon all FOLDER`` (the installer, CI)."""
    if len(args) != 3:
        print("usage: --install-addon ID ZIP | --install-addon all FOLDER", file=sys.stderr)
        return 2
    _, which, source = args
    try:
        if which == "all":  # every add-on zip in the folder (the installer copies the chosen ones)
            found = 0
            for addon_id, info in known_downloads().items():
                zip_path = Path(source) / info["file"]
                if zip_path.is_file():
                    install_from_zip(addon_id, zip_path)
                    found += 1
            if not found:
                raise ConversionError(f"No editor add-ons in {source}.")
        else:
            install_from_zip(which, Path(source))
    except (OSError, ConversionError) as exc:
        log.exception("add-on install failed")
        print(exc, file=sys.stderr)
        return 1
    return 0
