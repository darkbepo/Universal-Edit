"""Undo / redo for the data editor: a stack of immutable document snapshots (port:
web/src/dataedit/history.ts)."""

from __future__ import annotations

from umc.dataedit.table import DataDoc

MAX_STEPS = 500


class History:
    def __init__(self, doc: DataDoc) -> None:
        self.doc = doc
        self.version = 0
        self._undo: list[tuple[str, DataDoc]] = []
        self._redo: list[tuple[str, DataDoc]] = []
        self._saved: DataDoc = doc
        self._merge: object = None  # the last step's merge key

    def push(self, doc: DataDoc, label: str, merge: object = None) -> None:
        """A new step; with the same ``merge`` key as the step before (typing in one cell) it
        becomes part of that step instead."""
        if doc == self.doc:
            return
        if merge is not None and merge == self._merge and self._undo:
            self.doc = doc
            self._redo.clear()
            self.version += 1
            return
        self._merge = merge
        self._undo.append((label, self.doc))
        if len(self._undo) > MAX_STEPS:
            del self._undo[0]
        self._redo.clear()
        self.doc = doc
        self.version += 1

    def undo(self) -> str:
        if not self._undo:
            return ""
        self._merge = None
        label, before = self._undo.pop()
        self._redo.append((label, self.doc))
        self.doc = before
        self.version += 1
        return label

    def redo(self) -> str:
        if not self._redo:
            return ""
        self._merge = None
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

    def mark_saved(self, doc: DataDoc | None = None) -> None:
        """``doc`` (default: the current one) is what the file now holds."""
        self._saved = self.doc if doc is None else doc
        self._merge = None

    def end_merge(self) -> None:
        """The next push starts a step of its own (e.g. another cell was chosen)."""
        self._merge = None
