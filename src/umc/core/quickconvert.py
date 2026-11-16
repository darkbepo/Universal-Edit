"""Quick Convert (no Qt): which conversions the right-click "Quick Convert" menu offers for a
file type, and how to run one with the default settings, saving next to the original.

The menus are written into the Windows registry / Linux file-manager files ahead of time, so
they are worked out from the extension alone (``menu_for``). Left out: conversions that need
a choice the menu can't ask for (a password, page order, rotation, split ranges), ones made
for several files (slideshow), packing into an archive (that's for many files too), and a
"conversion" to the same format (PNG → PNG)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from umc.core import options as opts
from umc.core.detect import DetectedFile, canonical_ext, detect, family_of_ext
from umc.core.job import ConversionJob
from umc.core.registry import Target, targets_for

log = logging.getLogger(__name__)

MENU_TEXT = "Quick Convert"
OPEN_TEXT = "Open in Universal Edit…"
SKIP_PREFIXES = ("pack:", "slideshow:", "split:")
SKIP_IDS = frozenset({"pdf:rotate", "pdf:organize", "pdf:protect", "pdf:unlock"})
KEEP_SAME_FORMAT = frozenset({"pdf:compress", "ocr:pdf"})  # same extension, still a change
NEST_ABOVE = 10  # more items than this: one submenu per group (Image, Audio only, …)
INLINE_UP_TO = 2  # …except groups this small, whose items stay in the menu itself


@dataclass(frozen=True)
class MenuItem:
    target_id: str
    label: str  # "JPG", "Searchable PDF"
    group: str  # "Image", "Audio only"


@dataclass(frozen=True)
class Menu:
    items: tuple[MenuItem, ...]

    @property
    def nested(self) -> bool:
        return len(self.items) > NEST_ABOVE and len(self.groups()) > 1

    def groups(self) -> list[tuple[str, list[MenuItem]]]:
        """The items by group, in the order the converter lists them."""
        out: dict[str, list[MenuItem]] = {}
        for item in self.items:
            out.setdefault(item.group, []).append(item)
        return list(out.items())

    def entries(self) -> list[tuple[str, list[MenuItem]]]:
        """What the menu shows, top to bottom: ("", [item]) for an item, (group, items) for a
        submenu. Flat menus are all items; nested ones keep small groups inline."""
        if not self.nested:
            return [("", [i]) for i in self.items]
        out: list[tuple[str, list[MenuItem]]] = []
        for group, items in self.groups():
            if len(items) <= INLINE_UP_TO:
                out.extend(("", [i]) for i in items)
            else:
                out.append((group, items))
        return out

    @property
    def key(self) -> str:
        """A short name shared by every file type with exactly this menu."""
        text = "|".join(i.target_id for i in self.items)
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

    def flat_labels(self) -> list[tuple[MenuItem, str]]:
        """Each item with a label that still makes sense in one flat list ("Audio only: MP3")
        — for file managers whose menus can't nest."""
        return [
            (i, f"{group}: {i.label}" if group else i.label)
            for group, items in self.entries()
            for i in items
        ]


def _sample(ext: str) -> DetectedFile | None:
    ext = canonical_ext(ext)
    family = family_of_ext(ext)
    if family is None:
        return None
    return DetectedFile(Path(f"quick-convert-sample.{ext}"), family, ext)


def offered(target: Target, source_ext: str) -> bool:
    if target.id in SKIP_IDS or target.id.startswith(SKIP_PREFIXES):
        return False
    return not (target.ext == source_ext and target.id not in KEEP_SAME_FORMAT)


def menu_for(ext: str) -> Menu:
    """The Quick Convert menu for a file type (empty when nothing fits)."""
    sample = _sample(ext)
    if sample is None:
        return Menu(())
    try:
        targets = targets_for([sample])
    except Exception:  # a converter that needs the real file: no menu rather than a crash
        log.exception("quick convert: no targets for .%s", ext)
        return Menu(())
    return Menu(tuple(MenuItem(t.id, t.label, t.group) for t in targets if offered(t, sample.ext)))


def menus(extensions: list[str]) -> dict[str, Menu]:
    """{extension: its menu} for the extensions that have one."""
    out = {}
    for ext in extensions:
        menu = menu_for(ext)
        if menu.items:
            out[ext] = menu
    return out


def menu_for_files(paths: list[Path]) -> Menu:
    """The formats every one of ``paths`` can become (the first file's menu, in its order) —
    for the format picker that file managers without submenus open."""
    menus_ = [menu_for(Path(p).suffix.lstrip(".").lower()) for p in paths]
    if not menus_:
        return Menu(())
    common = set.intersection(*({i.target_id for i in m.items} for m in menus_))
    return Menu(tuple(i for i in menus_[0].items if i.target_id in common))


# --------------------------------------------------------------------------- running one
class QuickConvertError(Exception):
    """Plain-language reason a file can't be quick-converted (shown as it is)."""


def job_for(
    path: Path, target_id: str, on_progress: Callable[[float, str], None] | None = None
) -> ConversionJob:
    """A conversion of ``path`` to ``target_id`` with the default settings, written next to
    the file (``name (1).ext`` when that name is taken — the converters never overwrite)."""
    path = Path(path)
    if not path.is_file():
        raise QuickConvertError("It isn't there any more.")
    f = detect(path)
    if f is None:
        raise QuickConvertError("Universal Edit doesn't read this kind of file.")
    target = next((t for t in targets_for([f]) if t.id == target_id), None)
    if target is None:
        raise QuickConvertError("This file can't be converted to that format.")
    cls = target.converter.options_cls
    job = ConversionJob(files=[f], target=target, options=opts.build(cls, opts.defaults(cls)))
    if on_progress is not None:
        job.on_progress = on_progress
    return job


def label_of(target_id: str, path: Path) -> str:
    """The format's name for messages ("JPG"), else the id."""
    f = detect(Path(path)) if Path(path).is_file() else _sample(Path(path).suffix.lstrip("."))
    if f is not None:
        for t in targets_for([f]):
            if t.id == target_id:
                return t.label
    return target_id
