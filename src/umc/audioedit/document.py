"""The sound being edited, its selection, and undo / redo (port: web/src/audioedit/document.ts).

Every edit is one or more *splices*: "replace frames [start, start + len(old)) with new". Undo
puts ``old`` back, so a cut stores only what was cut and a fade only the faded part. Edits of
the same length are written in place (no copy of the whole sound).

The selection is ``(start, end)`` in frames; ``start == end`` is just the cursor. Effects without
a selection (amplify, normalize, reverse) work on the whole sound; cutting needs a selection."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from umc.audioedit import ops

HISTORY_BYTES = 500 * 1024 * 1024  # like the image editor: oldest steps are dropped beyond this


@dataclass
class Splice:
    start: int
    old: np.ndarray
    new: np.ndarray


@dataclass
class Step:
    label: str
    splices: list[Splice]
    before: tuple[int, int]
    after: tuple[int, int]

    @property
    def nbytes(self) -> int:
        return sum(s.old.nbytes + s.new.nbytes for s in self.splices)


@dataclass
class AudioDoc:
    rate: int
    samples: np.ndarray  # float32, (channels, frames); replaced or written in place by edits
    selection: tuple[int, int] = (0, 0)
    revision: int = 0  # bumped by every change (the waveform cache follows it)
    _undo: list[Step] = field(default_factory=list)
    _redo: list[Step] = field(default_factory=list)
    _saved: Step | None = None
    _pending: list[Splice] | None = None

    def __post_init__(self) -> None:
        self.samples = np.array(self.samples, dtype=np.float32, copy=True, order="C")
        if self.samples.ndim != 2 or self.samples.shape[0] < 1:
            raise ValueError("samples must be (channels, frames)")

    # ------------------------------------------------------------ facts
    @property
    def channels(self) -> int:
        return self.samples.shape[0]

    @property
    def frames(self) -> int:
        return self.samples.shape[1]

    @property
    def duration(self) -> float:
        return self.frames / self.rate

    @property
    def dirty(self) -> bool:
        return (self._undo[-1] if self._undo else None) is not self._saved

    def mark_saved(self) -> None:
        self._saved = self._undo[-1] if self._undo else None

    @property
    def has_selection(self) -> bool:
        return self.selection[1] > self.selection[0]

    def span(self) -> tuple[int, int]:
        """The selection, or the whole sound when nothing is selected."""
        return self.selection if self.has_selection else (0, self.frames)

    def set_selection(self, start: int, end: int | None = None) -> None:
        end = start if end is None else end
        a, b = sorted((int(start), int(end)))
        self.selection = (max(0, min(a, self.frames)), max(0, min(b, self.frames)))

    def select_all(self) -> None:
        self.set_selection(0, self.frames)

    def copy(self) -> np.ndarray | None:
        a, b = self.selection
        return self.samples[:, a:b].copy() if b > a else None

    # ------------------------------------------------------------ history
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def _put(self, start: int, length: int, new: np.ndarray) -> None:
        """Replace ``length`` frames at ``start`` with ``new``."""
        if new.shape[1] == length:
            self.samples[:, start : start + length] = new
        else:
            self.samples = np.ascontiguousarray(
                np.concatenate(
                    (self.samples[:, :start], new, self.samples[:, start + length :]), axis=1
                ),
                dtype=np.float32,
            )
        self.revision += 1

    def _splice(self, start: int, end: int, new: np.ndarray) -> None:
        new = np.ascontiguousarray(ops.fit_channels(new, self.channels), dtype=np.float32)
        old = self.samples[:, start:end].copy()
        self._put(start, end - start, new)
        assert self._pending is not None
        self._pending.append(Splice(start, old, new))

    def _edit(self, label: str, fn, after: tuple[int, int] | None = None) -> bool:
        before = self.selection
        self._pending = []
        try:
            fn()
        finally:
            splices, self._pending = self._pending, None
        if not splices:
            return False
        if after is not None:
            self.set_selection(*after)
        self._undo.append(Step(label, splices, before, self.selection))
        self._redo.clear()
        total = sum(s.nbytes for s in self._undo)
        while len(self._undo) > 1 and total > HISTORY_BYTES:
            total -= self._undo.pop(0).nbytes
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        step = self._undo.pop()
        for s in reversed(step.splices):
            self._put(s.start, s.new.shape[1], s.old)
        self.selection = step.before
        self._redo.append(step)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        step = self._redo.pop()
        for s in step.splices:
            self._put(s.start, s.old.shape[1], s.new)
        self.selection = step.after
        self._undo.append(step)
        return True

    # ------------------------------------------------------------ edits (True = changed)
    def delete(self) -> bool:
        a, b = self.selection
        if b <= a:
            return False
        return self._edit("Delete", lambda: self._splice(a, b, self.samples[:, :0]), (a, a))

    def cut(self) -> np.ndarray | None:
        clip = self.copy()
        if clip is None:
            return None
        a, b = self.selection
        self._edit("Cut", lambda: self._splice(a, b, self.samples[:, :0]), (a, a))
        return clip

    def paste(self, clip: np.ndarray) -> bool:
        """Insert at the cursor, or replace the selection; the pasted part is selected."""
        if clip is None or clip.shape[1] == 0:
            return False
        a, b = self.selection
        n = clip.shape[1]
        return self._edit("Paste", lambda: self._splice(a, b, clip), (a, a + n))

    def trim(self) -> bool:
        """Keep only the selection."""
        a, b = self.selection
        if b <= a or (a == 0 and b == self.frames):
            return False

        def run() -> None:
            empty = self.samples[:, :0]
            if b < self.frames:
                self._splice(b, self.frames, empty)
            if a > 0:
                self._splice(0, a, empty)

        return self._edit("Trim", run, (0, b - a))

    def _effect(self, label: str, fn, need_selection: bool = False) -> bool:
        a, b = self.selection if need_selection else self.span()
        if b <= a:
            return False
        return self._edit(label, lambda: self._splice(a, b, fn(self.samples[:, a:b])))

    def silence(self) -> bool:
        return self._effect("Silence", lambda x: ops.silence(*x.shape), need_selection=True)

    def fade_in(self) -> bool:
        return self._effect("Fade in", lambda x: ops.fade(x, True), need_selection=True)

    def fade_out(self) -> bool:
        return self._effect("Fade out", lambda x: ops.fade(x, False), need_selection=True)

    def amplify(self, db: float) -> bool:
        if db == 0:
            return False
        return self._effect("Amplify", lambda x: ops.gain(x, db))

    def normalize(self, target_db: float = ops.NORMALIZE_DB) -> bool:
        a, b = self.span()
        db = ops.normalize_db(self.samples[:, a:b], target_db)
        if db is None or abs(db) < 1e-3:
            return False
        return self._effect("Normalize", lambda x: ops.gain(x, db))

    def reverse(self) -> bool:
        return self._effect("Reverse", ops.reverse)
