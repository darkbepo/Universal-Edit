"""Package the Linux PyInstaller build (dist/UniversalEdit) as a Debian / Ubuntu .deb.

    pyinstaller --noconfirm packaging/umc.spec      # on Linux: every editor inside
    python scripts/build_deb.py                     # → dist/universal-edit_<version>_amd64.deb

Layout: the app in /opt/universal-edit, the command /usr/bin/universal-edit (a link), the menu
entry and "Open with" / "Convert with" desktop files (umc.tools.linux_desktop), the icon in
the hicolor theme. FFmpeg and the system libraries Qt needs come from the distribution
(Depends); Pandoc, LibreOffice, Ghostscript and bsdtar are recommended, Calibre suggested.
Built with dpkg-deb only (no extra tools).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from umc import __version__  # noqa: E402
from umc.tools import linux_desktop  # noqa: E402

PACKAGE = linux_desktop.APP_ID
INSTALL_DIR = f"/opt/{PACKAGE}"
EXE = "UniversalEdit"

# What the bundle loads from the system (checked with ldd + dpkg -S on Ubuntu), plus FFmpeg.
# "t64" names are Debian 13 / Ubuntu 24.04; the alternatives cover Debian 12 / Ubuntu 22.04.
DEPENDS = [
    "ffmpeg",
    "libc6 (>= 2.35)",
    "libstdc++6",
    "libegl1",
    "libgl1",
    "libfontconfig1",
    "libfreetype6",
    "libdbus-1-3",
    "libglib2.0-0t64 | libglib2.0-0",
    "libxkbcommon0",
    "libxkbcommon-x11-0",
    "libx11-6",
    "libx11-xcb1",
    "libxcb1",
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-randr0",
    "libxcb-render-util0",
    "libxcb-shape0",
    "libxcb-xkb1",
    "libwayland-client0",
    "libwayland-cursor0",
    "libwayland-egl1",
    "libpulse0",
]
RECOMMENDS = ["pandoc", "libreoffice", "ghostscript", "libarchive-tools", "xdg-utils"]
SUGGESTS = ["calibre"]

DESCRIPTION = """\
Convert, view and edit documents, pictures, video, audio and more
 Universal Edit converts between document, image, video, audio, data, 3D, font,
 vector, CAD, subtitle and archive formats, with sensible defaults and many
 settings. It also has editors for pictures, documents, audio, video, code and
 3D models, a file viewer and a properties panel.
 .
 Everything runs on your computer. LibreOffice, Pandoc and Ghostscript are used
 when installed for the formats that need them."""

# Menus and file managers pick up new desktop files / icons through these caches.
POSTINST = """\
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
"""
POSTRM = POSTINST


def control(installed_kb: int, arch: str) -> str:
    return (
        f"Package: {PACKAGE}\n"
        f"Version: {__version__}\n"
        f"Architecture: {arch}\n"
        "Maintainer: darkbepo <13454055+darkbepo@users.noreply.github.com>\n"
        f"Installed-Size: {installed_kb}\n"
        f"Depends: {', '.join(DEPENDS)}\n"
        f"Recommends: {', '.join(RECOMMENDS)}\n"
        f"Suggests: {', '.join(SUGGESTS)}\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Homepage: https://github.com/darkbepo/Universal-Edit\n"
        f"Description: {DESCRIPTION}\n"
    )


COPYRIGHT = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Universal Edit
Source: https://github.com/darkbepo/Universal-Edit

Files: *
Copyright: 2026 darkbepo
License: AGPL-3.0-or-later
 The full licence text is in /opt/universal-edit/_internal/LICENSE.
"""


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _size_kb(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) // 1024 + 1


def stage(app: Path, root: Path, arch: str) -> None:
    """Lay the package out under ``root`` (the future /)."""
    if not (app / EXE).is_file():
        raise SystemExit(f"{app / EXE} not found: run pyinstaller packaging/umc.spec first")
    if not (app / "_internal" / "umc" / "editors_bundled").is_file():
        raise SystemExit("this build has no editors_bundled marker (build it on Linux)")
    opt = root / INSTALL_DIR.lstrip("/")
    shutil.copytree(app, opt, symlinks=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / PACKAGE).symlink_to(f"{INSTALL_DIR}/{EXE}")
    apps = root / "usr" / "share" / "applications"
    _write(apps / f"{PACKAGE}.desktop", linux_desktop.desktop_entry())
    _write(apps / f"{PACKAGE}-convert.desktop", linux_desktop.convert_entry())
    icons = root / "usr" / "share" / "icons" / "hicolor"
    svg = icons / "scalable" / "apps" / f"{PACKAGE}.svg"
    svg.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "src" / "umc" / "gui" / "logo.svg", svg)
    _png_icons(icons)
    _write(root / "usr" / "share" / "doc" / PACKAGE / "copyright", COPYRIGHT)
    debian = root / "DEBIAN"
    _write(debian / "control", control(_size_kb(root), arch))
    _write(debian / "postinst", POSTINST, 0o755)
    _write(debian / "postrm", POSTRM, 0o755)


def _png_icons(icons: Path) -> None:
    from PIL import Image

    with Image.open(ROOT / "packaging" / "icon.png") as big:
        for size in (48, 128, 256, 512):
            out = icons / f"{size}x{size}" / "apps" / f"{PACKAGE}.png"
            out.parent.mkdir(parents=True)
            big.resize((size, size), Image.Resampling.LANCZOS).save(out)


def build(app: Path, out_dir: Path, arch: str = "amd64") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    deb = out_dir / f"{PACKAGE}_{__version__}_{arch}.deb"
    work = out_dir / "deb-root"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    try:
        stage(app, work, arch)
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(work), str(deb)],
            check=True,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return deb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", type=Path, default=ROOT / "dist" / "UniversalEdit")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--arch", default="amd64")
    args = parser.parse_args()
    print(build(args.app, args.out, args.arch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
