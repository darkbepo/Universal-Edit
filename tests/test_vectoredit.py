"""Vector editor model against the shared cases (web/tests/vectoredit.test.ts checks the same
file; made by scripts/make_vectoredit_cases.py)."""

import json
import math
from pathlib import Path

import pytest

from umc.vectoredit import geom, pathdata, xmlsrc

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "vectoredit_cases.json").read_text(encoding="utf-8")
)


def setting(el, path, name, value):
    """Set one attribute on the element at ``path`` (child element indexes) below ``el``."""
    if not path:
        return el.set(name, value)
    kids = list(el.children)
    target = el.elements[path[0]]
    i = next(i for i, c in enumerate(kids) if c is target)
    kids[i] = setting(target, path[1:], name, value)
    return el.with_children(kids)


def close(a, b):
    if a is None or b is None:
        return a is b
    return len(a) == len(b) and all(
        math.isclose(x, y, abs_tol=1e-9) for x, y in zip(a, b, strict=True)
    )


@pytest.mark.parametrize(("x", "text"), CASES["numbers"])
def test_numbers(x, text):
    assert geom.num(x) == text


@pytest.mark.parametrize(("text", "m", "written"), CASES["transforms"])
def test_transforms(text, m, written):
    if m is None:
        with pytest.raises(ValueError):
            geom.parse_transform(text)
        return
    got = geom.parse_transform(text)
    assert close([got.a, got.b, got.c, got.d, got.e, got.f], m)
    assert geom.format_transform(got) == written


@pytest.mark.parametrize("case", CASES["paths"], ids=lambda c: c["d"] or "empty")
def test_paths(case):
    segs = pathdata.parse(case["d"])
    assert [s[0] for s in segs] == [s[0] for s in case["segs"]]
    assert all(close(list(a[1:]), b[1:]) for a, b in zip(segs, case["segs"], strict=True))
    assert pathdata.write(segs) == case["written"]
    b = pathdata.bounds(segs)
    assert close(None if b is None else [b.x0, b.y0, b.x1, b.y1], case["bounds"])
    moved = [pathdata.write(pathdata.transform(segs, geom.Matrix(*m))) for m in CASES["matrices"]]
    assert moved == case["moved"]


@pytest.mark.parametrize("case", CASES["xml_ok"], ids=range(len(CASES["xml_ok"])))
def test_xml_round_trips_byte_for_byte(case):
    doc = xmlsrc.parse(case["src"])
    assert xmlsrc.write(doc) == case["src"]
    assert doc.root.name == case["root"] and doc.root.text() == case["text"]
    assert [list(a) for a in doc.root.attrs] == case["attrs"]


@pytest.mark.parametrize("src", CASES["xml_bad"], ids=range(len(CASES["xml_bad"])))
def test_xml_refuses(src):
    with pytest.raises(xmlsrc.XmlError):
        xmlsrc.parse(src)


@pytest.mark.parametrize("case", CASES["xml_edits"], ids=range(len(CASES["xml_edits"])))
def test_xml_edits_rewrite_only_what_changed(case):
    doc = xmlsrc.parse(case["src"])
    root = doc.root
    for path, name, value in case["edits"]:
        root = setting(root, path, name, value)
    assert xmlsrc.write(doc.with_root(root)) == case["out"]
    assert xmlsrc.write(xmlsrc.parse(case["out"])) == case["out"]


def test_matrix_inverse_and_boxes():
    m = geom.parse_transform("translate(5 6) rotate(30) scale(2 3)")
    x, y = (m @ m.inverse()).apply(7, -4)
    assert math.isclose(x, 7) and math.isclose(y, -4)
    with pytest.raises(ValueError):
        geom.scale(0).inverse()
    b = geom.Box(0, 0, 10, 20).transformed(geom.rotate(90))
    assert close([b.x0, b.y0, b.x1, b.y1], [-20, 0, 0, 10])


def test_new_elements_are_written_from_their_parts():
    el = xmlsrc.element("rect", [("x", "1")])
    g = xmlsrc.element("g", children=[el, xmlsrc.Text("\n")])
    assert xmlsrc.write_node(g) == '<g><rect x="1"/>\n</g>'
    closed = xmlsrc.parse("<svg/>").root.with_children([el])
    assert xmlsrc.write_node(closed) == '<svg><rect x="1"/></svg>'
