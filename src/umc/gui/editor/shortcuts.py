"""Every editor shortcut in one table: the page binds the keys from it and the "?" window lists it.

Tool letters come from the tools themselves (``Tool.shortcut``); everything else is here.
``keys`` are QKeySequence strings; the first one is the one shown in the list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    id: str
    keys: tuple[str, ...]
    label: str
    group: str


SHORTCUTS: tuple[Shortcut, ...] = (
    # Edit
    Shortcut("undo", ("Ctrl+Z",), "Undo", "Edit"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo", "Edit"),
    Shortcut("copy", ("Ctrl+C",), "Copy (selection, or the whole layer)", "Edit"),
    Shortcut("copy_merged", ("Ctrl+Shift+C",), "Copy what you see (all layers)", "Edit"),
    Shortcut("cut", ("Ctrl+X",), "Cut", "Edit"),
    Shortcut("paste", ("Ctrl+V",), "Paste as a new layer", "Edit"),
    Shortcut(
        "delete",
        ("Delete", "Backspace"),
        "Clear the selection · in the layers list: delete layers",
        "Edit",
    ),
    Shortcut("save", ("Ctrl+S",), "Save", "Edit"),
    Shortcut("save_as", ("Ctrl+Shift+S",), "Save as…", "Edit"),
    # Selection
    Shortcut("select_all", ("Ctrl+A",), "Select all", "Selection"),
    Shortcut("deselect", ("Ctrl+D",), "Deselect", "Selection"),
    Shortcut("invert_selection", ("Ctrl+Shift+I", "Shift+F7"), "Invert the selection", "Selection"),
    Shortcut("feather", ("Shift+F6", "Ctrl+Alt+D"), "Feather (soften) the selection…", "Selection"),
    Shortcut("apply", ("Enter", "Return"), "Apply the crop · finish a polygon", "Selection"),
    # Layers
    Shortcut("new_layer", ("Ctrl+Shift+N",), "New layer", "Layers"),
    Shortcut("duplicate", ("Ctrl+J",), "Duplicate layers", "Layers"),
    Shortcut("merge", ("Ctrl+E",), "Merge down / merge the selected layers", "Layers"),
    Shortcut("layer_up", ("Ctrl+]",), "Move layers up", "Layers"),
    Shortcut("layer_down", ("Ctrl+[",), "Move layers down", "Layers"),
    Shortcut("layer_top", ("Ctrl+Shift+]",), "Move layers to the top", "Layers"),
    Shortcut("layer_bottom", ("Ctrl+Shift+[",), "Move layers to the bottom", "Layers"),
    Shortcut("select_all_layers", ("Ctrl+Alt+A",), "Select all layers", "Layers"),
    # View
    Shortcut("zoom_in", ("Ctrl++", "Ctrl+="), "Zoom in", "View"),
    Shortcut("zoom_out", ("Ctrl+-",), "Zoom out", "View"),
    Shortcut("fit", ("Ctrl+0",), "Fit to window", "View"),
    Shortcut("actual", ("Ctrl+1",), "Actual pixels (100 %)", "View"),
    Shortcut("help", ("?", "F1"), "Show this list", "View"),
    # Tools (besides the tool letters)
    Shortcut("swap_colours", ("X",), "Swap colours", "Tools"),
    Shortcut("smaller", ("[",), "Smaller brush", "Tools"),
    Shortcut("bigger", ("]",), "Bigger brush", "Tools"),
)

# Shown in the list but handled elsewhere (canvas keys and mouse), so not bound by the page.
INFO: tuple[tuple[str, str, str], ...] = (
    (
        "Shift / Alt + select",
        "Add to / take from the selection (both: keep the overlap)",
        "Selection",
    ),
    ("Double-click", "Finish a polygon lasso", "Selection"),
    ("Arrow keys", "Move tool: nudge 1 px (Shift: 10 px)", "Tools"),
    ("Arrow keys ", "Selection tools: move the selection outline (Shift: 10 px)", "Tools"),
    ("Shift + drag", "Move tool: keep to one direction · shapes: keep square", "Tools"),
    ("Esc", "Cancel the current drag / crop", "Tools"),
    ("Space + drag", "Pan (also the middle mouse button)", "View"),
    ("Mouse wheel", "Zoom around the cursor", "View"),
)

GROUPS = ("Edit", "Selection", "Layers", "View", "Tools")
