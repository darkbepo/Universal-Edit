"""Undo / redo. Every change is applied first and then pushed as a Command.

Layer objects are treated as immutable by structural commands: an operation that changes a
layer's name, opacity, position or whole image replaces the Layer object, so a command can
keep plain references to the layer lists before and after. Pixel edits (brush, fill, ...)
change a layer's image in place and are recorded as patches of just the changed rectangle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.layer import Layer


def _image_bytes(img: Image.Image) -> int:
    return img.width * img.height * 4


def snapshot_layers(doc: Document) -> list[Layer]:
    """The current layer list (shared Layer objects; see the module docstring)."""
    return list(doc.layers)


class Command(ABC):
    label: str = ""

    @abstractmethod
    def undo(self, doc: Document) -> None: ...

    @abstractmethod
    def redo(self, doc: Document) -> None: ...

    @property
    def nbytes(self) -> int:
        return 0

    def repaint(self) -> Rect | None:
        """What to repaint after undo/redo, in canvas coordinates (None = everything)."""
        return None


class PatchCommand(Command):
    def __init__(
        self, label: str, layer_index: int, rect: Rect, before: Image.Image, after: Image.Image
    ) -> None:
        self.label = label
        self.layer_index = layer_index
        self.rect = rect  # layer coordinates
        self.before = before
        self.after = after
        self._origin = (0, 0)

    @classmethod
    def capture(
        cls, label: str, doc: Document, layer_index: int, rect: Rect, before_full: Image.Image
    ) -> PatchCommand | None:
        layer = doc.layers[layer_index]
        rect = rect.intersect(Rect(0, 0, layer.image.width, layer.image.height))
        if rect.empty:
            return None
        cmd = cls(label, layer_index, rect, before_full.crop(rect.box), layer.image.crop(rect.box))
        cmd._origin = (layer.x, layer.y)
        return cmd

    @classmethod
    def from_region(
        cls, label: str, doc: Document, layer_index: int, rect: Rect, before: Image.Image
    ) -> PatchCommand | None:
        """Like ``capture`` but ``before`` is already just ``rect`` (no whole-layer copy)."""
        layer = doc.layers[layer_index]
        if rect.empty:
            return None
        cmd = cls(label, layer_index, rect, before, layer.image.crop(rect.box))
        cmd._origin = (layer.x, layer.y)
        return cmd

    def _apply(self, doc: Document, img: Image.Image) -> None:
        layer = doc.layers[self.layer_index]
        layer.image.paste(img, (self.rect.x0, self.rect.y0))
        self._origin = (layer.x, layer.y)

    def undo(self, doc: Document) -> None:
        self._apply(doc, self.before)

    def redo(self, doc: Document) -> None:
        self._apply(doc, self.after)

    @property
    def nbytes(self) -> int:
        return _image_bytes(self.before) + _image_bytes(self.after)

    def repaint(self) -> Rect | None:
        return self.rect.offset(*self._origin)


def _unique_bytes(a: list[Layer], b: list[Layer]) -> int:
    """Bytes of the images that only one side holds (shared images cost nothing extra)."""
    ids_a = {id(layer.image) for layer in a}
    ids_b = {id(layer.image) for layer in b}
    seen: set[int] = set()
    total = 0
    for layer in [*a, *b]:
        key = id(layer.image)
        if key in seen or (key in ids_a and key in ids_b):
            continue
        seen.add(key)
        total += _image_bytes(layer.image)
    return total


class LayerListCommand(Command):
    def __init__(
        self,
        label: str,
        before: list[Layer],
        after: list[Layer],
        active_before: int,
        active_after: int,
        selected_before: set[int] | None = None,
        selected_after: set[int] | None = None,
    ) -> None:
        self.label = label
        self.before, self.after = list(before), list(after)
        self.active_before, self.active_after = active_before, active_after
        self.selected_before = set(selected_before or ())
        self.selected_after = set(selected_after or ())

    def undo(self, doc: Document) -> None:
        doc.layers = list(self.before)
        doc.active = self.active_before
        doc.selected = set(self.selected_before)

    def redo(self, doc: Document) -> None:
        doc.layers = list(self.after)
        doc.active = self.active_after
        doc.selected = set(self.selected_after)

    @property
    def nbytes(self) -> int:
        return _unique_bytes(self.before, self.after)


DocState = tuple[int, int, list[Layer]]


class DocumentStateCommand(Command):
    """Canvas size + layers (crop, resize, canvas size, rotate, flip)."""

    def __init__(self, label: str, before: DocState, after: DocState) -> None:
        self.label = label
        self.before = (before[0], before[1], list(before[2]))
        self.after = (after[0], after[1], list(after[2]))

    def _set(self, doc: Document, state: DocState) -> None:
        doc.width, doc.height, layers = state
        doc.layers = list(layers)
        doc.active = min(doc.active, len(doc.layers) - 1)
        doc.selection = None

    def undo(self, doc: Document) -> None:
        self._set(doc, self.before)

    def redo(self, doc: Document) -> None:
        self._set(doc, self.after)

    @property
    def nbytes(self) -> int:
        return _unique_bytes(self.before[2], self.after[2])


class SelectionCommand(Command):
    def __init__(self, before: Rect | None, after: Rect | None, label: str = "Select") -> None:
        self.label = label
        self.before, self.after = before, after

    def undo(self, doc: Document) -> None:
        doc.selection = self.before

    def redo(self, doc: Document) -> None:
        doc.selection = self.after


class History:
    def __init__(self, doc: Document, limit_bytes: int = 500 * 1024**2) -> None:
        self.doc = doc
        self.limit_bytes = limit_bytes
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        # Each pushed command gets a unique, ever-increasing token; a state is identified by the
        # token of the command on top of the undo stack (``_base`` when it is empty).
        self._next_token = 1
        self._tokens: dict[int, int] = {}  # id(command) → token
        self._base = 0
        self._saved: int | None = 0
        self._sync_dirty()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def _sync_dirty(self) -> None:
        self.doc.dirty = self._saved != self.position

    @property
    def position(self) -> int:
        """Token for the current state; pass it to ``mark_saved`` when a save started earlier."""
        return self._tokens[id(self._undo[-1])] if self._undo else self._base

    def mark_saved(self, position: int | None = None) -> None:
        self._saved = self.position if position is None else position
        self._sync_dirty()

    def push(self, cmd: Command | None) -> None:
        if cmd is None:
            return
        for old in self._redo:
            self._tokens.pop(id(old), None)
        self._redo.clear()
        self._undo.append(cmd)
        self._tokens[id(cmd)] = self._next_token
        self._next_token += 1
        total = sum(c.nbytes for c in self._undo)
        while len(self._undo) > 1 and total > self.limit_bytes:
            dropped = self._undo.pop(0)
            total -= dropped.nbytes
            self._base = self._tokens.pop(id(dropped))  # the bottom state is now "after dropped"
        self._sync_dirty()

    def undo(self) -> Rect | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo(self.doc)
        self._redo.append(cmd)
        self._sync_dirty()
        return cmd.repaint()

    def redo(self) -> Rect | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.redo(self.doc)
        self._undo.append(cmd)
        self._sync_dirty()
        return cmd.repaint()
