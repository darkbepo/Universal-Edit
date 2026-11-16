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


def test_main_entry_opens_files_in_the_viewer():
    e = _parse(linux_desktop.desktop_entry())
    assert e["Exec"] == "universal-edit --view %f"
    assert e["Icon"] == "universal-edit" and e["Type"] == "Application"
    types = e["MimeType"].rstrip(";").split(";")
    assert "application/pdf" in types and "image/png" in types and "video/mp4" in types


def test_runnable_scripts_and_code_get_no_convert_entry():
    view = _parse(linux_desktop.desktop_entry())["MimeType"].split(";")
    convert = _parse(linux_desktop.convert_entry())
    assert convert["NoDisplay"] == "true"
    assert convert["Exec"] == "universal-edit --convert %F"
    conv = convert["MimeType"].split(";")
    table = linux_desktop.known_mime()
    for ext in ("sh", "bash", "py"):  # double-click runs or edits them: left alone
        if ext in table:
            assert table[ext] not in view
    assert table["py"] not in conv and table["js"] not in conv  # nothing to convert code to
    assert table["docx"] in conv and table["mp4"] in conv


def test_table_covers_the_common_formats():
    table = linux_desktop.known_mime()
    for ext in ("pdf", "docx", "png", "jpg", "mp4", "mkv", "mp3", "flac", "svg", "stl", "zip"):
        assert ext in table
    assert set(table) <= set(shell_integration.known_extensions())


def test_mime_types_skip_unknown_and_dedupe():
    table = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
    assert linux_desktop.mime_types(["jpeg", "jpg", "png", "nope"], table) == [
        "image/jpeg",
        "image/png",
    ]


def test_read_globs2(tmp_path):
    globs = tmp_path / "globs2"
    globs.write_text(
        "# comment\n50:image/jpeg:*.jpg\n60:image/x-better:*.jpg\n50:text/x-c:*.C:cs\n"
        "50:application/x-thing:README\n50:image/png:*.png\n",
        encoding="utf-8",
    )
    assert linux_desktop.read_globs2(globs) == {"jpg": "image/x-better", "png": "image/png"}


@pytest.mark.skipif(not linux_desktop.GLOBS2.is_file(), reason="no shared-mime-info here")
def test_table_agrees_with_this_systems_mime_database():
    system = linux_desktop.read_globs2()
    for ext, mime in linux_desktop.known_mime().items():
        if ext in system:
            assert system[ext] == mime, ext
