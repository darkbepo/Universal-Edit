"""The editor's tool rail (no Qt): groups of tools and actions, top to bottom.

Selection tools come first, as in other editors. Each item is a tool (selected, stays active)
or an action (runs at once; "menu" items open a short list). ``web/src/editor/rail.json`` is
the same data for the website (``tests/test_editor_rail.py`` checks they match; run this module
to print it). Icons are Tabler names from ``gui/icon_data.py`` / ``web/src/ui/icon_data.ts``.
"""

from __future__ import annotations

import json

# kind, id, label (tools: None = the tool's own label), icon
_T = "tool"
_A = "action"
_M = "menu"

RAIL: list[dict] = [
    {
        "group": "Select",
        "items": [
            (_T, "select", None, "marquee-2"),
            (_T, "ellipse_select", None, "circle-dashed"),
            (_T, "lasso", None, "lasso"),
            (_T, "polygon_lasso", None, "lasso-polygon"),
            (_T, "wand", None, "wand"),
            (_A, "select_all", "Select all", "select-all"),
            (_A, "deselect", "Deselect", "deselect"),
            (_A, "invert_selection", "Invert selection", "arrows-exchange"),
            (_A, "feather", "Feather…", "feather"),
        ],
    },
    {
        "group": "Move & crop",
        "items": [
            (_T, "move", None, "arrows-move"),
            (_T, "crop", None, "crop"),
            (_A, "crop_selection", "Crop to selection", "crop-1-1"),
        ],
    },
    {
        "group": "Paint",
        "items": [
            (_T, "brush", None, "brush"),
            (_T, "highlighter", None, "highlight"),
            (_T, "eraser", None, "eraser"),
            (_T, "mask_brush", None, "mask"),
            (_T, "fill", None, "bucket-droplet"),
            (_T, "gradient", None, "gradienter"),
            (_T, "picker", None, "color-picker"),
        ],
    },
    {
        "group": "Retouch",
        "items": [
            (_T, "clone", None, "rubber-stamp"),
            (_A, "content_fill", "Content-aware fill", "sparkles"),
            (_A, "remove_background", "Remove background", "background"),
        ],
    },
    {
        "group": "Shapes & text",
        "items": [
            (_T, "line", None, "line"),
            (_T, "arrow", None, "arrow-up-right"),
            (_T, "rect", None, "square"),
            (_T, "ellipse", None, "circle"),
            (_T, "text", None, "typography"),
            (_A, "stickers", "Stickers…", "sticker"),
        ],
    },
    {
        "group": "Adjust",
        "items": [
            (_A, "quick_fix", "Quick fix…", "adjustments"),
            (_A, "levels", "Levels…", "chart-histogram"),
            (_A, "curves", "Curves…", "chart-line"),
        ],
    },
    {
        "group": "Filters",
        "items": [
            (_A, "looks", "Looks…", "palette"),
            (_A, "blur", "Blur…", "blur"),
            (_A, "background_blur", "Background blur…", "aperture"),
            (_A, "sharpen", "Sharpen…", "focus-2"),
        ],
    },
    {
        "group": "Image",
        "items": [
            (_A, "resize", "Resize image…", "resize"),
            (_A, "canvas_size", "Canvas size…", "aspect-ratio"),
            (_M, "rotate", "Rotate & flip", "rotate-clockwise"),
        ],
    },
]

# the short list behind a "menu" item: (action id, label); None = a separator
MENUS: dict[str, list] = {
    "rotate": [
        ("rotate_right", "Rotate 90° right"),
        ("rotate_left", "Rotate 90° left"),
        ("rotate_180", "Rotate 180°"),
        ("flip_h", "Flip horizontal"),
        ("flip_v", "Flip vertical"),
        None,
        ("layer_rotate_right", "Layer: rotate 90° right"),
        ("layer_rotate_left", "Layer: rotate 90° left"),
        ("layer_rotate_180", "Layer: rotate 180°"),
        ("layer_flip_h", "Layer: flip horizontal"),
        ("layer_flip_v", "Layer: flip vertical"),
    ],
}

ICON_NAMES = sorted(
    {it[3] for g in RAIL for it in g["items"]} | {"chevrons-left", "chevrons-right"}
)


def items():
    """Every (kind, id, label, icon) in rail order."""
    return [it for g in RAIL for it in g["items"]]


def as_json() -> str:
    data = {
        "_note": "The editor's tool rail, generated from src/umc/editor/rail.py.",
        "groups": [
            {
                "group": g["group"],
                "items": [
                    {"kind": k, "id": i, "label": label, "icon": icon}
                    for k, i, label, icon in g["items"]
                ],
            }
            for g in RAIL
        ],
        "menus": {k: [list(e) if e else None for e in v] for k, v in MENUS.items()},
    }
    return json.dumps(data, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    print(as_json())
