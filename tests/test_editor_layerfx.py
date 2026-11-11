"""Layer effects (umc.editor.layerfx): drawn live, kept on the layer, baked when merged.

``web/tests/editor-layerfx.test.ts`` checks the same cases on the website.
"""

from __future__ import annotations

from dataclasses import replace

from PIL import Image

from umc.editor import layerfx, ops
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.layerfx import LayerFx
from umc.editor.ora import read_ora, write_ora


def _doc(fx: LayerFx | None = None) -> Document:
    """A 60×60 transparent canvas with a 20×20 white square layer at (20, 20)."""
    doc = Document.new(60, 60)
    square = Layer("Square", Image.new("RGBA", (20, 20), (255, 255, 255, 255)), 20, 20, fx=fx)
    doc.layers.append(square)
    doc.active = 1
    return doc


def test_no_effects_draws_the_layer_as_before() -> None:
    plain = _doc().composite()
    assert _doc(LayerFx()).composite().tobytes() == plain.tobytes()  # all off = nothing changes
    assert plain.getpixel((30, 30)) == (255, 255, 255, 255)


def test_outline_surrounds_the_layer_by_its_width() -> None:
    fx = LayerFx(outline=True, outline_color="#ff0000", outline_width=3)
    img = _doc(fx).composite()
    assert img.getpixel((30, 30)) == (255, 255, 255, 255)  # the layer stays on top
    assert img.getpixel((18, 30)) == (255, 0, 0, 255)  # 2 px left of it: outline
    assert img.getpixel((17, 30)) == (255, 0, 0, 255)  # 3 px
    assert img.getpixel((16, 30))[3] == 0  # 4 px: past it
    assert img.getpixel((42, 30)) == (255, 0, 0, 255)  # right side too


def test_shadow_falls_in_its_direction() -> None:
    fx = LayerFx(shadow=True, shadow_angle=0, shadow_distance=10, shadow_blur=0, shadow_opacity=100)
    img = _doc(fx).composite()
    assert img.getpixel((45, 30)) == (0, 0, 0, 255)  # right of the square (angle 0 = right)
    assert img.getpixel((15, 30))[3] == 0  # nothing to the left
    soft = _doc(replace(fx, shadow_angle=90, shadow_blur=8, shadow_opacity=60)).composite()
    a = soft.getpixel((30, 45))[3]  # below (90 = down), soft and partly transparent
    assert 0 < a <= 153
    assert soft.getpixel((30, 15))[3] == 0


def test_glow_fades_outwards() -> None:
    img = _doc(LayerFx(glow=True, glow_color="#00ff00", glow_size=12)).composite()
    near, far = img.getpixel((19, 30)), img.getpixel((12, 30))
    assert near[1] == 255 and near[0] == 0 and near[3] > far[3] > 0


def test_overlay_tints_only_the_layer_s_pixels() -> None:
    img = _doc(LayerFx(overlay=True, overlay_color="#0000ff", overlay_opacity=50)).composite()
    assert img.getpixel((30, 30)) == (128, 128, 255, 255)
    assert img.getpixel((10, 10))[3] == 0


def test_effects_follow_the_mask_and_the_layer_opacity() -> None:
    doc = _doc(LayerFx(outline=True, outline_color="#ff0000", outline_width=2))
    mask = Image.new("L", (20, 20), 0)
    mask.paste(255, (0, 0, 10, 20))  # only the left half shows
    doc.layers[1] = replace(doc.layers[1], mask=mask, opacity=0.5)
    img = doc.composite()
    assert img.getpixel((31, 30)) == (255, 0, 0, 128)  # outline along the mask's edge, half opacity
    assert img.getpixel((35, 30))[3] == 0  # hidden half: nothing


def test_composite_of_a_part_matches_the_whole() -> None:
    fx = LayerFx(shadow=True, shadow_blur=6, glow=True, outline=True, overlay=True)
    doc = _doc(fx)
    whole = doc.composite()
    from umc.editor.geometry import Rect

    part = doc.composite(Rect(35, 35, 55, 50))
    assert part.tobytes() == whole.crop((35, 35, 55, 50)).tobytes()


def test_pasteboard_includes_the_effects() -> None:
    doc = _doc(LayerFx(shadow=True, shadow_distance=40, shadow_angle=0, shadow_blur=0))
    assert doc.pasteboard().x1 > doc.width


def test_merge_bakes_the_effects_into_pixels() -> None:
    doc = _doc(LayerFx(outline=True, outline_color="#ff0000", outline_width=3))
    before = doc.composite()
    cmd = ops.merge_down(doc)
    assert cmd is not None and len(doc.layers) == 1 and doc.layers[0].fx is None
    assert doc.composite().tobytes() == before.tobytes()
    cmd.undo(doc)
    assert doc.layers[1].fx is not None


def test_rasterize_bakes_and_duplicate_keeps() -> None:
    doc = _doc(LayerFx(outline=True, outline_width=3))
    before = doc.composite()
    assert ops.duplicate_layers(doc, [1]) is not None
    assert doc.layers[2].fx == doc.layers[1].fx
    doc.layers.pop()
    doc.active = 1
    cmd = ops.rasterize_layer(doc)
    assert cmd is not None and doc.layers[1].fx is None
    assert doc.layers[1].bounds.width == 26  # grown by the outline on both sides
    assert doc.composite().tobytes() == before.tobytes()


def test_transforms_keep_the_effects() -> None:
    doc = _doc(LayerFx(glow=True))
    ops.flip(doc, horizontal=True, layer_only=True)
    assert doc.layers[1].fx == LayerFx(glow=True)


def test_set_and_clear() -> None:
    doc = _doc()
    assert ops.set_layer_fx(doc, 1, LayerFx()) is None  # all off: nothing to change
    cmd = ops.set_layer_fx(doc, 1, LayerFx(glow=True), "Glow")
    assert cmd is not None and cmd.label == "Glow" and doc.layers[1].fx.glow
    clear = ops.clear_layer_fx(doc)
    assert clear is not None and doc.layers[1].fx is None
    clear.undo(doc)
    assert doc.layers[1].fx.glow


def test_a_run_of_window_changes_is_one_undo_step() -> None:
    doc = _doc()
    history = History(doc)
    for width in (2, 4, 6):
        fx = LayerFx(outline=True, outline_width=width)
        assert ops.tweak_layer_fx(doc, history, 1, fx, "Outline")
    assert doc.layers[1].fx.outline_width == 6
    history.undo()
    assert doc.layers[1].fx is None  # the whole run at once
    assert not history.can_undo
    # another window's change starts a new step
    history.redo()
    ops.tweak_layer_fx(doc, history, 1, replace(doc.layers[1].fx, glow=True), "Glow")
    history.undo()
    assert doc.layers[1].fx == LayerFx(outline=True, outline_width=6)


def test_window_values_round_trip() -> None:
    fresh = layerfx.window_values(None, "shadow")
    assert fresh["shadow"] is False and fresh["shadow_distance"] == 8
    fx = layerfx.with_values(None, {**fresh, "shadow": True})
    assert fx == LayerFx(shadow=True)
    assert layerfx.with_values(fx, {"shadow": False}) is None  # nothing left on


def test_ora_keeps_the_effects(tmp_path) -> None:
    fx = LayerFx(shadow=True, shadow_color="#123456", outline=True, outline_width=5)
    doc = _doc(fx)
    path = tmp_path / "fx.ora"
    write_ora(doc, path)
    back = read_ora(path)
    assert back.layers[1].fx == fx
    assert back.layers[1].image.size == (20, 20)  # its own pixels, not the effects
    assert LayerFx.from_dict({"glow": True, "glow_size": "7", "junk": 1, "glow_color": "bad"}) == (
        LayerFx(glow=True, glow_size=7)
    )


def test_an_effect_switched_off_keeps_its_settings() -> None:
    doc = _doc()
    history = History(doc)
    ops.tweak_layer_fx(doc, history, 1, LayerFx(shadow=True, shadow_distance=50), "Drop shadow")
    off = layerfx.with_values(doc.layers[1].fx, {"shadow": False})
    assert off is not None and off.shadow_distance == 50  # remembered while off
    ops.tweak_layer_fx(doc, history, 1, off, "Drop shadow")
    assert layerfx.window_values(doc.layers[1].fx, "shadow")["shadow_distance"] == 50
    assert doc.composite().tobytes() == _doc().composite().tobytes()  # but nothing is drawn


def test_a_run_back_to_its_start_leaves_nothing_to_redo() -> None:
    doc = _doc()
    history = History(doc)
    ops.tweak_layer_fx(doc, history, 1, LayerFx(glow=True), "Glow")
    ops.tweak_layer_fx(doc, history, 1, None, "Glow")
    assert doc.layers[1].fx is None and not history.can_redo


def test_files_cannot_ask_for_huge_effects() -> None:
    fx = LayerFx.from_dict({"outline": True, "outline_width": 10_000_000, "shadow_blur": -5})
    assert fx.outline_width == 50 and fx.shadow_blur == 0


def test_a_full_repaint_reuses_the_render_and_notices_strokes() -> None:
    doc = _doc(LayerFx(outline=True, outline_color="#ff0000", outline_width=2))
    first = doc.composite()
    doc.layers[1].image.putpixel((0, 0), (0, 0, 255, 255))  # painted in place
    second = doc.composite()
    assert second.getpixel((20, 20)) == (0, 0, 255, 255) and first.tobytes() != second.tobytes()


def test_older_files_keep_their_pixels(tmp_path) -> None:
    import zipfile

    from umc.editor.ora import read_ora
    from umc.editor.tools.base import ToolContext
    from umc.editor.tools.shapes import RectTool

    doc = Document.new(60, 60)
    t = RectTool()
    ctx = ToolContext((255, 0, 0, 255), (255, 255, 255, 255), t.options_cls())
    t.press(doc, 10, 10, ctx)
    t.move(doc, 40, 40, ctx)
    ops_cmd = t.release(doc, 40, 40, ctx)
    assert ops_cmd is not None
    doc.active_layer.image.putpixel((10, 10), (0, 255, 0, 100))  # a small retouch
    path = tmp_path / "old.ora"
    write_ora(doc, path)
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    import json

    spec = json.dumps(doc.active_layer.shape.to_dict()).replace('"', "&quot;")
    name = doc.active_layer.name
    files["stack.xml"] = files["stack.xml"].replace(  # what main wrote: the recipe, unchecked
        f'name="{name}"'.encode(), f'name="{name}" umc-shape="{spec}"'.encode()
    )
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    back = read_ora(path).layers[-1]
    assert back.image.getpixel((10, 10)) == (0, 255, 0, 100)  # the retouch is still there
