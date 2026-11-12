"""The normaliser's output is canonical: a second pass changes nothing, at any nesting depth.

The website runs the same checks (web/tests/doceditor-subset.test.ts) on its port.
"""

from __future__ import annotations

import re

import pytest

from umc.doceditor.html_subset import MAX_NESTING, normalise_html


def norm(html: str) -> str:
    return normalise_html(html)[0]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # a trailing space beside an empty tag at the end of a block (trimmed after merging)
        ("<p><b>z </b><b></b> </p>", "<p><b>z</b></p>"),
        ('<p><a href="u">z </a><a href="u"></a></p>', '<p><a href="u">z</a></p>'),
        (
            "<blockquote><p><strong>z </strong><strong></strong>  </p></blockquote>",
            "<blockquote><p><b>z</b></p></blockquote>",
        ),
        # ... and a space that merging puts after a space
        ('<p><a href="u">z </a><a href="u"></a> x</p>', '<p><a href="u">z </a>x</p>'),
        # white space at the start of a list item is kept like in a paragraph (a tab stays)
        ("<ol><li><sub>\t</sub><b>x</b></li></ol>", "<ol><li>\t<b>x</b></li></ol>"),
        ("<ol><li>\t<b>x</b></li></ol>", "<ol><li>\t<b>x</b></li></ol>"),
        ("<div><ol><li><code>\t</code></li></ol></div>", "<ol><li>\t</li></ol>"),
        ("<ul><li><p>a</p>\t</li></ul>", "<ul><li>a<br>\t</li></ul>"),
        # loose lists still drop their source formatting
        ("<ul>\n<li>\n<p>a</p>\n</li>\n</ul>", "<ul><li>a</li></ul>"),
    ],
)
def test_second_pass_changes_nothing(html: str, expected: str) -> None:
    assert norm(html) == expected
    assert norm(expected) == expected


def test_nesting_below_the_cap_is_kept() -> None:
    html = "<blockquote>" * (MAX_NESTING - 1) + "<p>a <b>b</b></p>"
    out = norm(html)
    assert out.count("<blockquote>") == MAX_NESTING - 1
    assert "<p>a <b>b</b></p>" in out
    assert norm(out) == out


@pytest.mark.parametrize(
    ("html", "words"),
    [
        ("<blockquote>" * 150 + "<p>a <b>b</b></p>", "a b"),
        ("<ul><li>" * 150 + "a<p>b</p>c", "a b c"),
        ("<ul>" * 150 + "<li>a</li>", "a"),
        ("<table><tr><td>" * 120 + "<p>a</p><p>b</p>", "a b"),
        ("<div>" * 300 + "a<div>b</div>c", "a b c"),
        ("<p>" + '<span style="color:red">' * 200 + "a<br>b" + "</span>" * 200 + "</p>", "a b"),
        ("<ol><li>" * 60 + "<table><tr><td>" * 60 + "x", "x"),
        (
            "<blockquote>" * 98 + "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>",
            "a b c",
        ),
        (
            "<blockquote>" * 99
            + '<table><tr><td>a</td><td><img src="img/1.png"> b</td></tr></table>',
            "a b",
        ),
    ],
)
def test_nesting_above_the_cap_is_flattened(html: str, words: str) -> None:
    out = norm(html)
    assert norm(out) == out
    depth = deepest = 0
    for tag in re.findall(r"<(/?)(blockquote|ul|ol|li|table|tr|td|span|b|i|u|s|a)[ >]", out):
        depth += -1 if tag[0] else 1
        deepest = max(deepest, depth)
    assert deepest <= MAX_NESTING + 2
    for word in words.split():
        assert word in re.sub(r"<[^>]*>", "", out)


def test_very_deep_input_keeps_its_text() -> None:
    out = norm("<div>" * 100000 + "x")
    assert out == "<p>x</p>"


# ----- property: normalise(normalise(x)) == normalise(x) -------------------------------------


class _Random:
    """A small seeded generator (the same LCG as the web test)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def __call__(self) -> float:
        self.seed = (self.seed * 1103515245 + 12345) & 0x7FFFFFFF
        return self.seed / 0x7FFFFFFF

    def pick(self, items: list):
        return items[int(self() * len(items))]


_TEXTS = ["a", "b c", " ", "  ", "\t", "\xa0", "x\xa0 y", "\n", "&amp;", "z ", " q"]
_SPAN = ["color:red", "font-size:14pt", "background-color:#00ff00;color:#000", "font-family:Arial"]
_STYLE = [
    "",
    ' style="text-align:center"',
    ' style="margin-left:36pt"',
    ' style="page-break-before:always;text-align:right"',
]


def _inline(r: _Random, d: int) -> str:
    if d > 3 or r() < 0.35:
        return r.pick(_TEXTS)
    k = int(r() * 12)

    def kids() -> str:
        return "".join(_inline(r, d + 1) for _ in range(int(r() * 3)))

    if k in (0, 1, 2, 3, 8, 9, 10):
        tag = ["b", "i", "u", "s", "", "", "", "", "strong", "code", "sub"][k]
        return f"<{tag}>{kids()}</{tag}>"
    if k == 4:
        return f'<a href="{r.pick(["u", "v"])}">{kids()}</a>'
    if k == 5:
        return f'<span style="{r.pick(_SPAN)}">{kids()}</span>'
    if k == 6:
        width = r.pick(["", ' width="5"'])
        return f'<img src="img/{r.pick([1, 2])}.png"{width}>'
    if k == 7:
        return "<br>"
    return r.pick(_TEXTS)


def _inl(r: _Random) -> str:
    return "".join(_inline(r, 0) for _ in range(1 + int(r() * 4)))


def _block(r: _Random, d: int) -> str:
    def kids(f) -> str:
        return "".join(f() for _ in range(int(r() * 3)))

    st = r.pick(_STYLE)
    k = int(r() * (4 if d > 3 else 11))
    if k == 0:
        return f"<p{st}>{_inl(r)}</p>"
    if k == 1:
        return f"<h{1 + int(r() * 5)}{st}>{_inl(r)}</h1>"
    if k == 2:
        return f"<pre>{r.pick(['', chr(10), chr(10) * 2])}{_inl(r)}{r.pick(['', chr(10)])}</pre>"
    if k == 3:
        return r.pick(["<hr>", _inl(r)])
    if k == 4:
        return f"<blockquote>{kids(lambda: _block(r, d + 1))}</blockquote>"
    if k in (5, 6):
        tag = r.pick(["ul", "ol"])

        def item() -> str:
            inner = _inl(r) + (_block(r, d + 1) if r() < 0.4 else "")
            return f"<li{st}>{inner}{_inl(r) if r() < 0.3 else ''}</li>"

        return f"<{tag}>{kids(item)}</ul>"
    if k in (7, 8):
        border = r.pick(["", ' style="border:none"'])

        def cell() -> str:
            span = r.pick(["", ' colspan="2"', ' rowspan="2"'])
            body = _inl(r) if r() < 0.5 else kids(lambda: _block(r, d + 1))
            return f"<td{span}>{body}</td>"

        return f"<table{border}>{kids(lambda: f'<tr>{kids(cell)}</tr>')}</table>"
    if k == 9:
        return f"<div>{kids(lambda: _block(r, d + 1))}</div>"
    return f"<p>{_inl(r)}</p>"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_normalising_twice_changes_nothing(seed: int) -> None:
    r = _Random(seed * 7919)
    for _ in range(400):
        raw = "".join(_block(r, 0) for _ in range(1 + int(r() * 4)))
        once = norm(raw)
        assert norm(once) == once, raw


def test_a_link_inside_a_link_is_only_its_text() -> None:
    # libxml2 keeps the nesting; an HTML5 parser (and so a second pass) would split the links
    html = '<p><a href="u">x<code><a href="v">y</a></code>z</a></p>'
    assert norm(html) == '<p><a href="u">xyz</a></p>'
    assert norm(norm(html)) == norm(html)
