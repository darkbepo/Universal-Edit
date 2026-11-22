"""The data editor's keyboard shortcuts: one table drives the key bindings and the "?" window.
``table`` ones are the grid's own keys (handled while the grid has the focus, so the edit box
and the find bar keep their usual copy / paste / delete)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    id: str
    keys: tuple[str, ...]
    label: str
    group: str
    table: bool = False


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("new", ("Ctrl+N",), "New table", "Files"),
    Shortcut("open", ("Ctrl+O",), "Open…", "Files"),
    Shortcut("save", ("Ctrl+S",), "Save", "Files"),
    Shortcut("save_as", ("Ctrl+Shift+S",), "Save as…", "Files"),
    Shortcut("undo", ("Ctrl+Z",), "Undo", "Editing"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo", "Editing"),
    Shortcut("add_row", ("Ctrl+Return", "Ctrl+Enter"), "Add a row below", "Editing"),
    Shortcut(
        "add_column",
        ("Ctrl+Shift+Return", "Ctrl+Shift+Enter"),
        "Add a column to the right",
        "Editing",
    ),  # fmt: skip
    Shortcut("delete_rows", ("Ctrl+-",), "Delete the chosen rows", "Editing"),
    Shortcut("edit_box", ("F4",), "Edit the cell in the box above the table", "Editing"),
    Shortcut("edit_cell", ("F2",), "Edit the cell in place", "Editing", table=True),
    Shortcut("clear", ("Delete",), "Empty the chosen cells", "Editing", table=True),
    Shortcut("copy", ("Ctrl+C",), "Copy the chosen cells", "Editing", table=True),
    Shortcut("cut", ("Ctrl+X",), "Cut the chosen cells", "Editing", table=True),
    Shortcut(
        "paste",
        ("Ctrl+V",),
        "Paste (rows and columns are added when needed)",
        "Editing",
        table=True,
    ),  # fmt: skip
    Shortcut("find", ("Ctrl+F",), "Find", "Find"),
    Shortcut("replace", ("Ctrl+H",), "Find and replace", "Find"),
    Shortcut("find_next", ("F3",), "Next match", "Find"),
    Shortcut("find_prev", ("Shift+F3",), "Previous match", "Find"),
    Shortcut("help", ("F1",), "Keyboard shortcuts", "View"),
)
