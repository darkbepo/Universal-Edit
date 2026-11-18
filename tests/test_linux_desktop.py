"""Linux menu entry and "Open with" / "Convert with" (desktop files + MIME types)."""

from __future__ import annotations

import configparser

import pytest

from umc.tools import linux_desktop, shell_integration


def _parse(text: str) -> configparser.SectionProxy:
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    cp.optionxform = str  # keys are case-sensitive in desktop files
    cp.read_string(text)
    return cp["Desktop Entry"]


def _types(entry: configparser.SectionProxy) -> list[str]:
    return entry["MimeType"].rstrip(";").split(";")


def test_main_entry_opens_files_in_the_viewer():
    e = _parse(linux_desktop.desktop_entry())
    assert e["Exec"] == "universal-edit --view %f"
    assert e["Icon"] == "universal-edit" and e["Type"] == "Application"
    types = _types(e)
    for mime in ("application/pdf", "image/png", "video/mp4", "model/obj", "video/mp2t"):
        assert mime in types
    assert "font/otf" not in types  # the viewer doesn't show fonts (Convert with does)
    assert "font/otf" in _types(_parse(linux_desktop.convert_entry()))


def test_convert_entry_sorts_after_the_main_one():
    # with no default set, a double-click opens the first entry listed: the viewer
    names = sorted([f"{linux_desktop.CONVERT_ID}.desktop", f"{linux_desktop.APP_ID}.desktop"])
    assert names[0] == "universal-edit.desktop"


def test_runnable_scripts_and_code_get_no_convert_entry():
    view = _types(_parse(linux_desktop.desktop_entry()))
    convert = _parse(linux_desktop.convert_entry())
    assert convert["NoDisplay"] == "true"
    assert convert["Exec"] == "universal-edit --convert %F"
    conv = _types(convert)
    table = linux_desktop.known_mime()
    for ext in ("sh", "bash"):  # double-click runs them: left alone in Open with
        assert not set(table.get(ext, [])) & set(view)
    assert not set(table["py"]) & set(conv)  # nothing to convert code to
    assert not set(table["js"]) & set(conv)
    assert set(table["docx"]) <= set(conv) and set(table["mp4"]) <= set(conv)


def test_table_covers_the_common_formats():
    table = linux_desktop.known_mime()
    for ext in ("pdf", "docx", "png", "jpg", "mp4", "mkv", "mp3", "flac", "svg", "stl", "zip"):
        assert table.get(ext)
    assert set(table) <= set(shell_integration.known_extensions())


def test_ties_keep_the_type_the_app_reads():
    # shared-mime-info gives these extensions two types of the same weight
    table = linux_desktop.known_mime()
    assert table["otf"][0] == "font/otf"  # not the ODF formula template
    assert "application/vnd.oasis.opendocument.formula-template" not in table["otf"]
    assert table["obj"][0] == "model/obj" and "application/x-tgif" not in table["obj"]
    assert table["smi"][0] == "application/x-sami" and "application/smil+xml" not in table["smi"]
    assert table["ts"][0] == "video/mp2t" and "text/vnd.trolltech.linguist" not in table["ts"]
    assert "tsx" not in table  # only Tiled's tileset type: not TypeScript


def test_fitting_by_family():
    assert linux_desktop.fitting("otf", ["application/x-odt-template", "font/otf"]) == ["font/otf"]
    assert linux_desktop.fitting("doc", ["application/msword", "application/vnd.ms-word"]) == [
        "application/msword",
        "application/vnd.ms-word",
    ]  # documents take every type
    assert linux_desktop.fitting("nope-ext", ["a/b"]) == ["a/b"]


def test_mime_types_skip_unknown_and_dedupe():
    table = {"jpg": ["image/jpeg"], "jpeg": ["image/jpeg"], "ogg": ["audio/ogg", "audio/x-v"]}
    assert linux_desktop.mime_types(["jpeg", "jpg", "ogg", "nope"], table) == [
        "audio/ogg",
        "audio/x-v",
        "image/jpeg",
    ]


def test_read_globs2(tmp_path):
    globs = tmp_path / "globs2"
    globs.write_text(
        "# comment\n50:image/jpeg:*.jpg\n60:image/x-better:*.jpg\n50:text/x-c:*.C:cs\n"
        "50:application/x-thing:README\n50:image/png:*.png\n50:font/otf:*.otf\n"
        "50:application/x-other:*.otf\n",
        encoding="utf-8",
    )
    assert linux_desktop.read_globs2(globs) == {
        "jpg": ["image/x-better"],
        "png": ["image/png"],
        "otf": ["font/otf", "application/x-other"],
    }


@pytest.mark.skipif(not linux_desktop.GLOBS2.is_file(), reason="no shared-mime-info here")
def test_table_agrees_with_this_systems_mime_database():
    system = linux_desktop.read_globs2()
    aliases = linux_desktop.read_aliases() if linux_desktop.ALIASES.is_file() else {}
    alias_of = {old: new for new, olds in aliases.items() for old in olds}
    known_here = {t for ts in system.values() for t in ts} | set(aliases) | set(alias_of)
    here_version = (
        linux_desktop.version_tuple(linux_desktop.MIME_VERSION.read_text(encoding="utf-8"))
        if linux_desktop.MIME_VERSION.is_file()
        else ()
    )
    # On a database at least as new as the table's, every name must be known; an older one
    # (Debian 12) hasn't heard of some newer names yet — those are skipped, a desktop file
    # listing an unknown type does nothing. A name it does know must still fit the extension.
    strict = here_version >= linux_desktop.table_source()
    for ext, types in linux_desktop.known_mime().items():
        if ext not in system:
            continue
        here = set(system[ext])
        assert here & set(types), ext  # whatever this system calls it is among them
        allowed = here | set(linux_desktop.LEGACY_TYPES.get(ext, []))
        for mime in here:
            allowed |= set(aliases.get(mime, []))
            allowed.add(alias_of.get(mime, mime))
        allowed |= {old for new in set(allowed) for old in aliases.get(new, [])}
        checked = set(types) if strict else set(types) & known_here
        assert checked <= allowed, (ext, sorted(checked - allowed))


def test_table_records_its_source():
    assert linux_desktop.table_source() >= (2, 4)
    assert not any(ext.startswith("_") for ext in linux_desktop.known_mime())


def test_old_names_come_along(tmp_path):
    aliases = tmp_path / "aliases"
    aliases.write_text(
        "video/x-msvideo video/vnd.avi\nvideo/avi video/vnd.avi\n"
        "zz-application/zz-winassoc-doc application/msword\n",
        encoding="utf-8",
    )
    assert linux_desktop.read_aliases(aliases) == {
        "video/vnd.avi": ["video/avi", "video/x-msvideo"]
    }
    assert "video/x-msvideo" in linux_desktop.known_mime()["avi"]
