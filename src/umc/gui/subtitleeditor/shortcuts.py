"""The subtitle editor's keyboard shortcuts: one table drives the key bindings and the "?"
window (``web/src/ui/subtitleeditor/shortcuts.ts`` has the same ids)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    id: str
    keys: tuple[str, ...]
    label: str
    group: str


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("new", ("Ctrl+N",), "New subtitles", "Files"),
    Shortcut("open", ("Ctrl+O",), "Open…", "Files"),
    Shortcut("save", ("Ctrl+S",), "Save", "Files"),
    Shortcut("save_as", ("Ctrl+Shift+S",), "Save as…", "Files"),
    Shortcut("undo", ("Ctrl+Z",), "Undo", "Editing"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo", "Editing"),
    Shortcut("add", ("Ctrl+Return", "Ctrl+Enter"), "Add a subtitle after this one", "Editing"),
    Shortcut("delete", ("Alt+Delete",), "Delete the chosen subtitles", "Editing"),
    Shortcut("split", ("Alt+S",), "Split at the cursor (or in the middle)", "Editing"),
    Shortcut("join", ("Alt+J",), "Join the chosen subtitles (or with the next)", "Editing"),
    Shortcut("prev", ("Alt+Up",), "Previous subtitle", "Editing"),
    Shortcut("next", ("Alt+Down",), "Next subtitle", "Editing"),
    Shortcut("next_problem", ("F8",), "Next problem", "Editing"),
    Shortcut("find", ("Ctrl+F",), "Find", "Find"),
    Shortcut("replace", ("Ctrl+H",), "Find and replace", "Find"),
    Shortcut("find_next", ("F3",), "Next match", "Find"),
    Shortcut("find_prev", ("Shift+F3",), "Previous match", "Find"),
    Shortcut("play", ("Ctrl+Space",), "Play / pause the video", "Video"),
    Shortcut("set_start", ("Ctrl+[",), "Start = the video's position", "Video"),
    Shortcut("set_end", ("Ctrl+]",), "End = the video's position", "Video"),
    Shortcut("help", ("F1",), "Keyboard shortcuts", "View"),
)
