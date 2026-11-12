"""Properties mode: the "About this file" facts (both apps share the builder cases) and the GUI."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from umc.core.detect import detect
from umc.properties import format as fmt
from umc.properties.info import build
from umc.properties.read import describe, iso6709, media_facts, pdf_date

FIXTURES = Path(__file__).parent / "fixtures"


def rows(sections) -> dict[str, dict[str, str]]:
    return {s.title: dict(s.rows) for s in sections}


# ----------------------------------------------------------------------------- shared cases
def test_format_cases_match_the_shared_file():
    cases = json.loads((FIXTURES / "properties_format_cases.json").read_text(encoding="utf-8"))
    for name, items in cases.items():
        fn = getattr(fmt, name)
        for *args, expected in items:
            assert fn(*args) == expected, (name, args)


def test_builder_cases_match_the_shared_file():
    cases = json.loads((FIXTURES / "properties_cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 10
    for case in cases:
        got = [s.to_json() for s in build(case["facts"])]
        assert got == case["sections"], case["name"]


def test_dates():
    assert pdf_date("D:20261112140300") == "2026-11-12 14:03"  # no zone: as written
    assert pdf_date("nonsense") is None
    local = pdf_date("D:20261112140300+01'00'")
    assert local is not None and local.startswith("2026-11-1")
    assert iso6709("+55.6761+012.5683/") == (55.6761, 12.5683)
    assert iso6709("-33.86+151.21+010.000/") == (-33.86, 151.21)


def test_media_facts_from_ffprobe_json():
    data = {
        "format": {"duration": "10.0", "tags": {"TITLE": "Song", "Album_Artist": "Band"}},
        "streams": [
            {"codec_type": "audio", "codec_name": "flac", "channels": 2, "sample_rate": "96000",
             "tags": {"ARTIST": "Band", "DATE": "2001"}},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ],
    }  # fmt: skip
    m = media_facts(data, 1_000_000)
    assert "video" not in m  # the cover picture isn't a video
    assert m["cover"] is True
    assert m["tags"]["title"] == "Song"
    assert m["tags"]["artist"] == "Band"  # Ogg / FLAC keep tags on the stream
    assert m["tags"]["album_artist"] == "Band"
    r = rows(build({"media": m}))
    assert r["Audio"]["Codec"] == "FLAC"
    assert r["Audio"]["Sample rate"] == "96 kHz"
    assert r["Tags"]["Year"] == "2001"


# ----------------------------------------------------------------------------- real files
def test_photo(tmp_path):
    from umc.core.samples import write_test_photo

    p = write_test_photo(tmp_path / "photo.jpg")
    r = rows(describe(detect(p)))
    assert r["File"]["Name"] == "photo.jpg"
    assert r["File"]["Type"] == "JPG image"
    assert r["File"]["Folder"] == str(tmp_path)
    assert r["Picture"]["Dimensions"] == "64 × 48 px"
    assert r["Picture"]["Resolution"] == "300 dpi"
    assert r["Picture"]["Orientation"] == "Turned 90° right"
    assert r["Camera"] == {
        "Camera": "Canon EOS R6",
        "Lens": "RF 50mm F1.8 STM",
        "Taken": "2026-11-12 14:03",
        "Exposure": "1/120 s",
        "Aperture": "f/1.8",
        "ISO": "200",
        "Focal length": "50 mm",
        "Flash": "Didn't fire",
    }
    assert r["Details"]["Author"] == "Ada Lovelace"
    assert r["Location"] == {"Coordinates": "55.67610, 12.56997", "Altitude": "12 m"}


def test_plain_pictures(media):
    r = rows(describe(detect(media / "img0.png")))
    assert r["Picture"]["Colour"] == "Colour, with transparency"
    assert "Camera" not in r and "Location" not in r
    assert rows(describe(detect(media / "anim.gif")))["Picture"]["Frames"] == "3 (animated)"


def test_pdf(tmp_path):
    import pymupdf

    p = tmp_path / "report.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=595, height=842)
        doc.new_page()
        doc.set_metadata({"title": "Report", "author": "Ada", "creator": "Writer"})
        doc.save(p)
    r = rows(describe(detect(p)))
    assert r["Document"]["Pages"] == "2"
    assert r["Document"]["Page size"] == "A4 (210 × 297 mm)"
    assert r["Document"]["Title"] == "Report"
    assert r["Document"]["Author"] == "Ada"
    assert r["Document"]["Made with"] == "Writer"
    assert r["Document"]["Version"].startswith("PDF 1.")


def _zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in parts.items():
            zf.writestr(name, text)
    return path


def test_word_document(tmp_path):
    core = (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>Letter</dc:title><dc:creator>Ada</dc:creator>"
        "<cp:lastModifiedBy>Bob</cp:lastModifiedBy>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-11-01T09:00:00Z</dcterms:created>'
        "</cp:coreProperties>"
    )
    app = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'extended-properties"><Pages>3</Pages><Words>1234</Words><Company>Acme</Company>'
        "<Application>Microsoft Office Word</Application></Properties>"
    )
    p = _zip(
        tmp_path / "letter.docx",
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:document/>",
            "docProps/core.xml": core,
            "docProps/app.xml": app,
        },
    )
    d = rows(describe(detect(p)))["Document"]
    assert d["Title"] == "Letter"
    assert d["Author"] == "Ada"
    assert d["Last saved by"] == "Bob"
    assert d["Pages"] == "3"
    assert d["Words"] == "1,234"
    assert d["Company"] == "Acme"
    assert d["Created"].startswith("2026-11-0")  # shown in local time


def test_odt(tmp_path):
    meta = (
        '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
        "<dc:title>Notes</dc:title><meta:initial-creator>Ada</meta:initial-creator>"
        "<meta:generator>LibreOffice/7.6</meta:generator>"
        '<meta:document-statistic meta:page-count="2" meta:word-count="40"/>'
        "</office:meta></office:document-meta>"
    )
    p = _zip(
        tmp_path / "notes.odt",
        {"mimetype": "application/vnd.oasis.opendocument.text", "meta.xml": meta},
    )
    d = rows(describe(detect(p)))["Document"]
    assert d == {
        "Pages": "2",
        "Words": "40",
        "Title": "Notes",
        "Author": "Ada",
        "Made with": "LibreOffice",
    }


def test_font(tmp_path):
    from umc.core.samples import write_test_font

    p = write_test_font(tmp_path / "sample.ttf")
    f = rows(describe(detect(p)))["Font"]
    assert f["Family"]
    assert f["Outlines"] == "TrueType"
    assert int(f["Glyphs"]) >= 1


def test_model(tmp_path):
    import trimesh

    p = tmp_path / "box.stl"
    trimesh.creation.box((10, 20, 30)).export(p)
    m = rows(describe(detect(p)))["3D model"]
    assert m["Width (X)"] == "10.0 mm"
    assert m["Watertight"] == "Yes"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_and_music(tmp_path):
    def ff(*args: str) -> None:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)

    video = tmp_path / "clip.mp4"
    ff("-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30", "-f", "lavfi", "-i",
       "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", "-metadata", "title=Holiday",
       "-metadata", "location=+55.6761+012.5683/", str(video))  # fmt: skip
    r = rows(describe(detect(video)))
    assert r["Video"]["Dimensions"] == "320 × 240 px"
    assert r["Video"]["Frame rate"] == "30 fps"
    assert r["Video"]["Codec"] == "H.264"
    assert r["Audio"]["Codec"] == "AAC"
    assert r["Tags"]["Title"] == "Holiday"
    assert r["Location"]["Coordinates"] == "55.67610, 12.56830"

    song = tmp_path / "song.mp3"
    ff("-f", "lavfi", "-i", "sine=duration=3", "-ac", "2", "-metadata", "title=Song",
       "-metadata", "artist=Band", "-metadata", "date=2019-05-01", str(song))  # fmt: skip
    r = rows(describe(detect(song)))
    assert r["Audio"]["Length"] == "3 s"
    assert r["Audio"]["Channels"] == "Stereo"
    assert r["Tags"] == {"Title": "Song", "Artist": "Band", "Year": "2019"}


def test_unreadable_file_still_shows_the_file(tmp_path):
    p = tmp_path / "broken.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    r = rows(describe(detect(p)))
    assert r["File"]["Name"] == "broken.png"
    assert r["Note"] == {"": "Some details of this file couldn't be read."}


def test_kinds_without_details_show_the_file(tmp_path):
    p = tmp_path / "subs.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    assert [s.title for s in describe(detect(p))] == ["File"]


# ----------------------------------------------------------------------------- GUI
@pytest.fixture
def window(qtbot):
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w  # tests end with _settle(qtbot, w): qtbot deletes the window before teardown


def _settle(qtbot, w) -> None:
    """Thumbnails and the panel's readers finished (before qtbot deletes the window)."""
    qtbot.waitUntil(lambda: not w.file_list.thumbnails_pending(), timeout=10000)
    w.properties_panel.tasks.wait(5000)


def _wait_rows(qtbot, w, title: str) -> dict[str, str]:
    qtbot.waitUntil(lambda: title in w.properties_panel.rows(), timeout=10000)
    return w.properties_panel.rows()[title]


def test_properties_switch(qtbot, window, tmp_path, media):
    from umc.core.samples import write_test_photo

    w = window
    assert not w.properties_mode.isEnabled()  # no files yet
    photo = write_test_photo(tmp_path / "photo.jpg")
    w.add_files([str(photo), str(media / "img0.png")])
    assert w.properties_mode.isEnabled()
    w.properties_mode.click()
    assert w.mode == "properties"
    assert w.properties_mode.isChecked()
    assert w.right.currentWidget() is w.properties_panel
    assert w.convert_button.isHidden()
    assert w.dest_button.isHidden()
    assert _wait_rows(qtbot, w, "Camera")["Camera"] == "Canon EOS R6"
    # several files: a picker on top chooses which one is shown
    panel = w.properties_panel
    assert not panel.picker.isHidden()
    assert panel.picker.count() == 2
    panel.picker.setCurrentIndex(1)
    qtbot.waitUntil(lambda: panel.rows().get("File", {}).get("Name") == "img0.png", timeout=10000)
    assert "Camera" not in panel.rows()
    # a file removed: the one shown stays if it's still there
    w.remove_row(0)
    qtbot.waitUntil(lambda: panel.picker.isHidden())
    assert panel.rows()["File"]["Name"] == "img0.png"
    # back to converting
    w.convert_mode.click()
    assert w.mode == "convert"
    assert w.right.currentWidget() is w.tabs
    assert not w.convert_button.isHidden()
    _settle(qtbot, w)


def test_clearing_files_leaves_properties(qtbot, window, media):
    w = window
    w.add_files([str(media / "tall.jpg")])
    w.enter_properties()
    assert w.mode == "properties"
    _wait_rows(qtbot, w, "Picture")
    w.clear_files()
    assert w.mode == "convert"
    assert w.convert_mode.isChecked()
    assert not w.properties_mode.isEnabled()
    _settle(qtbot, w)


def test_viewer_properties_button(qtbot, media):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.launcher import AppController

    app = AppController(listen=False)
    app.view(media / "tall.jpg")
    qtbot.addWidget(app.viewer)
    app.viewer.properties_button.click()
    assert not app.viewer.isVisible()
    w = app.windows[-1]
    qtbot.addWidget(w)
    assert w.mode == "properties"
    assert _wait_rows(qtbot, w, "Picture")["Dimensions"] == "100 × 200 px"
    for win in app.windows:
        _settle(qtbot, win)
        win.close()
