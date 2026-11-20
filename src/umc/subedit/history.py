"""Undo / redo for the subtitle editor: a stack of immutable document snapshots."""

from __future__ import annotations

from umc.subedit.formats import SubDoc

MAX_STEPS = 500


class History:
    def __init__(self, doc: SubDoc) -> None:
        self.doc = doc
        self.version = 0
        self._undo: list[tuple[str, SubDoc]] = []
        self._redo: list[tuple[str, SubDoc]] = []
        self._saved: SubDoc = doc

    def push(self, doc: SubDoc, label: str) -> None:
        if doc == self.doc:
            return
        self._undo.append((label, self.doc))
        if len(self._undo) > MAX_STEPS:
            del self._undo[0]
        self._redo.clear()
        self.doc = doc
        self.version += 1

    def undo(self) -> str:
        if not self._undo:
            return ""
        label, before = self._undo.pop()
        self._redo.append((label, self.doc))
        self.doc = before
        self.version += 1
        return label

    def redo(self) -> str:
        if not self._redo:
            return ""
        label, after = self._redo.pop()
        self._undo.append((label, self.doc))
        self.doc = after
        self.version += 1
        return label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def dirty(self) -> bool:
        return self.doc != self._saved

    def mark_saved(self) -> None:
        self._saved = self.doc
