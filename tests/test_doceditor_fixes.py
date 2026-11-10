"""Round trips through real files for the final review fixes (no Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.doceditor_samples import PNG_1PX
from umc.doceditor.html_subset import normalise_html


@pytest.mark.parametrize(
    "src", [r"\host\share\a.png", "//host/share/a.png", r"C:\x.png", "/etc/x.png", "C:/x.png"]
)
def test_picture_paths_never_touch_other_places(tmp_path, monkeypatch, src):
    def guard(name):
        real = getattr(Path, name)

        def check(self, *a, **k):
            text = str(self)
            if "host" in text or "x.png" in text:
                raise AssertionError(f"{name} called with {text}")
            return real(self, *a, **k)

        return check

    for name in ("resolve", "is_file", "stat", "read_bytes"):
        monkeypatch.setattr(Path, name, guard(name))
    html, imgs = normalise_html(f'<p>a<img src="{src}"></p>', base_dir=tmp_path)
    assert imgs == {} and html == "<p>a</p>"


def test_relative_picture_still_read(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "p.png").write_bytes(PNG_1PX)
    _, imgs = normalise_html('<p><img src="sub/p.png"></p>', base_dir=tmp_path)
    assert list(imgs) == ["img/1.png"]


# ----- helpers: open -> save over -> reopen, like the editor page does ----------------------
import docx  # noqa: E402

from umc.doceditor.io import open_document  # noqa: E402
from umc.doceditor.save import save_document  # noqa: E402
from umc.doceditor.types import SaveContent  # noqa: E402

NBSP = "\xa0"


def content_of(doc, path: Path) -> SaveContent:
    """What the page would hand to save_document for an unedited document."""
    fields = {}
    for name in ("front_matter", "title"):
        if hasattr(doc, name) and hasattr(SaveContent, name):
            fields[name] = getattr(doc, name)
    if "template" in SaveContent.__dataclass_fields__ and doc.source_format == "docx":
        fields["template"] = path
    return SaveContent(doc.html, dict(doc.images), **fields)


def cycle(path: Path, fmt: str, n: int = 3) -> list:
    """Open and save over ``path`` ``n`` times; returns every opened document."""
    opened = []
    for _ in range(n):
        doc = open_document(path)
        opened.append(doc)
        save_document(content_of(doc, path), path, fmt)
    opened.append(open_document(path))
    return opened


# ----- Critical 1: whitespace -------------------------------------------------------------


def test_normaliser_keeps_tabs_and_nbsp():
    html = f"<p>a\tb {NBSP}{NBSP}c</p><p>{NBSP}{NBSP}x</p>"
    assert normalise_html(html)[0] == html
    # source-code formatting (line breaks and the indentation around them) still collapses
    assert normalise_html("<p>\n\t\tHello\n\t\tworld\n</p>")[0] == "<p>Hello world</p>"


def test_txt_reader_marks_significant_spaces(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("def f():\n    return 1\n\tTabbed\nA    B\n", encoding="utf-8")
    assert open_document(p).html == (
        f"<p>def f():</p><p>{NBSP * 4}return 1</p><p>\tTabbed</p><p>A {NBSP * 3}B</p>"
    )


def test_docx_tabs_and_spaces_survive_three_saves(tmp_path):
    p = tmp_path / "a.docx"
    d = docx.Document()
    d.add_paragraph("Name:\tJohn    Smith")
    d.add_paragraph("  indented")
    d.save(p)
    opened = cycle(p, "docx")
    assert len({o.html for o in opened}) == 1
    texts = [par.text for par in docx.Document(p).paragraphs]
    assert texts == ["Name:\tJohn    Smith", "  indented"]
    assert NBSP not in "".join(texts)
