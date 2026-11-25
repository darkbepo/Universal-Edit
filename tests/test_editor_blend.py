"""Layer blend modes: compositing, merging, the ORA attribute, the undoable edit. The pixels are
the shared cases web/tests/editor-blend.test.ts checks too (scripts/make_blend_cases.py)."""

import json
from pathlib import Path

from PIL import Image

from umc.editor import blend, ops
from umc.editor.document import Document
from umc.editor.layer import Layer

CASES = json.loads((Path(__file__).parent / "fixtures" / "blend_cases.json").read_text())


def picture(pixels) -> Image.Image:
    img = Image.new("RGBA", (len(pixels), 1))
    img.putdata([tuple(p) for p in pixels])
    return img


def doc_for(mode: str, opacity: float) -> Document:
    back = Layer("back", picture(CASES["back"]))
    top = Layer("top", picture(CASES["source"]), opacity=opacity, blend=mode)
    return Document(len(CASES["back"]), 1, [back, top], active=1)


def row(img: Image.Image) -> list[list[int]]:
    return [list(img.getpixel((x, 0))) for x in range(img.width)]


def test_every_mode_matches_the_shared_pixels():
    assert [c["mode"] for c in CASES["cases"][::2]] == list(blend.IDS)
    for case in CASES["cases"]:
        assert row(doc_for(case["mode"], case["opacity"]).composite()) == case["out"], case["mode"]


def test_merging_keeps_the_look():
    for case in CASES["cases"]:
        doc = doc_for(case["mode"], case["opacity"])
        assert ops.merge_down(doc) is not None
        assert len(doc.layers) == 1 and doc.layers[0].blend == "normal"
        assert row(doc.composite()) == case["out"], case["mode"]


def test_blend_edit_is_one_undo_step():
    doc = doc_for("normal", 1.0)
    cmd = ops.set_blend_layers(doc, [1], "multiply")
    assert cmd is not None and doc.layers[1].blend == "multiply"
    assert ops.set_blend_layers(doc, [1], "multiply") is None
    assert ops.set_blend_layers(doc, [1], "nonsense") is None
    cmd.undo(doc)
    assert doc.layers[1].blend == "normal"


def test_ora_keeps_the_mode(tmp_path):
    from umc.editor.ora import read_ora, write_ora

    doc = doc_for("screen", 1.0)
    f = tmp_path / "a.ora"
    write_ora(doc, f)
    assert (
        'composite-op="svg:screen"' in __import__("zipfile").ZipFile(f).read("stack.xml").decode()
    )
    again = read_ora(f)
    assert [la.blend for la in again.layers] == ["normal", "screen"]
    assert blend.from_ora("svg:hue") == "normal" and blend.from_ora(None) == "normal"
    assert [[m, blend.ora_op(m)] for m in blend.IDS] == CASES["ora"]
