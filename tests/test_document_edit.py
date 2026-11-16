"""Properties step 5: changing PDF / Office / OpenDocument details (umc.properties.document)."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict
from pathlib import Path

import pymupdf
import pytest

from umc.properties import document as D
from umc.properties.document import DocumentEdit

CASES = json.loads((Path(__file__).parent / "fixtures" / "document_cases.json").read_text("utf-8"))


def check(case, fn, *args):
    if "error" in case:
        with pytest.raises(D.DocumentError, match=case["error"]):
            fn(*args)
    else:
        assert fn(*args) == case["expect"]


# ---------------------------------------------------------------- shared cases
def test_clean_and_keywords_shared_cases():
    for c in CASES["clean"]:
        check(c, D.clean_values, c["values"])
    for c in CASES["keywords"]:
        assert D.keyword_list(c["text"]) == c["expect"]
    for c in CASES["unescape"]:
        assert D.unescape(c["text"]) == c["expect"]


@pytest.mark.parametrize("name", ["core", "odf"])
def test_xml_shared_cases(name):
    fn = D.core_xml if name == "core" else D.odf_meta
    for c in CASES[name]:
        check(c, fn, c["xml"], c["values"])


def test_package_parts_shared_cases():
    for c in CASES["types"]:
        check(c, D.content_types, c["xml"])
    for c in CASES["rels"]:
        check(c, D.package_rels, c["xml"])
    for c in CASES["manifest"]:
        check(c, D.manifest, c["xml"])
    for c in CASES["target"]:
        assert D.core_target(c["xml"]) == c["expect"]
    for c in CASES["details"]:
        assert asdict(D.details_from_parts(c["kind"], c["parts"])) == c["expect"]
    for c in CASES["changed"]:
        check(c, D.changed_parts, c["kind"], c["parts"], c["values"])


def test_changed_xml_still_parses():
    from xml.etree import ElementTree as ET

    for c in CASES["core"] + CASES["odf"]:
        if "expect" in c:
            ET.fromstring(c["expect"].encode("utf-8"))


def test_kinds():
    assert D.kind_of("PDF") == "pdf" and D.kind_of("xlsx") == "office" and D.kind_of("odp") == "odf"
    assert D.kind_of("doc") is None and D.kind_of("txt") is None


# ---------------------------------------------------------------- real files
def make_pdf(path: Path, **save) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hello")
    doc.set_metadata({"title": "Old", "author": "Ann", "producer": "tests"})
    doc.save(path, **save)
    doc.close()
    return path


def make_docx(path: Path) -> Path:
    import docx

    d = docx.Document()
    d.add_paragraph("Body text")
    d.core_properties.title = "Old"
    d.core_properties.author = "Ann"
    d.save(path)
    return path


def make_odt(path: Path, meta: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", "<office:document-content/>", zipfile.ZIP_DEFLATED)
        if meta:
            zf.writestr("meta.xml", CASES["odf"][0]["xml"], zipfile.ZIP_DEFLATED)
        zf.writestr("META-INF/manifest.xml", CASES["manifest"][0]["xml"], zipfile.ZIP_DEFLATED)
    return path


def test_pdf_round_trip_keeps_the_pages(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    before = src.read_bytes()
    D.save_file(src, DocumentEdit({"title": "New ☃", "author": "", "keywords": "x, y"}))
    after = src.read_bytes()
    assert after.startswith(before)  # incremental: appended, nothing rewritten
    d = D.read_details(src)
    assert (d.title, d.author, d.keywords, d.signed) == ("New ☃", "", "x, y", False)
    with pymupdf.open(src) as doc:
        assert doc.metadata["producer"] == "tests" and "Hello" in doc[0].get_text()


def test_pdf_xmp_twin_removed(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    with pymupdf.open(src) as doc:
        doc.set_xml_metadata(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Stale</dc:title>'
            "</rdf:Description></rdf:RDF></x:xmpmeta>"
        )
        doc.saveIncr()
    D.save_file(src, DocumentEdit({"title": "Fresh"}))
    with pymupdf.open(src) as doc:
        assert "Stale" not in doc.get_xml_metadata()
    assert D.read_details(src).title == "Fresh"


def test_locked_pdf_needs_the_password_and_stays_locked(tmp_path):
    src = make_pdf(
        tmp_path / "a.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="own"
    )
    with pytest.raises(D.PasswordNeeded, match="locked"):
        D.read_details(src)
    with pytest.raises(D.PasswordNeeded, match="isn't right"):
        D.read_details(src, "nope")
    before = src.read_bytes()
    with pytest.raises(D.PasswordNeeded):
        D.save_file(src, DocumentEdit({"title": "X"}, password="nope"))
    assert src.read_bytes() == before
    D.save_file(src, DocumentEdit({"title": "Unlocked?"}, password="pw"))
    assert D.read_details(src, "pw").title == "Unlocked?"
    with pymupdf.open(src) as doc:
        assert doc.needs_pass


def test_docx_round_trip_keeps_everything_else(tmp_path):
    import docx

    src = make_docx(tmp_path / "a.docx")
    with zipfile.ZipFile(src) as zf:
        before = {i.filename: (zf.read(i), i.compress_type) for i in zf.infolist()}
    copy = D.save_file(
        src,
        DocumentEdit({"title": "New & <odd>", "subject": "S", "keywords": "k1, k2"}),
        as_copy=True,
    )
    assert copy.name == "a (1).docx"
    with zipfile.ZipFile(copy) as zf:
        after = {i.filename: (zf.read(i), i.compress_type) for i in zf.infolist()}
    assert list(after) == list(before)
    assert [k for k in after if after[k] != before[k]] == ["docProps/core.xml"]
    props = docx.Document(copy).core_properties
    assert (props.title, props.subject, props.author, props.keywords) == (
        "New & <odd>",
        "S",
        "Ann",
        "k1, k2",
    )
    assert D.read_details(src).title == "Old"  # the original is untouched


def test_docx_without_core_part_gets_one(tmp_path):
    import docx

    src = make_docx(tmp_path / "a.docx")
    plain = tmp_path / "plain.docx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "docProps/core.xml":
                continue
            if info.filename == "_rels/.rels":
                data = re.sub(rb"<Relationship [^>]*core-properties[^>]*/>", b"", data)
            zout.writestr(info, data)
    D.save_file(plain, DocumentEdit({"title": "Made"}))
    assert docx.Document(plain).core_properties.title == "Made"


def test_xlsx_round_trip(tmp_path):
    import openpyxl

    src = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = 5
    wb.save(src)
    D.save_file(src, DocumentEdit({"title": "Sheet", "author": "Bo"}))
    wb = openpyxl.load_workbook(src)
    assert (wb.properties.title, wb.properties.creator, wb.active["A1"].value) == ("Sheet", "Bo", 5)


@pytest.mark.parametrize("meta", [True, False])
def test_odt_round_trip(tmp_path, meta):
    src = make_odt(tmp_path / "a.odt", meta)
    D.save_file(src, DocumentEdit({"title": "T", "keywords": "a; b", "author": "Bo"}))
    with zipfile.ZipFile(src) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert ("meta.xml" in zf.read("META-INF/manifest.xml").decode()) is not meta  # added if new
    d = D.read_details(src)
    assert (d.title, d.keywords, d.author) == ("T", "a, b", "Bo")


def test_bad_input_changes_nothing(tmp_path):
    src = make_docx(tmp_path / "a.docx")
    before = src.read_bytes()
    with pytest.raises(D.DocumentError, match="2000"):
        D.save_file(src, DocumentEdit({"title": "x" * 3000}))
    assert src.read_bytes() == before
    broken = tmp_path / "b.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(D.DocumentError):
        D.read_details(broken)
    with pytest.raises(D.DocumentError, match="Only PDF"):
        D.read_details(tmp_path / "x.doc")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".umc-saving-")]


# ---------------------------------------------------------------- the Properties card
@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    monkeypatch.setenv("UMC_REDUCE_MOTION", "1")
    monkeypatch.setattr(
        "umc.gui.properties.panel.PropertiesPanel.confirm_original", lambda self, n: True
    )
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _form(qtbot, w):
    panel = w.properties_panel
    qtbot.waitUntil(
        lambda: not panel.document_form.isHidden() and "File" in panel.rows(), timeout=10000
    )
    return panel.document_form


def _type(box, text):
    box.setText(text)
    box.textEdited.emit(text)


def _wait_saved(qtbot, w):
    panel = w.properties_panel

    def done() -> bool:
        note = panel.save_message()
        return panel.status.isHidden() and "Sav" in note and "Saving" not in note

    qtbot.waitUntil(done, timeout=20000)
    panel.tasks.wait(5000)


def test_card_changes_a_docx_and_a_pdf(qtbot, window, tmp_path):
    a = make_docx(tmp_path / "a.docx")
    b = make_pdf(tmp_path / "b.pdf")
    D.save_file(b, DocumentEdit({"title": "Other"}))
    window.add_files([str(a), str(b)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert form.edits["title"].text() == "Old" and form.signed.isHidden()
    assert not form.save_button.isEnabled()
    assert form.apply_all.text() == "Apply to all 2 documents"
    form.apply_all.setChecked(True)
    assert form.edits["title"].placeholderText() == "Several values"
    assert form.edits["author"].text() == "Ann"
    _type(form.edits["keywords"], "plans, 2026")
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert window.properties_panel.save_message() == "Saved 2 documents."
    da, db = D.read_details(a), D.read_details(b)
    assert (da.title, db.title) == ("Old", "Other")  # "Several values" left alone
    assert da.keywords == db.keywords == "plans, 2026"


def test_card_opens_a_locked_pdf(qtbot, window, tmp_path):
    src = make_pdf(
        tmp_path / "a.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="own"
    )
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert (
        not form.lock_box.isHidden() and form.fields_box.isHidden() and form.save_button.isHidden()
    )
    form.password_box.setText("wrong")
    form.open_button.click()
    qtbot.waitUntil(lambda: "isn't right" in form.lock_text.text(), timeout=10000)
    form.password_box.setText("pw")
    form.open_button.click()
    qtbot.waitUntil(lambda: not form.fields_box.isHidden(), timeout=10000)
    assert form.edits["title"].text() == "Old"
    _type(form.edits["title"], "Opened")
    form.copy_button.click()
    _wait_saved(qtbot, window)
    copy = tmp_path / "a (1).pdf"
    assert D.read_details(copy, "pw").title == "Opened"
    assert D.read_details(src, "pw").title == "Old"


def test_card_warns_about_a_signed_pdf(qtbot, window, tmp_path, monkeypatch):
    src = make_pdf(tmp_path / "a.pdf")
    real = D.read_details
    monkeypatch.setattr(
        D,
        "read_details",
        lambda p, pw="": D.DocumentDetails(**{**asdict(real(p, pw)), "signed": True}),
    )
    monkeypatch.setitem(
        __import__("umc.gui.properties.panel", fromlist=["KINDS"]).KINDS,
        "document",
        (
            lambda ext: D.kind_of(ext) is not None,
            D.read_details,
            D.save_file,
            D.DocumentError,
            "document",
            "documents",
        ),
    )
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert not form.signed.isHidden() and "signed" in form.signed.text()
