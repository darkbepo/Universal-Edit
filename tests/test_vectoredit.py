"""Vector editor model against the shared cases (web/tests/vectoredit.test.ts checks the same
file; made by scripts/make_vectoredit_cases.py)."""

import json
import math
import re
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


# ------------------------------------------------------------------ step 2: shapes, edits, io

from umc.vectoredit import doc as vdoc  # noqa: E402
from umc.vectoredit import history, io, shapes  # noqa: E402

DRAWING = CASES["edits"][0]["src"]


def run_ops(src, ops):
    d = vdoc.parse(src)

    def paths(v):
        return [tuple(p) for p in v]

    try:
        for name, *a in ops:
            if name == "move":
                d = vdoc.move(d, paths(a[0]), a[1], a[2])
            elif name == "resize":
                d = vdoc.resize(d, paths(a[0]), geom.Box(*a[1]))
            elif name == "style":
                d = vdoc.set_style(d, paths(a[0]), a[1], a[2])
            elif name == "hide":
                d = vdoc.set_hidden(d, paths(a[0]), a[1])
            elif name == "rename":
                d = vdoc.rename(d, tuple(a[0]), a[1])
            elif name == "delete":
                d = vdoc.delete(d, paths(a[0]))
            elif name == "duplicate":
                d, _ = vdoc.duplicate(d, paths(a[0]))
            elif name == "reorder":
                d, _ = vdoc.reorder(d, tuple(a[0]), a[1])
            elif name == "group":
                d, _ = vdoc.group(d, paths(a[0]))
            elif name == "ungroup":
                d, _ = vdoc.ungroup(d, tuple(a[0]))
            elif name == "text":
                d = vdoc.set_text(d, tuple(a[0]), a[1])
            elif name == "attr":
                d = vdoc.set_attr(d, tuple(a[0]), a[1], a[2])
            elif name == "transform":
                d = vdoc.transform(d, paths(a[0]), geom.Matrix(*a[1]))
            elif name == "add":
                el = vdoc.new_shape(a[0], geom.Box(*a[1]), [tuple(s) for s in a[2]], vdoc.ids(d))
                d, _ = vdoc.add(d, el, tuple(a[3]))
    except vdoc.VectorError as e:
        return "error: " + str(e)
    return vdoc.write(d)


def test_items_and_bounds():
    d = vdoc.parse(DRAWING)
    got = vdoc.items(d)
    assert len(got) == len(CASES["items"])
    for item, want in zip(got, CASES["items"], strict=True):
        assert [list(item.path), item.kind, item.name, item.depth, item.hidden, item.container] == [
            want["path"], want["kind"], want["name"], want["depth"], want["hidden"],
            want["container"],
        ]  # fmt: skip
        b = vdoc.bounds(d, item.path)
        assert close(None if b is None else [b.x0, b.y0, b.x1, b.y1], want["bounds"])


@pytest.mark.parametrize("case", CASES["edits"], ids=range(len(CASES["edits"])))
def test_edits(case):
    assert run_ops(case["src"], case["ops"]) == case["out"]


def test_hits_and_rubber_band():
    d = vdoc.parse(DRAWING)
    for x, y, inside, pad, want in CASES["hits"]:
        got = vdoc.hit(d, x, y, tuple(inside), pad)
        assert (None if got is None else list(got)) == want
    for b, inside, want in CASES["in_box"]:
        assert [list(p) for p in vdoc.in_box(d, geom.Box(*b), tuple(inside))] == want


@pytest.mark.parametrize("case", CASES["canvases"], ids=range(len(CASES["canvases"])))
def test_canvas(case):
    src, w, h, view = case
    gw, gh, gv = vdoc.canvas(vdoc.parse(src))
    assert close([gw, gh, gv.x0, gv.y0, gv.x1, gv.y1], [w, h, *view])


def test_styles():
    for src, name, want in CASES["style_get"]:
        assert shapes.style_get(xmlsrc.parse(src).root, name) == want
    for src, name, value, want in CASES["style_set"]:
        assert xmlsrc.write_node(shapes.style_set(xmlsrc.parse(src).root, name, value)) == want


def test_files():
    for w, h, unit, want in CASES["new_text"]:
        assert io.new_text(w, h, unit) == want
        opened = io.new_document(w, h, unit)
        assert io.to_bytes(opened, opened.doc) == want.encode()
    for data, message in CASES["open_bad"]:
        with pytest.raises(vdoc.VectorError, match=re.escape(message)):
            io.open_bytes(data.encode("latin-1"))
    gz = CASES["svgz"]["latin1"].encode("latin-1")
    opened = io.open_bytes(gz)
    assert opened.gz and vdoc.write(opened.doc) == CASES["svgz"]["text"]
    assert io.to_bytes(opened, opened.doc) == gz  # untouched: as read
    moved = vdoc.move(opened.doc, [(0,)], 1, 0)
    import gzip

    assert b'x="2"' in gzip.decompress(io.to_bytes(opened, moved))


def test_untouched_and_encodings():
    text = DRAWING.replace("\n", "\r\n")
    data = b"\xef\xbb\xbf" + text.encode()
    opened = io.open_bytes(data)
    assert io.to_bytes(opened, opened.doc) == data
    moved = vdoc.move(opened.doc, [(1,)], 1, 0)
    out = io.to_bytes(opened, moved)
    assert out.startswith(b"\xef\xbb\xbf") and b"\r\n" in out and b'x="11"' in out
    latin = io.open_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>caf\xe9</text></svg>'.encode("cp1252")
    )
    snow = vdoc.set_text(latin.doc, (0,), "☃")
    with pytest.raises(ValueError, match="encoding"):
        io.to_bytes(latin, snow)
    assert "☃".encode() in io.to_bytes(latin, snow, utf8=True)


def test_history_merges_and_tracks_saving():
    d0 = vdoc.parse(DRAWING)
    h = history.History(d0)
    assert not h.dirty
    d1 = vdoc.move(d0, [(1,)], 1, 0)
    h.push(d1, "Move", merge="nudge")
    h.push(vdoc.move(d1, [(1,)], 1, 0), "Move", merge="nudge")
    assert h.dirty and h.can_undo
    assert h.undo() == "Move" and h.doc is d0 and not h.dirty
    assert h.redo() == "Move" and 'x="12"' in vdoc.write(h.doc)
    h.mark_saved()
    assert not h.dirty
    h.push(h.doc, "Nothing")
    assert h.version == 4


def test_oversized_svgz_is_refused():
    import gzip

    bomb = gzip.compress(bytes(io.MAX_OPEN_BYTES + 10))
    with pytest.raises(vdoc.VectorError, match="too big"):
        io.open_bytes(bomb)


# ------------------------------------------------------------------ nodes, paths, clipboard


@pytest.mark.parametrize("case", CASES["nodes"], ids=lambda c: c["d"])
def test_nodes(case):
    segs = pathdata.parse(case["d"])
    pts = pathdata.points(segs)
    assert [list(p) for p in pts] == case["points"]
    moved = [pathdata.write(pathdata.move_point(segs, i, s, x + 1, y - 2)) for i, s, x, y in pts]
    assert moved == case["moved"]
    deleted = [
        pathdata.write(pathdata.delete_point(segs, i)) for i, s in enumerate(segs) if s[0] != "Z"
    ]
    assert deleted == case["deleted"]
    splits = [
        pathdata.write(pathdata.split(segs, i))
        for i, s in enumerate(segs)
        if s[0] not in ("M", "Z")
    ]
    assert splits == case["split"]
    with pytest.raises(ValueError):
        pathdata.move_point(segs, 0, "c1", 0, 0)


def test_shapes_become_paths():
    for src, want in CASES["to_path"]:
        r = shapes.to_path(xmlsrc.parse(src).root)
        assert (None if r is None else xmlsrc.write_node(r)) == want
    done = vdoc.to_path(vdoc.parse(DRAWING), [(1,), (2,), (4, 1)])
    assert vdoc.write(done) == CASES["copy"]["to_path_doc"]


def test_copy_and_paste():
    c = CASES["copy"]
    d = vdoc.parse(c["src"])
    text = vdoc.copy_text(d, [(0, 0)])
    assert text == c["text"]
    d2, ps = vdoc.paste_text(d, text, 10, 0)
    assert vdoc.write(d2) == c["pasted"] and [list(p) for p in ps] == c["paths"]
    d3, ps3 = vdoc.paste_text(d2, DRAWING, 0, 0, (0,))
    assert vdoc.write(d3) == c["drawing_into_group"]
    assert [list(p) for p in ps3] == c["drawing_paths"]
    with pytest.raises(vdoc.VectorError, match="nothing to paste"):
        vdoc.paste_text(d, '<svg xmlns="http://www.w3.org/2000/svg"><defs/></svg>')
