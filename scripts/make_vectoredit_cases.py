# ruff: noqa: E501  (the cases hold long lines of SVG on purpose)
"""Write tests/fixtures/vectoredit_cases.json from the desktop vector-editor model.

Both test suites (tests/test_vectoredit.py, web/tests/vectoredit.test.ts) check these cases, so
the web port must read, change and write SVG exactly alike. Regenerate only on purpose:
    python scripts/make_vectoredit_cases.py
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from umc.vectoredit import doc as vdoc
from umc.vectoredit import geom, io, pathdata, shapes, xmlsrc

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "vectoredit_cases.json"

NUMBERS = [
    0,
    -0.0,
    1,
    -1,
    0.5,
    1.0005,
    1.0004,
    -1.0005,
    2.25,
    1e-7,
    -1e-7,
    123456.7891,
    1 / 3,
    -2 / 3,
    0.1 + 0.2,
    99.9996,
    -99.9996,
    1e6,
]

TRANSFORMS = [
    "",
    "translate(10)",
    "translate(10, 20)",
    "translate(10 20) scale(2)",
    "scale(2 3)",
    "rotate(90)",
    "rotate(45 10 10)",
    "skewX(30)",
    "skewY(-15)",
    "matrix(1 0 0 1 5 6)",
    "matrix(2,0,0,2,0,0)",
    " translate(1,2)  ,  rotate(30) ",
    "translate(1e1 -.5e1)",
    "translate(",
    "scale()",
    "rotate(1 2)",
    "foo(1)",
    "translate(1 2 3)",
    "translate(1)x",
]

PATHS = [
    "M10 10h20v20H10z",
    "m5 5 l1-1 c1,1 2,2 3,3 s1 1 2 2 q1 1 2 0 t2 0",
    "M0 0 C0 -10 10 -10 10 0",
    "M0 0 Q5 10 10 0 T20 0",
    "M0 0 A10 10 0 0 1 20 0",
    "M0 0 A10 5 30 1 0 20 10",
    "M0 0a1 1 0 00.5.5",
    "M1 2 3 4 5 6",
    "M1.5.5.5.5",
    "M1e2-1e1L-.5-.5",
    "M0 0 L10 0 Z m5 5 h1",
    "M0 0 L10",
    "L10 10",
    "M0 0 A0 5 0 0 1 10 0",
    "M0 0 A1 1 0 0 1 100 0",
    "M 10,10 S 20,0 30,10",
    "M0 0 T10 10",
    "",
    "M0 0 Z 1 2",
]

MATRICES = [
    [1, 0, 0, 1, 5, -3],
    [2, 0, 0, 3, 1, 1],
    [0, 1, -1, 0, 0, 0],
]

XML_OK = [
    '<svg xmlns="http://www.w3.org/2000/svg"/>',
    '<?xml version="1.0" encoding="UTF-8"?>\n<!-- made by hand -->\n<svg  width=\'10\' height="5">\n'
    '\t<rect x="1"   y="2"/>\n  <g id="a"><text>a &amp; b<![CDATA[<c>]]> &#233;&#x41;</text></g>\n'
    "</svg>\n<!-- end -->\n",
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
    '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\r\n<svg><?pi x?><g/></svg>',
    '<svg xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
    '<g inkscape:label="Layer 1" style="display:inline">\n<path d="M0 0" title="a&#10;b"/></g></svg>',
    "<svg>é\U0001f600<élément a='&lt;&gt;'/></svg>",
]

XML_BAD = [
    "",
    "   ",
    "<a><b></a>",
    "<a/><b/>",
    '<!DOCTYPE a [<!ENTITY x "y">]><a/>',
    "<a>&foo;</a>",
    '<a b="1" b="2"/>',
    "text<a/>",
    "<a>",
    "<a><!-- x</a>",
    "<a b=1/>",
    "<a>&</a>",
    "<a>&#0;</a>",
    "<a><![CDATA[x</a>",
    "<a/>tail",
    "<1a/>",
    '<a b="<"/>',
    "<a>" * 300 + "</a>" * 300,
]

# Edits: [path of element indexes from the root, attribute, value or None]
XML_EDITS = [
    (XML_OK[1], [[[0], "x", "5"], [[0], "fill", 'red &"\n']]),
    (XML_OK[1], [[[1], "id", None], [[1, 0], "x", "3"]]),
    (XML_OK[1], [[[], "width", "20"]]),
    (XML_OK[3], [[[0, 0], "d", "M1 1 L2 2"]]),
    (XML_OK[0], [[[], "viewBox", "0 0 1 1"]]),
]


def at(root: xmlsrc.Element, path: list[int]) -> xmlsrc.Element:
    for k in path:
        root = root.elements[k]
    return root


def setting(el: xmlsrc.Element, path: list[int], name: str, value: str | None) -> xmlsrc.Element:
    if not path:
        return el.set(name, value)
    kids = list(el.children)
    target = el.elements[path[0]]
    i = next(i for i, c in enumerate(kids) if c is target)
    kids[i] = setting(target, path[1:], name, value)
    return el.with_children(kids)


def box(b: geom.Box | None):
    return None if b is None else [b.x0, b.y0, b.x1, b.y1]


DRAWING = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="200" height="100" viewBox="0 0 400 200">
  <!-- a comment that must survive -->
  <defs><linearGradient id="g1"/></defs>
  <rect id="r1" x="10" y="10" width="50" height="20" rx="4" style="fill:red;stroke:#000"/>
  <g id="grp" transform="translate(100 0)" fill="blue" opacity="0.5">
    <circle id="c1" cx="10" cy="10" r="5"/>
    <path d="M0 0 L10 10"/>
  </g>
  <text id="t1" x="5" y="90" font-size="20">Hi <tspan>there</tspan></text>
  <g id="layer1" inkscape:groupmode="layer" inkscape:label="Layer 1">
    <ellipse id="e1" cx="300" cy="100" rx="20" ry="10" transform="rotate(30 300 100)"/>
    <polygon points="200,150 220,150 210,170" fill="green"/>
    <line x1="0" y1="190" x2="50" y2="195" stroke="black"/>
    <use href="#r1" x="0" y="100"/>
  </g>
</svg>
"""

SIMPLE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="1" y="1" width="2" height="2"/></svg>'
EMPTY = '<svg xmlns="http://www.w3.org/2000/svg">\n</svg>\n'

# [source, [op, ...]] → result text (or the error message)
EDITS = [
    (DRAWING, [["move", [[1], [2]], 5, 5]]),
    (DRAWING, [["move", [[2, 0]], 1, 2]]),
    (DRAWING, [["move", [[4, 0], [4, 1], [4, 2], [4, 3]], -10, 2.5]]),
    (DRAWING, [["move", [[3]], 3, 4]]),
    (DRAWING, [["resize", [[1]], [0, 0, 100, 40]]]),
    (DRAWING, [["resize", [[2]], [100, 0, 130, 30]]]),
    (DRAWING, [["resize", [[1], [4, 2]], [0, 0, 10, 10]]]),
    (DRAWING, [["resize", [[1]], [0, 0, 0, 10]]]),
    (DRAWING, [["style", [[1], [2]], "fill", "#00f"]]),
    (DRAWING, [["style", [[1]], "stroke", None], ["style", [[1]], "fill", None]]),
    (DRAWING, [["style", [[2, 1]], "stroke-width", "2"]]),
    (DRAWING, [["hide", [[1]], True]]),
    (DRAWING, [["hide", [[1]], True], ["hide", [[1]], False]]),
    (DRAWING, [["rename", [1], "box"]]),
    (DRAWING, [["rename", [1], "grp"]]),
    (DRAWING, [["rename", [1], "two words"]]),
    (DRAWING, [["rename", [1], ""]]),
    (DRAWING, [["delete", [[2, 1], [1]]]]),
    (DRAWING, [["delete", [[2], [2, 0]]]]),
    (DRAWING, [["duplicate", [[1], [3]]]]),
    (DRAWING, [["duplicate", [[2]]]]),
    (DRAWING, [["reorder", [1], "front"]]),
    (DRAWING, [["reorder", [1], "forward"]]),
    (DRAWING, [["reorder", [4], "back"]]),
    (DRAWING, [["reorder", [0], "backward"]]),
    (DRAWING, [["group", [[1], [3]]]]),
    (DRAWING, [["group", [[1], [2, 0]]]]),
    (DRAWING, [["ungroup", [2]]]),
    (DRAWING, [["ungroup", [1]]]),
    (DRAWING, [["text", [3], "a<b & c"]]),
    (DRAWING, [["text", [1], "x"]]),
    (DRAWING, [["attr", [2, 1], "d", "M1 1 L2 2"]]),
    (DRAWING, [["transform", [[1]], [0, 1, -1, 0, 0, 0]]]),
    (DRAWING, [["add", "rect", [1, 2, 11, 22], [["fill", "red"]], []]]),
    (DRAWING, [["add", "line", [0, 0, 10, 10], [], [4]]]),
    (
        EMPTY,
        [
            ["add", "ellipse", [0, 0, 10, 20], [["fill", "#123456"]], []],
            ["add", "line", [0, 0, 10, 20], [], []],
        ],
    ),
    (SIMPLE, [["add", "rect", [5, 5, 6, 6], [], []]]),
    (SIMPLE, [["delete", [[0]]]]),
    (SIMPLE, [["delete", [[]]]]),
    (SIMPLE, [["move", [], 1, 1]]),
    (SIMPLE, [["move", [[7]], 1, 1]]),
]

HITS = [
    (DRAWING, 20, 20, [], 0),
    (DRAWING, 105, 8, [], 0),
    (DRAWING, 105, 8, [2], 0),
    (DRAWING, 205, 155, [], 0),
    (DRAWING, 399, 1, [], 0),
    (DRAWING, 62, 31, [], 0),
    (DRAWING, 62, 31, [], 3),
]
BOXES = [
    (DRAWING, [0, 0, 120, 40], []),
    (DRAWING, [0, 0, 400, 200], []),
    (DRAWING, [0, 0, 400, 200], [4]),
]
CANVASES = [
    DRAWING,
    SIMPLE,
    EMPTY,
    '<svg xmlns="http://www.w3.org/2000/svg" width="50" viewBox="0 0 100 50"/>',
    '<svg xmlns="http://www.w3.org/2000/svg" height="10px" viewBox="0 0 100 50"/>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="34"/>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="5cm" viewBox="0 0 0 10"/>',
]
STYLES = [
    ('<rect fill="red" style="fill: blue ; stroke:green"/>', "fill"),
    ('<rect fill="red" style="stroke:green"/>', "fill"),
    ('<rect style="stroke:green"/>', "opacity"),
    ('<rect style=";;bad;x:1"/>', "x"),
]
STYLE_SETS = [
    ('<rect fill="red" style="fill: blue ; stroke:green"/>', "fill", "#fff"),
    ('<rect fill="red" style="stroke:green"/>', "fill", "#fff"),
    ('<rect style="stroke:green"/>', "fill", "#fff"),
    ("<rect/>", "fill", "#fff"),
    ('<rect fill="red" style="fill:blue"/>', "fill", None),
    ('<rect style="fill:blue"/>', "fill", None),
]
OPEN_BAD = [b"", b"\x00\x01binary", b"<html/>", b"<svg>", b"\x1f\x8bnot gzip"]


def run_ops(src: str, ops: list) -> str:
    d = vdoc.parse(src)
    try:
        for op in ops:
            name, args = op[0], op[1:]
            paths = lambda v: [tuple(p) for p in v]  # noqa: E731
            if name == "move":
                d = vdoc.move(d, paths(args[0]), args[1], args[2])
            elif name == "resize":
                d = vdoc.resize(d, paths(args[0]), geom.Box(*args[1]))
            elif name == "style":
                d = vdoc.set_style(d, paths(args[0]), args[1], args[2])
            elif name == "hide":
                d = vdoc.set_hidden(d, paths(args[0]), args[1])
            elif name == "rename":
                d = vdoc.rename(d, tuple(args[0]), args[1])
            elif name == "delete":
                d = vdoc.delete(d, paths(args[0]))
            elif name == "duplicate":
                d, _ = vdoc.duplicate(d, paths(args[0]))
            elif name == "reorder":
                d, _ = vdoc.reorder(d, tuple(args[0]), args[1])
            elif name == "group":
                d, _ = vdoc.group(d, paths(args[0]))
            elif name == "ungroup":
                d, _ = vdoc.ungroup(d, tuple(args[0]))
            elif name == "text":
                d = vdoc.set_text(d, tuple(args[0]), args[1])
            elif name == "attr":
                d = vdoc.set_attr(d, tuple(args[0]), args[1], args[2])
            elif name == "transform":
                d = vdoc.transform(d, paths(args[0]), geom.Matrix(*args[1]))
            elif name == "add":
                el = vdoc.new_shape(
                    args[0], geom.Box(*args[1]), [tuple(s) for s in args[2]], vdoc.ids(d)
                )
                d, _ = vdoc.add(d, el, tuple(args[3]))
            else:
                raise SystemExit(name)
    except vdoc.VectorError as e:
        return "error: " + str(e)
    return vdoc.write(d)


def step2(cases: dict) -> None:
    d = vdoc.parse(DRAWING)
    cases["items"] = [
        {
            "path": list(i.path),
            "kind": i.kind,
            "name": i.name,
            "depth": i.depth,
            "hidden": i.hidden,
            "container": i.container,
            "bounds": box(vdoc.bounds(d, i.path)),
        }
        for i in vdoc.items(d)
    ]
    cases["edits"] = [{"src": src, "ops": ops, "out": run_ops(src, ops)} for src, ops in EDITS]
    cases["hits"] = []
    for src, x, y, inside, pad in HITS:
        p = vdoc.hit(vdoc.parse(src), x, y, tuple(inside), pad)
        cases["hits"].append([x, y, inside, pad, None if p is None else list(p)])
    cases["in_box"] = [
        [b, inside, [list(p) for p in vdoc.in_box(vdoc.parse(src), geom.Box(*b), tuple(inside))]]
        for src, b, inside in BOXES
    ]
    cases["canvases"] = []
    for src in CANVASES:
        w, h, v = vdoc.canvas(vdoc.parse(src))
        cases["canvases"].append([src, w, h, box(v)])
    cases["style_get"] = [
        [src, n, shapes.style_get(xmlsrc.parse(src).root, n)] for src, n in STYLES
    ]
    cases["style_set"] = [
        [src, n, v, xmlsrc.write_node(shapes.style_set(xmlsrc.parse(src).root, n, v))]
        for src, n, v in STYLE_SETS
    ]
    cases["new_text"] = [
        [w, h, u, io.new_text(w, h, u)]
        for w, h, u in ((210, 297, "mm"), (1920, 1080, "px"), (0.5, 2.25, "in"))
    ]
    bad = []
    for data in OPEN_BAD:
        try:
            io.open_bytes(data)
        except vdoc.VectorError as e:
            bad.append([data.decode("latin-1"), str(e)])
        else:
            raise SystemExit(f"should fail: {data!r}")
    cases["open_bad"] = bad
    gz = gzip.compress(SIMPLE.encode(), mtime=0)
    cases["svgz"] = {"latin1": gz.decode("latin-1"), "text": SIMPLE}
    step3(cases)


NODE_PATHS = [
    "M0 0 L10 0 C10 5 5 10 0 10 Z",
    "M0 0 Q5 10 10 0 L20 0",
    "M0 0 A5 5 0 0 1 10 0",
    "M0 0 L1 1 M5 5 L6 6",
]
TO_PATH = [
    '<rect x="1" y="2" width="10" height="20" id="a" fill="red"/>',
    '<rect x="0" y="0" width="10" height="20" rx="2"/>',
    '<rect x="0" y="0" width="10" height="20" ry="30"/>',
    '<circle cx="5" cy="5" r="5" style="fill:blue"/>',
    '<ellipse cx="5" cy="5" rx="5" ry="2"><title>x</title></ellipse>',
    '<line x1="0" y1="0" x2="3" y2="4"/>',
    '<polyline points="0,0 1,0 1,1"/>',
    '<polygon points="0,0 1,0 1,1"/>',
    '<svg:rect x="0" y="0" width="1" height="1"/>',
    '<path d="M0 0"/>',
    "<text>a</text>",
    '<rect width="0" height="1"/>',
]
COPY_SRC = '<svg xmlns="http://www.w3.org/2000/svg">\n  <g transform="translate(5 5)"><rect id="r1" x="0" y="0" width="1" height="1"/></g>\n</svg>\n'


def seg_list(segs):
    return [list(s) for s in segs]


def step3(cases: dict) -> None:
    nodes = []
    for d in NODE_PATHS:
        segs = pathdata.parse(d)
        pts = pathdata.points(segs)
        entry = {"d": d, "points": [list(p) for p in pts], "moved": [], "deleted": [], "split": []}
        for i, slot, x, y in pts:
            entry["moved"].append(pathdata.write(pathdata.move_point(segs, i, slot, x + 1, y - 2)))
        for i, s in enumerate(segs):
            if s[0] != "Z":
                entry["deleted"].append(pathdata.write(pathdata.delete_point(segs, i)))
            if s[0] not in ("M", "Z"):
                entry["split"].append(pathdata.write(pathdata.split(segs, i)))
        nodes.append(entry)
    cases["nodes"] = nodes
    out = []
    for src in TO_PATH:
        r = shapes.to_path(xmlsrc.parse(src).root)
        out.append([src, None if r is None else xmlsrc.write_node(r)])
    cases["to_path"] = out
    d = vdoc.parse(COPY_SRC)
    text = vdoc.copy_text(d, [(0, 0)])
    d2, ps = vdoc.paste_text(d, text, 10, 0)
    d3, ps3 = vdoc.paste_text(d2, DRAWING, 0, 0, (0,))
    cases["copy"] = {
        "src": COPY_SRC,
        "text": text,
        "pasted": vdoc.write(d2),
        "paths": [list(p) for p in ps],
        "drawing_into_group": vdoc.write(d3),
        "drawing_paths": [list(p) for p in ps3],
        "to_path_doc": vdoc.write(vdoc.to_path(vdoc.parse(DRAWING), [(1,), (2,), (4, 1)])),
    }


def main() -> None:
    cases: dict = {"numbers": [[x, geom.num(x)] for x in NUMBERS], "matrices": MATRICES}
    cases["transforms"] = []
    for t in TRANSFORMS:
        try:
            m = geom.parse_transform(t)
        except ValueError:
            cases["transforms"].append([t, None, None])
            continue
        cases["transforms"].append([t, [m.a, m.b, m.c, m.d, m.e, m.f], geom.format_transform(m)])
    cases["paths"] = []
    for d in PATHS:
        segs = pathdata.parse(d)
        cases["paths"].append(
            {
                "d": d,
                "segs": [list(s) for s in segs],
                "written": pathdata.write(segs),
                "bounds": box(pathdata.bounds(segs)),
                "moved": [
                    pathdata.write(pathdata.transform(segs, geom.Matrix(*m))) for m in MATRICES
                ],
            }
        )
    cases["xml_ok"] = []
    for src in XML_OK:
        doc = xmlsrc.parse(src)
        cases["xml_ok"].append(
            {
                "src": src,
                "root": doc.root.name,
                "text": doc.root.text(),
                "attrs": [list(a) for a in doc.root.attrs],
            }
        )
    cases["xml_bad"] = []
    for src in XML_BAD:
        try:
            xmlsrc.parse(src)
        except xmlsrc.XmlError:
            cases["xml_bad"].append(src)
        else:
            raise SystemExit(f"should fail: {src!r}")
    cases["xml_edits"] = []
    for src, edits in XML_EDITS:
        doc = xmlsrc.parse(src)
        root = doc.root
        for path, name, value in edits:
            root = setting(root, path, name, value)
        cases["xml_edits"].append(
            {"src": src, "edits": edits, "out": xmlsrc.write(doc.with_root(root))}
        )
    step2(cases)
    OUT.write_text(json.dumps(cases, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
