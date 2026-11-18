"""Linux desktop integration (no Qt): the menu entry and "Open with" / "Convert with".

Linux desktops match files by MIME type, not extension. ``linux_mime.json`` maps the app's
extensions to the types the freedesktop shared-mime-info database gives them; it is made from
``/usr/share/mime/globs2`` with ``python -m umc.tools.linux_desktop --refresh-mime`` and checked
into git, so a build doesn't depend on the machine it runs on. An extension can have several
types of the same weight (``.otf`` = font or OpenDocument formula template): for pictures,
video, audio, fonts, 3D models, subtitles and code only the types of that kind are kept
(``KIND_TYPES``), so the app never claims files it doesn't read.

The .deb installs two entries (``scripts/build_deb.py``):

* ``universal-edit.desktop`` — the menu entry, and "Open with Universal Edit" for every type
  the viewer shows (``--view``; runnable scripts are left alone, like on Windows);
* ``universal-edit_convert.desktop`` — hidden from the menu, it shows up in a file's
  "Open with" list as "Convert with Universal Edit" (``--convert``; code files have no entry).
  The "_" makes it sort after the main entry: where no default is set, a double-click opens
  the first app listed for the type, which must be the viewer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from umc import __version__
from umc.tools import shell_integration

APP_ID = "universal-edit"  # command, icon and desktop-file name
CONVERT_ID = f"{APP_ID}_convert"  # sorts after APP_ID ("." < "_")
MIME_FILE = Path(__file__).with_name("linux_mime.json")
GLOBS2 = Path("/usr/share/mime/globs2")
ALIASES = Path("/usr/share/mime/aliases")
_MEDIA_TOPS = {"application", "audio", "font", "image", "model", "text", "video"}
# Old names the aliases file can't give: shared-mime-info before 2.3 (Debian 12) typed .tbz as
# x-bzip-compressed-tar, which newer ones made an alias of the .tbz2 type instead.
LEGACY_TYPES = {"tbz": ["application/x-bzip-compressed-tar"]}

# The kinds of type a family's files may have (detect.py's *_EXTS sets); other families
# (documents, data, archives, CAD, vector …) take every type of the top weight.
KIND_TYPES: dict[str, tuple[str, ...]] = {
    "IMAGE_EXTS": ("image/",),
    "VIDEO_EXTS": ("video/", "application/mxf", "application/vnd.ms-asf",
                   "application/vnd.rn-realmedia"),
    "AUDIO_EXTS": ("audio/",),
    "FONT_EXTS": ("font/", "application/x-font"),
    "MODEL_EXTS": ("model/",),
    "SUBTITLE_EXTS": ("text/", "application/x-subrip", "application/x-sami", "application/ttml"),
    # not "text/vnd.…": .ts is also Qt Linguist's text/vnd.trolltech.linguist
    "CODE_EXTS": ("text/x-", "text/css", "text/javascript", "text/rust", "text/plain",
                  "application/javascript", "application/json", "application/xml",
                  "application/x-shellscript", "application/x-php", "application/x-perl",
                  "application/x-ruby", "application/sql", "application/toml",
                  "application/yaml", "application/x-yaml", "application/x-ipynb"),
}  # fmt: skip


def _families(ext: str) -> set[str]:
    from umc.core import detect

    return {
        name
        for name in dir(detect)
        if name.endswith("_EXTS")
        and not name.startswith("_")  # _OTHER_EXTS is a catch-all, not a family
        and isinstance(getattr(detect, name), set | frozenset)
        and ext in getattr(detect, name)
    }


def fitting(ext: str, types: list[str]) -> list[str]:
    """The ``types`` of the right kind for ``ext``'s families (all of them when one of its
    families takes any type)."""
    families = _families(ext)
    if not families or any(f not in KIND_TYPES for f in families):
        return types
    kinds = tuple(k for f in families for k in KIND_TYPES[f])
    return [t for t in types if t.startswith(kinds)]


def read_globs2(path: Path = GLOBS2) -> dict[str, list[str]]:
    """{extension: MIME types} from a shared-mime-info ``globs2`` file: every type with the
    highest weight for it (only plain ``*.ext`` patterns), in the file's order."""
    best: dict[str, tuple[int, list[str]]] = {}
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
            best[ext] = (weight, [mime])
        elif weight == best[ext][0] and mime not in best[ext][1]:
            best[ext][1].append(mime)
    return {ext: types for ext, (_, types) in best.items()}


def read_aliases(path: Path = ALIASES) -> dict[str, list[str]]:
    """{type: its older names} from a shared-mime-info ``aliases`` file ("alias type" lines).
    Older systems still call some types by those (Debian 12: AVI is video/x-msvideo, newer
    ones video/vnd.avi), so the desktop files list them too."""
    names: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        # only real media types ("zz-application/…", "flv-application/…" are old hacks)
        if len(parts) == 2 and parts[0].split("/")[0] in _MEDIA_TOPS:
            names.setdefault(parts[1], []).append(parts[0])
    return {mime: sorted(old) for mime, old in names.items()}


def known_mime() -> dict[str, list[str]]:
    """The checked-in table: {extension: MIME types} for the app's extensions."""
    return json.loads(MIME_FILE.read_text(encoding="utf-8"))


def mime_types(extensions: list[str], table: dict[str, list[str]] | None = None) -> list[str]:
    """The MIME types of ``extensions`` (sorted, no duplicates; unknown ones are skipped)."""
    table = known_mime() if table is None else table
    return sorted({t for e in extensions for t in table.get(e, ())})


def _entry(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def desktop_entry(table: dict[str, list[str]] | None = None) -> str:
    """The menu entry; also "Open with Universal Edit" for every type the viewer shows."""
    types = mime_types(shell_integration.view_extensions(), table)
    return _entry(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.4",  # the newest Debian 12's desktop-file-validate knows
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


def convert_entry(table: dict[str, list[str]] | None = None) -> str:
    """Hidden from the menu: "Convert with Universal Edit" in a file's Open with list."""
    types = mime_types(shell_integration.convert_extensions(), table)
    return _entry(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.4",  # the newest Debian 12's desktop-file-validate knows
            f"Name={shell_integration.MENU_TEXT}",
            "Comment=Open the files in Universal Edit's converter",
            f"Exec={APP_ID} --convert %F",
            f"Icon={APP_ID}",
            "Terminal=false",
            "NoDisplay=true",
            f"MimeType={';'.join(types)};",
        ]
    )


def refresh_mime(globs2: Path = GLOBS2, aliases: Path = ALIASES) -> dict[str, list[str]]:
    """Rebuild ``linux_mime.json`` from the system's shared-mime-info (run on purpose): each
    extension's fitting types first, then their older names."""
    system = read_globs2(globs2)
    old_names = read_aliases(aliases) if aliases.is_file() else {}
    table = {}
    for ext in shell_integration.known_extensions():
        types = fitting(ext, system.get(ext, []))
        for mime in list(types):
            types += [a for a in old_names.get(mime, []) if a not in types]
        if types:
            types += [m for m in LEGACY_TYPES.get(ext, []) if m not in types]
        if types:
            table[ext] = types
    MIME_FILE.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return table


if __name__ == "__main__":
    if sys.argv[1:] == ["--refresh-mime"]:
        print(f"{len(refresh_mime())} extensions → {MIME_FILE}")
    else:
        sys.exit("usage: python -m umc.tools.linux_desktop --refresh-mime")
