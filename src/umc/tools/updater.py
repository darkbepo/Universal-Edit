"""The "Update" button (no Qt): what can be updated, and doing it.

* **The app** — the newest GitHub release of Universal Edit (``RELEASES_API``, or
  ``UMC_UPDATE_URL``). Windows: its ``UniversalEdit-setup.exe``, checked against the SHA-256
  GitHub publishes for it, run quietly over the installed copy (the per-user install folder
  is writable without admin rights; the installer keeps the chosen editors and switches),
  then the app starts again. Linux: apt — from the app's apt repository when it's set up,
  else the release's .deb — in one ``pkexec`` (one password prompt), together with the tools.
* **Editors** (Windows add-ons) installed for an older version: downloaded again for this
  one (an add-on only loads in the version it was made for).
* **Tools**: Windows — LibreOffice, Pandoc, Ghostscript, Calibre and LibreDWG that the app
  installed into its own folder (``installer.private_tools``), when a newer version is out;
  copies installed by the user are theirs to update. Linux — the distribution's packages
  (FFmpeg, Pandoc, LibreOffice, …), upgraded by apt in the same step.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from umc import __version__
from umc.core.errors import ConversionError

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/darkbepo/Universal-Edit/releases/latest"
WINDOWS_ASSET = "UniversalEdit-setup.exe"
LINUX_ASSET = re.compile(r"universal-edit_[\d.]+_amd64\.deb")
APT_PACKAGE = "universal-edit"
# The distribution packages the app uses (tools/linux_packages.py names them); apt upgrades the
# ones that are installed and skips the others.
LINUX_TOOL_PACKAGES = (
    "ffmpeg",
    "pandoc",
    "ghostscript",
    "libarchive-tools",
    "libreoffice",
    "calibre",
    "python3-nautilus",
)
Progress = Callable[[float, str], None]


class UpdateError(ConversionError):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    """ "v1.2.10" → (1, 2, 10); anything that isn't a version → ()."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(p) for p in m.group(0).split(".")) if m else ()


def is_newer(latest: str, installed: str | None) -> bool:
    """``latest`` is newer than ``installed``. Only as many parts as ``latest`` has are
    compared (LibreOffice lists 24.8.2, installs 24.8.2.1); an unknown installed version is
    never "older" — the update would be a guess."""
    new, old = parse_version(latest), parse_version(installed or "")
    if not new or not old:
        return False
    old = old[: len(new)]
    return new > old + (0,) * (len(new) - len(old))


@dataclass
class AppUpdate:
    version: str
    url: str
    sha256: str | None
    name: str


@dataclass
class ToolUpdate:
    tool: str
    installed: str
    latest: str


@dataclass
class Report:
    app: AppUpdate | None = None
    tools: list[ToolUpdate] = field(default_factory=list)
    addons: list[str] = field(default_factory=list)  # add-on ids made for another version
    linux_packages: list[str] = field(default_factory=list)  # apt packages with an upgrade
    problems: list[str] = field(default_factory=list)  # what couldn't be checked

    @property
    def anything(self) -> bool:
        return bool(self.app or self.tools or self.addons or self.linux_packages)


# --------------------------------------------------------------------------- checking
def _get_json(url: str) -> dict:
    from umc.tools.installer import _get

    with _get(url) as resp:
        return json.load(resp)


def pick_app_asset(release: dict, platform: str = sys.platform) -> AppUpdate | None:
    """The release's download for this computer (None when the release has none)."""
    version = str(release.get("tag_name", "")).lstrip("v")
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        wanted = (
            name == WINDOWS_ASSET
            if platform == "win32"
            else bool(LINUX_ASSET.fullmatch(name))
            if platform.startswith("linux")
            else False
        )
        if wanted:
            digest = str(asset.get("digest") or "")
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return AppUpdate(version, asset["browser_download_url"], sha, name)
    return None


def check_app(current: str = __version__) -> AppUpdate | None:
    release = _get_json(os.environ.get("UMC_UPDATE_URL", RELEASES_API))
    if release.get("draft") or release.get("prerelease"):
        return None
    found = pick_app_asset(release)
    return found if found is not None and is_newer(found.version, current) else None


def check_tools() -> list[ToolUpdate]:
    """Windows: the app's own copies of the tools with a newer version out."""
    from umc.tools import installer

    if sys.platform != "win32":
        return []
    out = []
    for tool in installer.private_tools():
        have = installer.installed_version(tool)
        try:
            latest = installer.latest_version(tool)
        except Exception:
            log.exception("update check: latest %s", tool)
            continue
        if is_newer(latest, have):
            out.append(ToolUpdate(tool, have or "", latest))
    return out


def outdated_addons() -> list[str]:
    """Editors installed for another version of the app (they don't load in this one)."""
    from umc.core import addons

    if addons.from_source():
        return []
    out = []
    for addon_id in addons.ADDONS:
        data = addons.manifest(addon_id)
        if data and data.get("version") != __version__:
            out.append(addon_id)
    return out


def apt_upgradable(packages: tuple[str, ...] = LINUX_TOOL_PACKAGES) -> list[str]:
    """Linux: which of ``packages`` apt would upgrade now (a dry run, no root needed; uses the
    package lists as they are)."""
    apt = shutil.which("apt-get")
    if apt is None:
        return []
    try:
        out = subprocess.run(
            [apt, "-s", "install", "--only-upgrade", *packages],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({m.group(1) for m in re.finditer(r"^Inst (\S+)", out, re.MULTILINE)})


def check_all() -> Report:
    report = Report()
    try:
        report.app = check_app()
    except Exception as exc:
        log.exception("update check: app")
        report.problems.append(
            "Couldn't check for a new version of Universal Edit: "
            + (str(exc) if isinstance(exc, ConversionError) else "no answer from GitHub.")
        )
    report.addons = outdated_addons()
    report.tools = check_tools()
    if sys.platform.startswith("linux"):
        report.linux_packages = apt_upgradable()
    return report


# --------------------------------------------------------------------------- updating
def update_tools(
    updates: list[ToolUpdate], progress: Progress, cancel: threading.Event
) -> list[str]:
    """Reinstall each tool at its newest version (the one-click installers). Returns the
    problems, in plain words; the others still go ahead."""
    from umc.tools import installer

    problems = []
    for n, u in enumerate(updates):
        share = 1 / max(1, len(updates))

        def part(f: float, message: str, n: int = n, share: float = share) -> None:
            progress((n + f) * share, message)

        try:
            installer.INSTALLERS[u.tool](part, cancel)
        except ConversionError as exc:
            log.error("updating %s: %s\n%s", u.tool, exc, exc.details)
            problems.append(f"{u.tool}: {exc}")
    return problems


def update_addons(ids: list[str], progress: Progress, cancel: threading.Event) -> list[str]:
    from umc.core import addons

    problems = []
    for n, addon_id in enumerate(ids):
        title = addons.ADDONS[addon_id].title

        def part(f: float, n: int = n, title: str = title) -> None:
            progress((n + f) / max(1, len(ids)), f"Updating the {title.lower()}…")

        try:
            addons.download(addon_id, part, cancel)
        except ConversionError as exc:
            problems.append(f"{title}: {exc}")
    return problems


def download_app(update: AppUpdate, progress: Progress, cancel: threading.Event) -> Path:
    """Fetch the new version's installer / package into a temporary folder (checked)."""
    from umc.tools.installer import download

    if not update.sha256:
        raise UpdateError(
            "The new version's download has no checksum on GitHub, so it can't be checked. "
            "Download it from the release page instead."
        )
    folder = Path(tempfile.mkdtemp(prefix="umc-update-"))
    return download(
        update.url,
        folder / update.name,
        progress=progress,
        cancel_event=cancel,
        label=f"Universal Edit {update.version}",
        expected_sha256=update.sha256,
    )


def windows_restart_command(setup: Path, app_exe: Path) -> str:
    """Run the installer quietly over this copy, then start the app again. ``start /wait``
    waits for setup — by then this process has quit and its files are free. One string:
    ``cmd /s /c "…"`` takes everything between the outer quotes as it is (a list would get
    Python's backslash quoting, which cmd doesn't understand)."""
    inner = (
        f'start "" /wait "{setup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
        f' & start "" "{app_exe}"'
    )
    return f'cmd /s /c "{inner}"'


def start_windows_update(setup: Path) -> None:
    """Hand over to the installer (detached); the caller quits the app right after."""
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    subprocess.Popen(
        windows_restart_command(setup, Path(sys.executable)),
        creationflags=flags,
        close_fds=True,
    )


def has_apt_source() -> bool:
    """The app's apt repository is set up (so ``apt`` itself knows about new versions)."""
    for p in [Path("/etc/apt/sources.list"), *Path("/etc/apt/sources.list.d").glob("*")]:
        try:
            if "Universal-Edit/releases/download/apt" in p.read_text(errors="replace"):
                return True
        except OSError:
            continue
    return False


def linux_update_script(deb: Path | None, packages: list[str]) -> str:
    """The commands ``pkexec`` runs as root: refresh apt's lists, then the new .deb (or the
    package from the app's apt repository) and the tools — only those already installed."""
    names = " ".join(packages)
    lines = ["set -e", "export DEBIAN_FRONTEND=noninteractive", "apt-get update -q || true"]
    if deb is not None:
        lines.append(f"apt-get install -y -q '{deb}'")
    if names:
        lines.append(f"apt-get install -y -q --only-upgrade {names}")
    return "\n".join(lines)


def linux_command_to_copy(packages: list[str]) -> str:
    return "sudo apt update && sudo apt install --only-upgrade " + " ".join(packages)


def run_linux_update(deb: Path | None, packages: list[str], cancel: threading.Event) -> None:
    """One password prompt (pkexec) for everything. Raises UpdateError in plain words."""
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        raise UpdateError(
            "This computer can't ask for your password from the app. Run this in a terminal: "
            + linux_command_to_copy(packages)
        )
    proc = subprocess.Popen(
        [pkexec, "sh", "-c", linux_update_script(deb, packages)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    while True:
        try:
            out, _ = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                proc.terminate()
    if proc.returncode in (126, 127):  # the password prompt was closed / refused
        raise UpdateError("The update needs your password; nothing was changed.")
    if proc.returncode != 0:
        log.error("apt update failed (%s):\n%s", proc.returncode, (out or "")[-3000:])
        raise UpdateError(
            "apt couldn't finish the update. Try it in a terminal: "
            + linux_command_to_copy(packages)
        )


def linux_packages_for(report: Report) -> list[str]:
    """What apt is asked to upgrade: the app (from its apt repository) and the tools."""
    names = list(report.linux_packages)
    if report.app is not None and has_apt_source():
        names.insert(0, APT_PACKAGE)
    return names


def restart_app() -> None:
    """Start a fresh copy of the app (after an update); the caller then quits."""
    args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "umc"]
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)
