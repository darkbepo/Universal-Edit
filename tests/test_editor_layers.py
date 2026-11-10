"""Several selected layers: reorder (arrows / drag), duplicate, delete, merge, show/hide."""

import pytest
from PIL import Image

from umc.editor import ops
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer


def _doc(n=5):
    """Layers L0 (bottom) … L{n-1} (top), each 4×4 in its own colour."""
    layers = [Layer(f"L{i}", Image.new("RGBA", (4, 4), (i * 40, 0, 0, 255))) for i in range(n)]
    return Document(4, 4, layers, active=0)


def names(doc):
    return [la.name for la in doc.layers]


def _state(doc):
    return (names(doc), doc.active, doc.selected_indices, [la.visible for la in doc.layers])


def test_selected_indices_always_contains_active():
    doc = _doc()
    assert doc.selected_indices == [0]
    ops.select_layers(doc, [1, 3])
    assert doc.selected_indices == [1, 3] and doc.active == 3
    ops.select_layers(doc, [1, 3], active=1)
    assert doc.active == 1
    ops.select_layer(doc, 4)
    assert doc.selected_indices == [4]


def test_move_to_gap_keeps_order_and_selection():
    doc = _doc()
    ops.select_layers(doc, [0, 2], active=2)
    ops.move_layers_to(doc, doc.selected_indices, 5)  # drag to the top
    assert names(doc) == ["L1", "L3", "L4", "L0", "L2"]
    assert doc.selected_indices == [3, 4] and doc.active == 4
    ops.move_layers_to(doc, doc.selected_indices, 0)  # and to the bottom
    assert names(doc) == ["L0", "L2", "L1", "L3", "L4"]


def test_move_to_gap_inside_selection_is_nothing():
    doc = _doc()
    ops.select_layers(doc, [1, 2])
    assert ops.move_layers_to(doc, doc.selected_indices, 2) is None
    assert ops.move_layers_to(doc, doc.selected_indices, 3) is None
    assert ops.move_layers_to(doc, doc.selected_indices, 1) is None


def test_move_by_one_moves_group_and_stops_at_the_top():
    doc = _doc()
    ops.select_layers(doc, [2, 4])
    ops.move_layers_by(doc, doc.selected_indices, 1)
    # L4 is already on top; L2 goes up one
    assert names(doc) == ["L0", "L1", "L3", "L2", "L4"]
    assert doc.selected_indices == [3, 4]
    assert ops.move_layers_by(doc, doc.selected_indices, 1) is None
    ops.move_layers_by(doc, doc.selected_indices, -1)
    assert names(doc) == ["L0", "L1", "L2", "L4", "L3"]


def test_duplicate_several():
    doc = _doc(3)
    ops.select_layers(doc, [0, 2], active=0)
    ops.duplicate_layers(doc, doc.selected_indices)
    assert names(doc) == ["L0", "L0 copy", "L1", "L2", "L2 copy"]
    assert doc.selected_indices == [1, 4] and doc.active == 1
    assert doc.layers[1].image is not doc.layers[0].image


def test_delete_several_keeps_one():
    doc = _doc()
    ops.select_layers(doc, [1, 3])
    ops.delete_layers(doc, doc.selected_indices)
    assert names(doc) == ["L0", "L2", "L4"] and doc.selected_indices == [0]
    assert ops.delete_layers(doc, [0, 1, 2]) is None


def test_merge_several():
    doc = Document(
        4,
        4,
        [
            Layer("bottom", Image.new("RGBA", (4, 4), (255, 0, 0, 255))),
            Layer("middle", Image.new("RGBA", (2, 2), (0, 255, 0, 255)), x=2, y=2),
            Layer("hidden", Image.new("RGBA", (4, 4), (0, 0, 255, 255)), visible=False),
            Layer("top", Image.new("RGBA", (1, 1), (0, 0, 255, 255))),
        ],
    )
    ops.merge_layers(doc, [1, 2, 3])
    assert names(doc) == ["bottom", "top"] and doc.active == 1
    merged = doc.layers[1]
    assert merged.bounds.box == (0, 0, 4, 4)
    assert merged.image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert merged.image.getpixel((3, 3)) == (0, 255, 0, 255)
    assert merged.image.getpixel((0, 3))[3] == 0  # the hidden layer was dropped
    assert ops.merge_layers(doc, [1]) is None


def test_show_hide_and_opacity_of_several():
    doc = _doc()
    ops.set_visible_layers(doc, [1, 2], False)
    assert [la.visible for la in doc.layers] == [True, False, False, True, True]
    assert ops.set_visible_layers(doc, [1, 2], False) is None
    ops.set_opacity_layers(doc, [0, 4], 0.5)
    assert [la.opacity for la in doc.layers] == [0.5, 1, 1, 1, 0.5]


@pytest.mark.parametrize(
    "fn",
    [
        lambda d: ops.move_layers_to(d, [0, 2], 5),
        lambda d: ops.move_layers_by(d, [1, 3], 1),
        lambda d: ops.duplicate_layers(d, [1, 3]),
        lambda d: ops.delete_layers(d, [1, 3]),
        lambda d: ops.merge_layers(d, [1, 3]),
        lambda d: ops.set_visible_layers(d, [1, 3], False),
    ],
)
def test_undo_restores_layers_and_selection(fn):
    doc = _doc()
    ops.select_layers(doc, [1, 3], active=1)
    h = History(doc)
    before = _state(doc)
    h.push(fn(doc))
    after = _state(doc)
    assert after != before
    h.undo()
    assert _state(doc) == before
    h.redo()
    assert _state(doc) == after


def test_solo_layer_shows_only_one_then_all_again():
    from PIL import Image

    from umc.editor import ops
    from umc.editor.document import Document
    from umc.editor.history import History
    from umc.editor.layer import Layer

    doc = Document(4, 4, [Layer(n, Image.new("RGBA", (4, 4))) for n in "abc"])
    doc.layers[2] = Layer("c", doc.layers[2].image, visible=False)
    h = History(doc)
    h.push(ops.solo_layer(doc, 2))
    assert [la.visible for la in doc.layers] == [False, False, True] and ops.is_solo(doc, 2)
    h.push(ops.solo_layer(doc, 2))
    assert [la.visible for la in doc.layers] == [True, True, True]
    h.undo()
    h.undo()
    assert [la.visible for la in doc.layers] == [True, True, False]
