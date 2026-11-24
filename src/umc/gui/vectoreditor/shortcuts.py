"""The vector editor's keyboard shortcuts: one table drives the key bindings and the "?" window.
``canvas`` ones (single letters, Delete, arrows …) work while the drawing has the focus, so the
boxes of the Appearance panel keep their own keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    id: str
    keys: tuple[str, ...]
    label: str
    group: str
    canvas: bool = False


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("new", ("Ctrl+N",), "New drawing", "Files"),
    Shortcut("open", ("Ctrl+O",), "Open…", "Files"),
    Shortcut("save", ("Ctrl+S",), "Save", "Files"),
    Shortcut("save_as", ("Ctrl+Shift+S",), "Save as…", "Files"),
    Shortcut("export", ("Ctrl+E",), "Export as PDF, PNG …", "Files"),
    Shortcut("undo", ("Ctrl+Z",), "Undo", "Editing"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo", "Editing"),
    Shortcut("copy", ("Ctrl+C",), "Copy", "Editing", canvas=True),
    Shortcut("cut", ("Ctrl+X",), "Cut", "Editing", canvas=True),
    Shortcut("paste", ("Ctrl+V",), "Paste", "Editing", canvas=True),
    Shortcut("duplicate", ("Ctrl+D",), "Duplicate", "Editing", canvas=True),
    Shortcut(
        "delete",
        ("Delete", "Backspace"),
        "Delete (a point with the Node tool)",
        "Editing",
        canvas=True,
    ),
    Shortcut("select_all", ("Ctrl+A",), "Select everything", "Editing", canvas=True),
    Shortcut("deselect", ("Esc",), "Stop drawing / select nothing", "Editing", canvas=True),
    Shortcut("group", ("Ctrl+G",), "Group", "Arrange", canvas=True),
    Shortcut("ungroup", ("Ctrl+Shift+G",), "Ungroup", "Arrange", canvas=True),
    Shortcut("forward", ("PgUp",), "Bring forward", "Arrange", canvas=True),
    Shortcut("backward", ("PgDown",), "Send backward", "Arrange", canvas=True),
    Shortcut("front", ("Home",), "Bring to front", "Arrange", canvas=True),
    Shortcut("back", ("End",), "Send to back", "Arrange", canvas=True),
    Shortcut("to_path", ("Ctrl+Shift+C",), "Turn into a path", "Arrange", canvas=True),
    Shortcut("nudge_left", ("Left",), "Move left (Shift: 10)", "Arrange", canvas=True),
    Shortcut("nudge_right", ("Right",), "Move right (Shift: 10)", "Arrange", canvas=True),
    Shortcut("nudge_up", ("Up",), "Move up (Shift: 10)", "Arrange", canvas=True),
    Shortcut("nudge_down", ("Down",), "Move down (Shift: 10)", "Arrange", canvas=True),
    Shortcut("tool_select", ("V",), "Select tool", "Tools", canvas=True),
    Shortcut("tool_node", ("A",), "Node tool (move a path's points)", "Tools", canvas=True),
    Shortcut("tool_rect", ("R",), "Rectangle", "Tools", canvas=True),
    Shortcut("tool_ellipse", ("E",), "Ellipse", "Tools", canvas=True),
    Shortcut("tool_line", ("L",), "Line", "Tools", canvas=True),
    Shortcut(
        "tool_pen", ("P",), "Pen (click = corner, drag = curve, Enter ends)", "Tools", canvas=True
    ),
    Shortcut("tool_text", ("T",), "Text", "Tools", canvas=True),
    Shortcut("finish", ("Return", "Enter"), "Finish the pen path", "Tools", canvas=True),
    Shortcut("fit", ("Ctrl+0",), "Fit the page in the window", "View"),
    Shortcut("actual", ("Ctrl+1",), "Actual size", "View"),
    Shortcut("zoom_in", ("Ctrl+=", "Ctrl++"), "Zoom in", "View"),
    Shortcut("zoom_out", ("Ctrl+-",), "Zoom out", "View"),
    Shortcut("help", ("F1",), "Keyboard shortcuts", "Help"),
)
