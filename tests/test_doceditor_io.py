from __future__ import annotations

import docx
import pytest

from tests.doceditor_samples import PNG_1PX, b64
from umc.core.errors import ConversionError, ToolMissingError
from umc.doceditor import io as io_mod
from umc.doceditor.docx_reader import read
from umc.doceditor.docx_writer import write
from umc.doceditor.html_subset import normalise_html, text_length
from umc.doceditor.io import open_document
from umc.doceditor.routes import convert_file
from umc.doceditor.types import (
    W_COMMENTS,
    W_HEADERS,
    W_PICTURES,
    next_image_name,
    warning_text,
)
from umc.tools import libreoffice, pandoc

SAMPLE = (
    '<html><body><h1 class="x">T</h1><script>bad()</script>'
    '<p style="text-align:center;color:red;margin-top:3px">a <strong>b</strong> '
    '<span style="color:RGB(255,0,0);font-size:12pt;position:absolute">c</span>'
    '<font face="Arial">d</font></p><div>e</div><h4>f</h4></body></html>'
)


def test_warning_text():
    assert warning_text([W_HEADERS, W_COMMENTS]) == (
        "This document has parts the editor can't keep: headers and footers, comments. "
        "They will be lost if you save over the original — use Save as to keep a copy."
    )
    assert warning_text([]) == ""


def test_next_image_name():
    assert next_image_name({}, "png") == "img/1.png"
    assert next_image_name({"img/1.png": b""}, "jpg") == "img/2.jpg"


def test_normalise_keeps_subset_and_drops_the_rest():
    html, imgs = normalise_html(SAMPLE)
    assert "<script" not in html and "bad()" not in html and "class=" not in html
    assert "<h1>T</h1>" in html
    assert '<p style="text-align:center">' in html
    assert "<b>b</b>" in html
    assert 'style="font-size:12pt;color:#ff0000"' in html
    assert '<span style="font-family:Arial">d</span>' in html
    assert "<p>e</p>" in html and "<h3>f</h3>" in html
    assert imgs == {}


def test_normalise_idempotent():
    once, _ = normalise_html(SAMPLE)
    twice, _ = normalise_html(once)
    assert once == twice


def test_normalise_unicode_as_characters():
    text = "<p>Blåbærgrød — Привет — 你好 — 😀</p>"
    assert normalise_html(text)[0] == text


def test_normalise_images(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    html, imgs = normalise_html(
        f'<p><img src="pic.png" width="10"><img src="data:image/png;base64,{b64(PNG_1PX)}">'
        '<img src="https://example.com/x.png"></p>',
        base_dir=tmp_path,
    )
    assert list(imgs) == ["img/1.png", "img/2.png"] and "example.com" not in html


def test_images_never_read_outside_base_dir(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    html, imgs = normalise_html('<p><img src="../secret.png"></p>', base_dir=base)
    assert imgs == {} and "secret" not in html


def test_jpeg_extension_and_roundtrip_of_existing_img_names(tmp_path):
    (tmp_path / "a.jpeg").write_bytes(PNG_1PX)
    html, imgs = normalise_html('<p><img src="a.jpeg"></p>', base_dir=tmp_path)
    assert list(imgs) == ["img/1.jpg"]


def test_text_length():
    assert text_length("<p>ab <b>c</b></p><p>d</p>") == 5


def test_blocks_inside_inline_tags_are_separated():
    html, _ = normalise_html('<a href="http://a"><div>one</div><div>two</div></a>')
    assert html == '<p><a href="http://a">one<br>two</a></p>'
    html, _ = normalise_html("<span><div>one</div><div>two</div></span>")
    assert html == "<p>one<br>two</p>"
    assert normalise_html(html)[0] == html


def test_deeply_nested_content_survives():
    html, _ = normalise_html("<div>" * 600 + "x" + "</div>" * 600)
    assert "x" in html


def test_unparseable_html_raises():
    from umc.core.errors import ConversionError

    with pytest.raises(ConversionError, match="couldn't be read as HTML"):
        normalise_html("<div>" * 100000 + "x")


def test_empty_input_is_empty():
    assert normalise_html("  \n") == ("", {})


def test_open_markdown(media):
    d = open_document(media / "doc.md")
    assert d.source_format == "md"
    assert "<h1>Title</h1>" in d.html and "<b>world</b>" in d.html and "<ul>" in d.html


def test_open_txt_cp1252(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("Café\nline 2\n".encode("cp1252"))
    assert open_document(p).html == "<p>Café</p><p>line 2</p>"


def test_open_txt_escapes(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("a < b & c")
    assert open_document(p).html == "<p>a &lt; b &amp; c</p>"


def test_open_html_with_local_picture(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    p = tmp_path / "a.html"
    p.write_text('<html><body><p>Hi</p><p><img src="pic.png"></p></body></html>')
    d = open_document(p)
    assert d.source_format == "html"
    assert list(d.images) == ["img/1.png"] and 'src="img/1.png"' in d.html


def test_open_docx(tmp_path):
    p = write("<h1>T</h1><p>Hello <b>x</b></p>", {}, tmp_path / "a.docx")
    d = open_document(p)
    assert d.source_format == "docx"
    assert d.html == read(p).html


def test_open_empty_files(tmp_path):
    (tmp_path / "e.txt").write_bytes(b"")
    docx.Document().save(tmp_path / "e.docx")
    for name in ("e.txt", "e.docx"):
        assert open_document(tmp_path / name).html == "<p></p>"


def test_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "MAX_CHARS", 10)
    p = tmp_path / "big.txt"
    p.write_text("x" * 11)
    with pytest.raises(ConversionError, match="too large to edit here"):
        open_document(p)


def test_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "MAX_FILE_BYTES", 5)
    p = tmp_path / "big.txt"
    p.write_text("x" * 11)
    with pytest.raises(ConversionError, match="too large to edit here"):
        open_document(p)


def test_unsupported_type(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(ConversionError, match="can't be edited"):
        open_document(p)


def test_convert_file_unknown_target(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    with pytest.raises(ConversionError, match="Can't save as NOPE"):
        convert_file(p, "nope", tmp_path / "o.nope")


def test_open_odt_needs_a_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "soffice_path", lambda: None)
    monkeypatch.setattr(pandoc, "pandoc_path", lambda: None)
    p = tmp_path / "a.odt"
    p.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(ToolMissingError):
        open_document(p)


@pytest.mark.skipif(
    not (pandoc.is_available() or libreoffice.is_available()),
    reason="needs Pandoc or LibreOffice",
)
def test_open_odt_round_trip(tmp_path):
    src = write("<h1>Title</h1><p>Body</p>", {}, tmp_path / "a.docx")
    odt = convert_file(src, "odt", tmp_path / "a.odt")
    d = open_document(odt)
    assert d.source_format == "odt" and "<h1>Title</h1>" in d.html


def test_two_space_nested_list(tmp_path):
    from tests.test_doceditor_fixes import content_of
    from umc.doceditor.save import save_document

    p = tmp_path / "a.md"
    p.write_text("- one\n  - two\n    - three\n", encoding="utf-8")
    want = "<ul><li>one<ul><li>two<ul><li>three</li></ul></li></ul></li></ul>"
    assert open_document(p).html == want
    for _ in range(3):
        save_document(content_of(open_document(p), p), p, "md")
        assert open_document(p).html == want


def test_md_to_html_is_the_shared_configuration():
    assert io_mod.md_to_html("~~x~~\n").strip() == "<p><s>x</s></p>"
    assert "<table>" in io_mod.md_to_html("| a |\n|---|\n| 1 |\n")


def _three_cycles(tmp_path, text):
    from tests.test_doceditor_fixes import cycle

    p = tmp_path / "a.md"
    p.write_text(text, encoding="utf-8")
    docs = cycle(p, "md")
    assert all(d.html == docs[0].html for d in docs)
    return docs[0], p.read_text(encoding="utf-8")


def test_loose_list_is_plain_items(tmp_path):
    doc, md = _three_cycles(tmp_path, "- a\n\n- b\n")
    assert doc.html == "<ul><li>a</li><li>b</li></ul>"
    assert md == "- a\n- b\n"


def test_loose_list_second_paragraph_keeps_two_lines(tmp_path):
    doc, md = _three_cycles(tmp_path, "1. a\n\n   para two\n\n2. b\n")
    assert doc.html == "<ol><li>a<br>para two</li><li>b</li></ol>"
    assert md == "1. a<br>para two\n1. b\n"


def test_task_list_stays_a_task_list(tmp_path):
    doc, md = _three_cycles(tmp_path, "- [ ] todo\n- [x] done\n- [X] big\n")
    assert md == "- [ ] todo\n- [x] done\n- [X] big\n"


def test_comments_and_table_alignment_warn(tmp_path):
    from umc.doceditor.types import W_MDFORMAT

    for text in (
        "<!-- note -->\n\ntext\n",
        "| a |\n|:--|\n| 1 |\n",
        "| a | b |\n|--:|:-:|\n| 1 | 2 |\n",
    ):
        p = tmp_path / "a.md"
        p.write_text(text, encoding="utf-8")
        assert W_MDFORMAT in open_document(p).warnings, text
    p.write_text("| a |\n|---|\n| 1 |\n\n- x\n", encoding="utf-8")
    assert W_MDFORMAT not in open_document(p).warnings


def test_writers_own_list_separator_does_not_warn(tmp_path):
    from umc.doceditor.save import save_document
    from umc.doceditor.types import W_MDFORMAT, SaveContent

    p = tmp_path / "a.md"
    save_document(SaveContent("<ul><li>a</li></ul><ul><li>b</li></ul>", {}), p, "md")
    assert "<!-- -->" in p.read_text("utf-8")
    assert W_MDFORMAT not in open_document(p).warnings


def test_empty_heading_stays_stable(tmp_path):
    doc, md = _three_cycles(tmp_path, "##\n\ntext\n")
    assert md.startswith("##\n")


def test_link_space_is_written_as_percent_20():
    from umc.doceditor.md_writer import to_markdown

    assert to_markdown('<p><a href="https://x.org/a b">l</a></p>') == "[l](https://x.org/a%20b)\n"


def test_italic_alone_in_bold_is_written_with_underscores():
    from umc.doceditor.md_writer import to_markdown

    assert to_markdown("<p><b><i>x</i></b> <i><b>y</b></i></p>") == "**_x_** ***y***\n"


def test_bad_url_is_dropped_not_fatal():
    from umc.doceditor.html_subset import html_title, normalise_html_report
    from umc.doceditor.types import W_WEBPICS

    src = '<p><a href="http://[::1/x">t</a><img src="http://[::1/x.png"></p>'
    html, images, warnings = normalise_html_report(src)
    assert (html, images) == ("<p>t</p>", {})
    assert warnings == [W_WEBPICS]
    assert html_title(src) == ""
    assert text_length(src) == 1


def test_html_without_body():
    from umc.doceditor.html_subset import html_title, normalise_html_report

    src = "<html><head><title>T</title></head></html>"
    assert normalise_html_report(src) == ("", {}, [])
    assert html_title(src) == "T"
    assert text_length(src) == 0
    assert normalise_html("<html><p>x</p></html>") == ("<p>x</p>", {})


def test_indent_is_capped():
    big = "1" + "0" * 30
    assert normalise_html(f'<p style="margin-left:{big}pt">x</p>')[0] == (
        '<p style="margin-left:720pt">x</p>'
    )
    huge = "9" * 400  # float("9" * 400) is inf
    assert normalise_html(f'<li style="margin-left:{huge}px">x</li>')[0] == (
        '<p style="margin-left:720pt">x</p>'
    )
    assert normalise_html('<p style="margin-left:1e30pt">x</p>')[0] == "<p>x</p>"
    assert normalise_html('<p style="margin-left:738pt">x</p>')[0] == (
        '<p style="margin-left:720pt">x</p>'
    )


def test_table_first_in_cell(tmp_path):
    want = "<table><tr><td><table><tr><td><p>in</p></td></tr></table><p>after</p></td></tr></table>"
    alone = "<table><tr><td><table><tr><td><p>in</p></td></tr></table></td></tr></table>"
    for html in (want, alone):
        p = tmp_path / "a.docx"
        current = html
        for _ in range(3):
            write(current, {}, p)
            current = read(p).html
            assert current == html
    cell = docx.Document(str(tmp_path / "a.docx")).tables[0].cell(0, 0)._tc
    assert [c.tag.rsplit("}", 1)[1] for c in cell][1:] == ["tbl", "p"]  # Word's trailing p


def test_style_id_collision(tmp_path):
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml.ns import qn

    tpl = docx.Document()
    for name, sid, kind in (
        ("Web Link", "Hyperlink", WD_STYLE_TYPE.CHARACTER),
        ("Source Text", "Code", WD_STYLE_TYPE.PARAGRAPH),
        ("Pull Quote", "Quote", WD_STYLE_TYPE.PARAGRAPH),
    ):
        tpl.styles.add_style(name, kind).element.set(qn("w:styleId"), sid)
    for el in tpl.styles.element.findall(qn("w:style")):  # the real Quote style is missing
        if el.find(qn("w:name")).get(qn("w:val")) == "Quote":
            tpl.styles.element.remove(el)
    tpl.save(tmp_path / "tpl.docx")
    html = '<p><a href="https://x.org/">l</a></p><pre>code</pre><blockquote><p>q</p></blockquote>'
    p = write(html, {}, tmp_path / "a.docx", template=tmp_path / "tpl.docx")
    d = docx.Document(str(p))
    els = d.styles.element.findall(qn("w:style"))
    ids = [el.get(qn("w:styleId")) for el in els]
    assert len(ids) == len(set(ids))
    by_id = {el.get(qn("w:styleId")): el.find(qn("w:name")).get(qn("w:val")) for el in els}
    assert by_id["Hyperlink"] == "Web Link" and by_id["Code"] == "Source Text"
    assert by_id["Hyperlink1"] == "Hyperlink" and by_id["Code1"] == "Code"
    assert by_id["Quote1"] == "Quote"
    used = {el.get(qn("w:val")) for el in d.element.body.iter(qn("w:rStyle"), qn("w:pStyle"))}
    assert {"Hyperlink1", "Code1", "Quote1"} <= used
    assert read(p).html == html


def test_li_whitespace_edges():
    assert normalise_html("<ul><li><span> </span><p>x</p></li></ul>")[0] == "<ul><li>x</li></ul>"
    assert normalise_html("<ul><li>a <p>b</p></li></ul>")[0] == "<ul><li>a<br>b</li></ul>"
    assert normalise_html("<ul><li><p>a </p>\nb</li></ul>")[0] == "<ul><li>a<br>b</li></ul>"
    assert normalise_html("<ul><li><b>a </b><p>b</p></li></ul>")[0] == (
        "<ul><li><b>a</b><br>b</li></ul>"
    )


def test_c1_character_references():
    from umc.doceditor.html_subset import html_title

    assert normalise_html("<p>it&#146;s &#150; &#128;5</p>")[0] == "<p>it’s – €5</p>"
    assert normalise_html("<p>&#x92;&#X96;&#0146;&#146x &#00000000000000000000150;</p>")[0] == (
        "<p>’–’’x –</p>"
    )
    # Not in Windows-1252 (0x81) and outside 128-159: unchanged; DEL and too long: U+FFFD.
    assert normalise_html("<p>&#129;&#160;&#127;&#99999999999999999999999;</p>")[0] == (
        "<p>\x81\xa0��</p>"
    )
    assert html_title("<title>a&#151;b</title>") == "a—b"
    assert normalise_html('<p><a href="http://x/&#146;">l</a></p>')[0] == (
        '<p><a href="http://x/’">l</a></p>'
    )


def test_bad_numeric_references_become_replacement_characters():
    bad = "&#1;&#xFFFE;&#xD800;&#x110000;&#0;&#127;&#xFDD0;&#xFDEF;&#x1FFFF;&#x10FFFE;&#11;&#31;"
    assert normalise_html(f"<p>{bad}</p>")[0] == "<p>" + "�" * 12 + "</p>"
    assert normalise_html("<p>a&#9;b&#xFDCF;&#xFDF0;&#x10FFFD;</p>")[0] == (
        "<p>a\tb﷏ﷰ\U0010fffd</p>"
    )
    assert normalise_html("<p>a\x01b\x7fc</p>")[0] == "<p>ab\x7fc</p>"  # literal: as before


def test_unparsable_link_with_a_safe_scheme_is_kept():
    from umc.doceditor.html_subset import normalise_html_report
    from umc.doceditor.types import W_WEBPICS

    src = (
        '<p><a href="http://[abc]/x">t</a><a href="javascript://[abc]/%0aalert(1)">j</a>'
        '<a href="//[abc]/y">r</a><a href="http://[::1/x">u</a>'
        '<img src="http://[abc]/p.png"></p>'
    )
    assert normalise_html_report(src) == (
        '<p><a href="http://[abc]/x">t</a>j<a href="//[abc]/y">r</a>u</p>',
        {},
        [W_WEBPICS],
    )


def test_raw_text_elements_follow_html5():
    from umc.doceditor.html_subset import html_title, normalise_html_report
    from umc.doceditor.types import W_PAGESTYLE, W_RAWHTML

    assert normalise_html("<xmp>&#146; <b>x</b> &amp;</xmp>")[0] == (
        "<p>&amp;#146; &lt;b&gt;x&lt;/b&gt; &amp;amp;</p>"
    )
    for tag in ("noembed", "noframes"):
        assert normalise_html(f"<{tag}><i>a</i></{tag}><p>b</p>")[0] == (
            "<p>&lt;i&gt;a&lt;/i&gt;</p><p>b</p>"
        )
    assert normalise_html("<plaintext>a<p>b</p>")[0] == "<p>a&lt;p&gt;b&lt;/p&gt;</p>"
    assert normalise_html_report('<iframe><img src="http://x/a.png"></iframe><p>x</p>') == (
        "<p>x</p>",
        {},
        [W_RAWHTML],
    )
    assert html_title("<title>a<b>b</b>&#146;</title>") == "a<b>b</b>’"
    assert normalise_html_report('<script>"<xmp>"</script><p>hi</p>') == (
        "<p>hi</p>",
        {},
        [W_PAGESTYLE],
    )


def test_empty_wrapper_before_an_item_break():
    assert normalise_html("<ul><li><b> </b><p>x</p></li></ul>")[0] == "<ul><li>x</li></ul>"
    assert normalise_html('<ul><li>a<span style="color:red"><i> </i></span><p>b</p></li></ul>')[
        0
    ] == ("<ul><li>a<br>b</li></ul>")


def test_tag_names_in_attribute_values_are_not_tags():
    # A "<xmp>" or "<pre>" inside a quoted attribute value starts nothing (HTML5 reads it so).
    assert normalise_html('<p><a title="<xmp>" href="http://a">l</a>&#1;</p><p>b</p>')[0] == (
        '<p><a href="http://a">l</a>�</p><p>b</p>'
    )
    assert normalise_html("<p><span title='<xmp>'>s</span>&#146;<b>b</b></p>")[0] == (
        "<p>s’<b>b</b></p>"
    )
    assert normalise_html('<xmp title=">">a&#1;</xmp><p>&#1;b</p>')[0] == (
        "<p>a&amp;#1;</p><p>�b</p>"
    )


def test_carriage_return_references_and_stray_paragraph_ends():
    assert normalise_html("<p>a&#13;b</p><pre>a&#13;b</pre>")[0] == "<p>a b</p><pre>a\rb</pre>"
    assert normalise_html("<p><xmp>x</xmp></p>")[0] == "<p></p><p>x</p>"
    assert normalise_html("<p>a</p></p><div>b</p>c</div>")[0] == "<p>a</p><p>bc</p>"


def test_self_closing_slash_is_ignored_like_html5():
    # HTML5 ignores the "/" of <p/> (the paragraph stays open); libxml2 would close it.
    assert normalise_html("<p/>x</p>")[0] == "<p>x</p>"
    assert normalise_html("<div/>y<p title='a/'/>t</p>")[0] == ("<p>y</p><p>t</p>")
    assert normalise_html("<p><a href=x/>l</a>a<br/>z</p>")[0] == '<p><a href="x/">l</a>a<br>z</p>'


def test_closing_br_is_a_line_break_like_html5():
    # "</br>" is a common README habit; browsers and GitHub read it as <br>, libxml2 drops it.
    assert normalise_html("<p>a</br>b</p>")[0] == "<p>a<br>b</p>"
    assert normalise_html("<p>a</BR >b</br class=x>c</p>")[0] == "<p>a<br>b<br>c</p>"
    assert normalise_html('<p title="</br>">a</p><!-- </br> --><p>b</brx>c</p>')[0] == (
        "<p>a</p><p>bc</p>"
    )


def test_relative_pictures_that_cant_be_read_are_reported(tmp_path):
    (tmp_path / "pics").mkdir()
    (tmp_path / "pics" / "ok.png").write_bytes(PNG_1PX)
    (tmp_path.parent / "outside.png").write_bytes(PNG_1PX)
    md = tmp_path / "a.md"
    md.write_text("![](pics/ok.png)\n", encoding="utf-8")
    doc = open_document(md)
    assert doc.images and W_PICTURES not in doc.warnings
    for target in ("pics/missing.png", "../outside.png"):
        md.write_text(f"![]({target})\n", encoding="utf-8")
        doc = open_document(md)
        assert doc.images == {} and doc.warnings == [W_PICTURES], target
    page = tmp_path / "a.html"
    page.write_text('<p>x<img src="gone.png"></p>', encoding="utf-8")
    assert open_document(page).warnings == [W_PICTURES]
    # an earlier pass's own reference is kept, so nothing is lost
    page.write_text('<p>x<img src="img/1.png"></p>', encoding="utf-8")
    assert open_document(page).warnings == []
