from tests.doceditor_samples import PNG_1PX, b64
from umc.doceditor.html_subset import normalise_html, text_length
from umc.doceditor.types import (
    W_COMMENTS,
    W_HEADERS,
    next_image_name,
    warning_text,
)

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
