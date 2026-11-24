# ruff: noqa: E501  (the cases hold long lines of SVG on purpose)
"""Write tests/fixtures/vectoredit_cases.json from the desktop vector-editor model.

Both test suites (tests/test_vectoredit.py, web/tests/vectoredit.test.ts) check these cases, so
the web port must read, change and write SVG exactly alike. Regenerate only on purpose:
    python scripts/make_vectoredit_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

from umc.vectoredit import geom, pathdata, xmlsrc

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
    OUT.write_text(json.dumps(cases, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
