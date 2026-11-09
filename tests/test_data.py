import json

import pytest

from tests.conftest import convert
from umc.core.detect import detect
from umc.core.registry import targets_for

PEOPLE = [
    {"name": "Ada", "age": 36, "address": {"city": "London", "zip": "N1"}, "admin": True},
    {"name": "Lin", "age": 7, "address": {"city": "Oslo", "zip": "0150"}, "admin": False},
]


@pytest.fixture
def people_json(tmp_path):
    p = tmp_path / "people.json"
    p.write_text(json.dumps({"people": PEOPLE}), encoding="utf-8")
    return p


def test_targets(people_json, media):
    ids = {t.id for t in targets_for([detect(people_json)])}
    assert {"data:yaml", "data:xml", "data:csv", "data:tsv", "data:xlsx"} <= ids
    assert "data:json" not in ids  # same format
    csv_ids = {t.id for t in targets_for([detect(media / "data.csv")])}
    assert "data:json" in csv_ids and "doc:xlsx" not in csv_ids and "doc:csv" not in csv_ids


def test_json_csv_json_round_trip(people_json, tmp_path):
    [csv_out] = convert([people_json], "data:csv", tmp_path)
    text = csv_out.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "name,age,address.city,address.zip,admin"
    assert "Ada,36,London,N1,true" in text
    [back] = convert([csv_out], "data:json", tmp_path / "back")
    assert json.loads(back.read_text()) == PEOPLE  # numbers, booleans and nesting restored


def test_zip_codes_stay_text_in_xlsx_round_trip(people_json, tmp_path):
    [xlsx] = convert([people_json], "data:xlsx", tmp_path)
    from openpyxl import load_workbook

    ws = load_workbook(xlsx).active
    assert [c.value for c in ws[1]] == ["name", "age", "address.city", "address.zip", "admin"]
    assert ws["B2"].value == 36 and ws["D3"].value == "0150"
    [back] = convert([xlsx], "data:json", tmp_path / "back")
    assert json.loads(back.read_text()) == PEOPLE


def test_json_yaml_xml(people_json, tmp_path):
    [yml] = convert([people_json], "data:yaml", tmp_path)
    import yaml

    assert yaml.safe_load(yml.read_text()) == {"people": PEOPLE}
    [xml] = convert([yml], "data:xml", tmp_path)
    text = xml.read_text()
    assert text.startswith("<?xml") and "<city>London</city>" in text
    [back] = convert([xml], "data:json", tmp_path / "back")
    data = json.loads(back.read_text())
    assert data["root"]["people"][0]["address"]["city"] == "London"


def test_xml_attributes_and_excel_csv(tmp_path):
    src = tmp_path / "books.xml"
    src.write_text(
        '<library><book id="1"><title>Æble</title></book>'
        '<book id="2"><title>Bog</title></book></library>',
        encoding="utf-8",
    )
    [out] = convert([src], "data:csv", tmp_path, excel_bom=True, delimiter=";")
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines() == ["@id;title", "1;Æble", "2;Bog"]


def test_invalid_json_friendly_error(tmp_path):
    from umc.core.errors import ConversionError

    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(ConversionError, match="isn't valid JSON"):
        convert([bad], "data:csv", tmp_path)
