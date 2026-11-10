"""Undo / redo for the video editor: a stack of immutable project snapshots. Pure Python."""

from __future__ import annotations

from umc.videoedit.project import Project

MAX_STEPS = 300


class History:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.version = 0
        self._undo: list[tuple[str, Project]] = []  # (label, project before the step)
        self._redo: list[tuple[str, Project]] = []  # (label, project before the undone step)
        self._saved: Project = project

    def push(self, project: Project, label: str) -> None:
        if project is self.project:
            return
        self._undo.append((label, self.project))
        if len(self._undo) > MAX_STEPS:
            del self._undo[0]
        self._redo.clear()
        self.project = project
        self.version += 1

    def undo(self) -> None:
        if not self._undo:
            return
        label, before = self._undo.pop()
        self._redo.append((label, self.project))
        self.project = before
        self.version += 1

    def redo(self) -> None:
        if not self._redo:
            return
        label, after = self._redo.pop()
        self._undo.append((label, self.project))
        self.project = after
        self.version += 1

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    @property
    def dirty(self) -> bool:
        return self.project is not self._saved

    def mark_saved(self) -> None:
        self._saved = self.project
