"""The code editor's keyboard shortcuts: one table drives the key bindings and the "?" window."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    id: str
    keys: tuple[str, ...]
    label: str
    group: str
    bound: bool = True  # False: handled by the text view itself (listed in the "?" window only)


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("new", ("Ctrl+N",), "New file", "Files"),
    Shortcut("open", ("Ctrl+O",), "Open…", "Files"),
    Shortcut("save", ("Ctrl+S",), "Save", "Files"),
    Shortcut("save_as", ("Ctrl+Shift+S",), "Save as…", "Files"),
    Shortcut("close", ("Ctrl+W",), "Close the tab", "Files"),
    Shortcut("next_tab", ("Ctrl+Tab",), "Next tab", "Files"),
    Shortcut("prev_tab", ("Ctrl+Shift+Tab",), "Previous tab", "Files"),
    Shortcut("find", ("Ctrl+F",), "Find", "Find"),
    Shortcut("replace", ("Ctrl+H",), "Find and replace", "Find"),
    Shortcut("find_next", ("F3",), "Next match", "Find"),
    Shortcut("find_prev", ("Shift+F3",), "Previous match", "Find"),
    Shortcut("go_to_line", ("Ctrl+G",), "Go to line…", "Find"),
    Shortcut("undo", ("Ctrl+Z",), "Undo", "Editing"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo", "Editing"),
    Shortcut("comment", ("Ctrl+/",), "Comment / uncomment the lines", "Editing"),
    Shortcut("indent", ("Tab",), "Indent the selected lines", "Editing", bound=False),
    Shortcut("outdent", ("Shift+Tab",), "Outdent the selected lines", "Editing", bound=False),
    Shortcut("wrap", ("Alt+Z",), "Word wrap on / off", "View"),
    Shortcut("zoom_in", ("Ctrl+=", "Ctrl++"), "Bigger text", "View"),
    Shortcut("zoom_out", ("Ctrl+-",), "Smaller text", "View"),
    Shortcut("zoom_reset", ("Ctrl+0",), "Normal text size", "View"),
    Shortcut("help", ("F1",), "Keyboard shortcuts", "View"),
)
