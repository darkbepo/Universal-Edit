"""Linux desktop integration (no Qt): the menu entry and "Open with" / "Convert with".

Linux desktops match files by MIME type, not extension. ``linux_mime.json`` maps the app's
extensions to the types the freedesktop shared-mime-info database gives them; it is made from
``/usr/share/mime/globs2`` with ``python -m umc.tools.linux_desktop --refresh-mime`` and checked
into git, so a build doesn't depend on the machine it runs on.

The .deb installs two entries (``scripts/build_deb.py``):

* ``universal-edit.desktop`` — the menu entry, and "Open with Universal Edit" for every type
  the viewer shows (``--view``; runnable scripts are left alone, like on Windows);
* ``universal-edit-convert.desktop`` — hidden from the menu, it shows up in a file's
  "Open with" list as "Convert with Universal Edit" (``--convert``; code files have no entry).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from umc import __version__
from umc.tools import shell_integration

APP_ID = "universal-edit"  # command, icon and desktop-file name
MIME_FILE = Path(__file__).with_name("linux_mime.json")
GLOBS2 = Path("/usr/share/mime/globs2")


def read_globs2(path: Path = GLOBS2) -> dict[str, str]:
    """{extension: MIME type} from a shared-mime-info ``globs2`` file (highest weight wins;
    only plain ``*.ext`` patterns)."""
    best: dict[str, tuple[int, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 3:
            continue
        weight, mime, pattern = int(parts[0]), parts[1], parts[2]
        if "cs" in parts[3:] or not pattern.startswith("*."):
            continue
        ext = pattern[2:]
        if any(c in ext for c in "*?[]") or ext != ext.lower():
            continue
        if ext not in best or weight > best[ext][0]:
            best[ext] = (weight, mime)
    return {ext: mime for ext, (_, mime) in best.items()}


def known_mime() -> dict[str, str]:
    """The checked-in table: {extension: MIME type} for the app's extensions."""
    return json.loads(MIME_FILE.read_text(encoding="utf-8"))


def mime_types(extensions: list[str], table: dict[str, str] | None = None) -> list[str]:
    """The MIME types of ``extensions`` (sorted, no duplicates; unknown ones are skipped)."""
    table = known_mime() if table is None else table
    return sorted({table[e] for e in extensions if e in table})


def _entry(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def desktop_entry(table: dict[str, str] | None = None) -> str:
    """The menu entry; also "Open with Universal Edit" for every type the viewer shows."""
    types = mime_types(shell_integration.view_extensions(), table)
    return _entry(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.5",
            "Name=Universal Edit",
            "GenericName=File converter and editor",
            "Comment=Convert, view and edit documents, pictures, video, audio and more",
            f"Exec={APP_ID} --view %f",
            f"Icon={APP_ID}",
            "Terminal=false",
            "Categories=Utility;",
            "Keywords=convert;converter;pdf;video;image;audio;editor;viewer;",
            f"MimeType={';'.join(types)};",
            f"X-AppVersion={__version__}",
        ]
    )


def convert_entry(table: dict[str, str] | None = None) -> str:
    """Hidden from the menu: "Convert with Universal Edit" in a file's Open with list."""
    types = mime_types(shell_integration.convert_extensions(), table)
    return _entry(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.5",
            f"Name={shell_integration.MENU_TEXT}",
            "Comment=Open the files in Universal Edit's converter",
            f"Exec={APP_ID} --convert %F",
            f"Icon={APP_ID}",
            "Terminal=false",
            "NoDisplay=true",
            f"MimeType={';'.join(types)};",
        ]
    )


def refresh_mime(globs2: Path = GLOBS2) -> dict[str, str]:
    """Rebuild ``linux_mime.json`` from the system's shared-mime-info (run on purpose)."""
    system = read_globs2(globs2)
    table = {e: system[e] for e in shell_integration.known_extensions() if e in system}
    MIME_FILE.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return table


if __name__ == "__main__":
    if sys.argv[1:] == ["--refresh-mime"]:
        print(f"{len(refresh_mime())} extensions → {MIME_FILE}")
    else:
        sys.exit("usage: python -m umc.tools.linux_desktop --refresh-mime")
