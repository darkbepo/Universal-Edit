"""The document editor reads HTML the same with every libxml2 version.

lxml's Linux wheels carry libxml2 2.14 (raw text tags read as HTML5 does, control characters
kept, nothing past ~2048 levels), the Windows ones an older libxml2. Run these with both
(``pip install "lxml==5.3.0"`` in a spare venv gives libxml2 2.12)."""

from __future__ import annotations

import pytest

from umc.doceditor.html_scan import html5_text, without_deep_tags
from umc.doceditor.html_subset import normalise_html, subset_fragment


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<plaintext>x &amp; <b>y</b>", "<p>x &amp;amp; &lt;b&gt;y&lt;/b&gt;</p>"),
        ("<plaintext/>x &amp; <b>y</b>", "<p>x &amp;amp; &lt;b&gt;y&lt;/b&gt;</p>"),
        ('<plaintext class="a">x</xmp>y', "<p>x&lt;/xmp&gt;y</p>"),
        ("<xmp>a &amp; <i>b</i>", "<p>a &amp;amp; &lt;i&gt;b&lt;/i&gt;</p>"),
        ("<xmp>a</xmp><p>b</p>", "<p>a</p><p>b</p>"),
    ],
)
def test_raw_text_reads_the_same(html, expected):
    assert normalise_html(html)[0] == expected


def test_control_characters_are_dropped_and_nul_is_a_space():
    assert normalise_html("<p>a\x01b\x0bc\x0cd\x00e\x7ff</p>")[0] == "<p>abcd e\x7ff</p>"
    root = subset_fragment("<p>a\x08b\x00c</p>")
    assert root[0].text == "ab c"


def test_very_deep_nesting_keeps_its_text_and_later_content():
    deep = "<b>" * 3000 + "k" + "</b>" * 3000 + "<p>after</p>"
    out = normalise_html(deep)[0]
    assert "k" in out and out.endswith("<p>after</p>")


def test_deep_tags_inside_raw_text_are_text():
    html = "<xmp>" + "<div>" * 2500 + "</xmp>" + "<b>" * 2100 + "k"
    out = normalise_html(html)[0]
    assert out.startswith("<p>&lt;div&gt;") and "&lt;div&gt;k" not in out
    assert "k</b>" in out and "&lt;/body&gt;" not in out


def test_without_deep_tags():
    assert without_deep_tags("<a><b><c>x</c></b></a>", 2) == "<a><b>x</b></a>"
    # void tags never open anything, comments are skipped, both are kept
    assert without_deep_tags("<a><br><img src=x><!--<b>--><b>y</b></a>", 1) == (
        "<a><br><img src=x><!--<b>-->y</a>"
    )
    # raw text / script content is text: never counted, never cut
    assert without_deep_tags("<a><xmp><b><c></xmp><b>z</b></a>", 1) == ("<a><xmp><b><c></xmp>z</a>")
    assert without_deep_tags("<a><plaintext><b>q", 1) == "<a><plaintext><b>q"


def test_escape_raw_off_leaves_raw_text_as_written():
    assert html5_text("<xmp>&amp; <b></xmp>", escape_raw=False) == "<xmp>&amp; <b></xmp>"
    assert html5_text("<xmp>&amp; <b></xmp>") == "<xmp>&amp;amp; &lt;b></xmp>"
