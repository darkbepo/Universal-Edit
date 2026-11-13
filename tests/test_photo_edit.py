"""Changing photo details (Properties step 2): EXIF / XMP inside JPEG, PNG, WebP, losslessly."""

from __future__ import annotations

import io
import json
import struct
from dataclasses import asdict
from pathlib import Path

import pytest
from PIL import Image

from umc.core.samples import write_test_photo
from umc.properties import containers, exif, photo, xmp

CASES = Path(__file__).parent / "fixtures" / "photo_edit_cases.json"


def pixels(data: bytes) -> list:
    return list(Image.open(io.BytesIO(data)).convert("RGBA").get_flattened_data())


def test_shared_cases():
    """The bytes the desktop writes — the web port must write the same (web/tests/photo.test.ts)."""
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) >= 30
    for case in cases:
        data = bytes.fromhex(case["input"])
        if "edit" in case:
            out = photo.apply(data, photo.PhotoEdit(**case["edit"]))
            assert out.hex() == case["output"], case["name"]
            assert pixels(out) == pixels(data), case["name"]  # the picture is untouched
            data = out
        assert asdict(photo.read_details(data)) == case["details"], case["name"]


def test_old_values_are_wiped_not_just_hidden(tmp_path):
    data = write_test_photo(tmp_path / "a.jpg").read_bytes()
    gps_lat = struct.pack("<II", 3396, 100)  # 33.96″ of the sample's latitude
    assert b"Ada Lovelace" in data and gps_lat in data
    out = photo.apply(data, photo.PhotoEdit({"author": "Grace"}, remove_location=True))
    assert b"Ada Lovelace" not in out
    assert "Ada".encode("utf-16-le") not in out
    assert gps_lat not in out
    assert b"Canon EOS R6" in out  # untouched values stay
    gone = photo.apply(data, photo.PhotoEdit(remove_all=True))
    assert b"Canon" not in gone and gps_lat not in gone
    assert photo.read_details(gone).orientation == 6  # still shown the right way up


def test_pillow_reads_what_we_wrote(tmp_path):
    data = write_test_photo(tmp_path / "a.jpg").read_bytes()
    out = photo.apply(data, photo.PhotoEdit({"title": "Pier", "taken": "2024-02-29 23:59"}))
    ex = Image.open(io.BytesIO(out)).getexif()
    assert ex[0x010E] == "Pier"
    assert ex.get_ifd(0x8769)[0x9003] == "2024:02:29 23:59:00"
    assert ex[0x9C9B].decode("utf-16-le").rstrip("\x00") == "Pier"


def test_dates_are_checked():
    assert photo.exif_date("2026-11-12 14:03") == "2026:11:12 14:03:00"
    assert photo.exif_date("2026-1-2") == "2026:01:02 00:00:00"
    for bad in ("12/11/2026", "2026-02-30 10:00", "2026-13-01", "2026-11-12 25:00"):
        with pytest.raises(photo.PhotoError):
            photo.exif_date(bad)


def test_tags_are_tidied():
    assert photo.tags_text(" a, b;;c , ") == "a; b; c"


def test_strip_removes_every_hidden_place():
    b = io.BytesIO()
    img = Image.new("RGB", (4, 4))
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Author", "Someone")
    info.add_itxt("Comment", "Private")
    img.save(b, "PNG", pnginfo=info, icc_profile=b"\x00" * 128)
    data = b.getvalue()
    out = photo.apply(data, photo.PhotoEdit(remove_all=True))
    assert b"Someone" not in out and b"Private" not in out
    assert b"iCCP" in out  # the colour profile stays
    # JPEG comments and IPTC blocks go too
    jb = io.BytesIO()
    Image.new("RGB", (4, 4)).save(jb, "JPEG", comment=b"secret note")
    jpeg = jb.getvalue()
    assert b"secret note" in jpeg
    assert b"secret note" not in photo.apply(jpeg, photo.PhotoEdit(remove_all=True))


def test_xmp_properties_are_removed():
    packet = (
        '<rdf:Description dc:x="1" tiff:Orientation="6" exif:GPSLatitude="1" exif:GPSLongitude="2">'
        "<dc:title><rdf:Alt><rdf:li>T &amp; U</rdf:li></rdf:Alt></dc:title><dc:creator/>"
        "</rdf:Description>"
    )
    assert xmp.get(packet, "dc:title") == "T & U"
    assert xmp.get(packet, "tiff:Orientation") == "6"
    out = xmp.remove(packet, ["dc:title", "dc:creator", "tiff:Orientation", "exif:GPS*"])
    assert out == '<rdf:Description dc:x="1"></rdf:Description>'


def test_damaged_exif_is_refused_unless_removing_everything():
    b = io.BytesIO()
    Image.new("RGB", (4, 4)).save(b, "JPEG")
    data = containers.put(b.getvalue(), None)
    broken = data[:2] + b"\xff\xe1\x00\x0aExif\x00\x00II" + data[2:]
    with pytest.raises(photo.PhotoError, match="damaged"):
        photo.apply(broken, photo.PhotoEdit({"title": "x"}))
    out = photo.apply(broken, photo.PhotoEdit({"title": "Fresh"}, remove_all=True))
    assert photo.read_details(out).title == "Fresh"
    Image.open(io.BytesIO(out)).load()


def test_not_a_picture_we_change():
    with pytest.raises(photo.PhotoError):
        photo.apply(b"GIF89a....", photo.PhotoEdit({"title": "x"}))
    assert photo.can_change("JPG") and not photo.can_change("heic")


def test_too_much_for_a_jpeg(tmp_path):
    data = write_test_photo(tmp_path / "a.jpg").read_bytes()
    with pytest.raises(photo.PhotoError):
        photo.apply(data, photo.PhotoEdit({"comment": "x" * 40000}))


def test_rewrite_keeps_maker_note_offsets(tmp_path):
    """Unchanged values stay at their old place (maker notes point into the block)."""
    raw = containers.get_exif(write_test_photo(tmp_path / "a.jpg").read_bytes())
    before = exif.Tiff(raw)
    model = before.ifd0.get(exif.TAG["model"])
    after = exif.Tiff(exif.rewrite(raw, {exif.TAG["artist"]: exif.ascii_value("Z")}))
    assert after.ifd0.get(exif.TAG["model"]).at == model.at
    assert after.text(after.ifd0, exif.TAG["artist"]) == "Z"


# ----------------------------------------------------------------------------- GUI
@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    monkeypatch.setenv("UMC_REDUCE_MOTION", "1")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _settle(qtbot, w) -> None:
    qtbot.waitUntil(lambda: not w.file_list.thumbnails_pending(), timeout=10000)
    w.properties_panel.tasks.wait(5000)


def _form(qtbot, w):
    panel = w.properties_panel
    qtbot.waitUntil(lambda: not panel.form.isHidden() and "File" in panel.rows(), timeout=10000)
    return panel.form


def _wait_saved(qtbot, w):
    panel = w.properties_panel

    def done() -> bool:
        note = panel.save_message()
        return not panel.form.save_button.isEnabled() and "Sav" in note and "Saving" not in note

    qtbot.waitUntil(done, timeout=10000)


def test_form_saves_a_copy(qtbot, window, tmp_path):
    p = write_test_photo(tmp_path / "phone.jpg")
    before = p.read_bytes()
    window.add_files([str(p)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert form.edits["title"].text() == "Harbour at dusk"
    assert form.edits["taken"].text() == "2026-11-12 14:03"
    assert form.rotation.currentData() == 6
    assert form.remove_location.isEnabled()
    assert not form.save_button.isEnabled()  # nothing changed yet
    form.edits["title"].setText("Pier")
    form.edits["title"].textEdited.emit("Pier")
    assert form.save_button.isEnabled()
    form.copy_button.click()
    _wait_saved(qtbot, window)
    copy = tmp_path / "phone (1).jpg"
    assert copy.exists() and p.read_bytes() == before
    assert photo.read_details(copy.read_bytes()).title == "Pier"
    assert "phone (1).jpg" in window.properties_panel.save_message()
    _settle(qtbot, window)


def test_form_changes_the_original(qtbot, window, tmp_path, monkeypatch):
    import os

    p = write_test_photo(tmp_path / "phone.jpg")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    asked = []
    monkeypatch.setattr(
        type(window.properties_panel), "confirm_original", lambda self, n: asked.append(n) or True
    )
    window.add_files([str(p)])
    window.enter_properties()
    form = _form(qtbot, window)
    form.edits["author"].setText("Grace Hopper")
    form.edits["author"].textEdited.emit("Grace Hopper")
    form.rotation.setCurrentIndex(form.rotation.findData(1))
    form.remove_location.setChecked(True)
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert asked == [1]
    d = photo.read_details(p.read_bytes())
    assert (d.author, d.orientation, d.has_location) == ("Grace Hopper", 1, False)
    assert int(p.stat().st_mtime) == 1_600_000_000  # the modified date is kept
    assert not list(tmp_path.glob(".*umc-saving"))
    # "About this file" shows what's in the file now
    qtbot.waitUntil(lambda: window.properties_panel.rows().get("Details", {}).get("Author")
                    == "Grace Hopper", timeout=10000)  # fmt: skip
    assert "Location" not in window.properties_panel.rows()
    assert window.properties_panel.save_message() == "Saved."
    _settle(qtbot, window)


def test_form_cancel_changes_nothing(qtbot, window, tmp_path, monkeypatch):
    p = write_test_photo(tmp_path / "phone.jpg")
    before = p.read_bytes()
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: False)
    window.add_files([str(p)])
    window.enter_properties()
    form = _form(qtbot, window)
    form.remove_all.setChecked(True)
    form.save_button.click()
    window.properties_panel.tasks.wait(3000)
    assert p.read_bytes() == before
    _settle(qtbot, window)


def test_form_applies_to_every_picture(qtbot, window, tmp_path, monkeypatch):
    a = write_test_photo(tmp_path / "a.jpg")
    b = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(b)
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: True)
    window.add_files([str(a), str(b)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert not form.apply_all.isHidden()
    form.apply_all.setChecked(True)
    assert (
        form.edits["title"].text() == ""
        and form.edits["title"].placeholderText() == "Several values"
    )
    assert form.rotation.currentText() == "Keep as they are"
    form.edits["copyright"].setText("CC0")
    form.edits["copyright"].textEdited.emit("CC0")
    form.save_button.click()
    _wait_saved(qtbot, window)
    da, db = photo.read_details(a.read_bytes()), photo.read_details(b.read_bytes())
    assert da.copyright == db.copyright == "CC0"
    assert da.title == "Harbour at dusk" and db.title == ""  # "Several values" left alone
    assert da.orientation == 6
    assert window.properties_panel.save_message() == "Saved 2 pictures."
    _settle(qtbot, window)


def test_form_refuses_a_bad_date(qtbot, window, tmp_path):
    p = write_test_photo(tmp_path / "phone.jpg")
    before = p.read_bytes()
    window.add_files([str(p)])
    window.enter_properties()
    form = _form(qtbot, window)
    form.edits["taken"].setText("12/11/2026")
    form.edits["taken"].textEdited.emit("12/11/2026")
    assert form.edits["taken"].property("invalid") is True
    form.copy_button.click()
    assert "year-month-day" in window.properties_panel.save_message()
    assert p.read_bytes() == before and not (tmp_path / "phone (1).jpg").exists()
    _settle(qtbot, window)


def test_form_only_for_pictures_it_can_change(qtbot, window, media):
    window.add_files([str(media / "anim.gif")])
    window.enter_properties()
    qtbot.waitUntil(lambda: "Picture" in window.properties_panel.rows(), timeout=10000)
    assert window.properties_panel.form.isHidden()
    _settle(qtbot, window)


# ----------------------------------------------------------------------------- saving safety
def test_save_keeps_mode_and_follows_links(tmp_path):
    import os
    import stat

    p = write_test_photo(tmp_path / "private.jpg")
    os.chmod(p, 0o600)
    link = tmp_path / "link.jpg"
    link.symlink_to(p)
    written = photo.save_file(link, photo.PhotoEdit({"title": "Via link"}))
    assert written == p.resolve()
    assert link.is_symlink()  # the link is still a link…
    assert photo.read_details(p.read_bytes()).title == "Via link"  # …and its target changed
    assert stat.S_IMODE(p.stat().st_mode) == 0o600  # a private picture stays private
    assert not list(tmp_path.glob(photo.TEMP_PREFIX + "*"))


def test_locked_file_says_so_and_leaves_nothing_behind(tmp_path, monkeypatch):
    import os

    p = write_test_photo(tmp_path / "a.jpg")
    before = p.read_bytes()

    def locked(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(photo.PhotoError, match="open in another program"):
        photo.save_file(p, photo.PhotoEdit({"title": "x"}))
    assert p.read_bytes() == before
    assert not list(tmp_path.glob(photo.TEMP_PREFIX + "*"))


def test_save_as_copy_never_overwrites(tmp_path):
    p = write_test_photo(tmp_path / "a.jpg")
    (tmp_path / "a (1).jpg").write_bytes(b"someone else's")
    out = photo.save_file(p, photo.PhotoEdit({"title": "x"}), as_copy=True)
    assert out.name == "a (2).jpg"
    assert (tmp_path / "a (1).jpg").read_bytes() == b"someone else's"


def test_location_hiding_places_are_cleaned():
    cases = {c["name"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))}
    data = bytes.fromhex(cases["hiding.jpg: read"]["input"])
    assert b"Copenhagen" in data and data.count(b"\xff\xd8") == 2
    out = photo.apply(data, photo.PhotoEdit(remove_location=True))
    assert b"Copenhagen" not in out and b"Denmark" not in out  # IPTC city / country
    assert b"Caption kept" in out  # other IPTC records stay
    assert out.count(b"\xff\xd8") == 1  # the appended preview picture (own EXIF + GPS) is gone
    assert b"xmp/extension" not in out and b"MPF\x00" not in out
    gone = photo.apply(data, photo.PhotoEdit(remove_all=True))
    assert b"c2pa" not in gone and b"Caption kept" not in gone
    xmp_case = bytes.fromhex(cases["xmp2.jpg: read"]["input"])
    cleaned = photo.apply(xmp_case, photo.PhotoEdit(remove_location=True))
    assert b"GPSLatitude" not in cleaned and b"Copenhagen" not in cleaned  # single quotes too
    assert b"Mine" in cleaned
