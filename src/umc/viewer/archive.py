"""Browsing an archive in the viewer (no Qt): its files as folders, the breadcrumb, what a
selection covers, and which names files dropped in get.

Folders are paths like ``photos/2024`` ("" = the archive's top). Folders that exist only
because a file's name has them (``a/b.txt`` without an ``a/`` entry) are made up here."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from umc.core.detect import COMPOUND_EXTS
from umc.tools.archive import Contents, norm_name
from umc.viewer import files as vf

ROOT = ""


@dataclass
class Node:
    """A file or folder inside the archive."""

    path: str  # full name inside: "photos/2024/a.jpg"
    is_dir: bool
    size: int = 0  # folders: everything below them
    packed: int | None = None
    modified: datetime | None = None
    encrypted: bool = False
    children: dict[str, Node] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def files(self) -> int:
        """How many files this is / holds."""
        if not self.is_dir:
            return 1
        return sum(c.files() for c in self.children.values())


class Tree:
    """The archive's contents as folders."""

    def __init__(self, contents: Contents) -> None:
        self.root = Node(ROOT, True)
        self.encrypted = contents.encrypted
        for e in contents.entries:
            name = norm_name(e.name)
            if not name:
                continue
            node = self._folder(parent_of(name)) if "/" in name else self.root
            existing = node.children.get(name.rsplit("/", 1)[-1])
            if e.is_dir:
                folder = self._folder(name)
                folder.modified = folder.modified or e.modified
            elif existing is None or not existing.is_dir:
                node.children[name.rsplit("/", 1)[-1]] = Node(
                    name, False, e.size, e.packed, e.modified, e.encrypted
                )
        self._sum(self.root)

    def _folder(self, path: str) -> Node:
        node = self.root
        if not path:
            return node
        walked = []
        for part in path.split("/"):
            walked.append(part)
            child = node.children.get(part)
            if child is None or not child.is_dir:
                child = Node("/".join(walked), True)
                node.children[part] = child
            node = child
        return node

    def _sum(self, node: Node) -> int:
        if node.is_dir:
            node.size = sum(self._sum(c) for c in node.children.values())
        return node.size

    def find(self, path: str) -> Node | None:
        node = self.root
        for part in [p for p in norm_name(path).split("/") if p]:
            node = node.children.get(part) if node.is_dir else None
            if node is None:
                return None
        return node

    def is_folder(self, path: str) -> bool:
        node = self.find(path)
        return node is not None and node.is_dir

    def listing(self, folder: str) -> list[Node]:
        """What ``folder`` holds: folders first, then files, each sorted like File Explorer."""
        node = self.find(folder)
        if node is None or not node.is_dir:
            return []
        return sorted(node.children.values(), key=lambda n: (not n.is_dir, vf.natural_key(n.name)))

    def file_count(self) -> int:
        return self.root.files()

    def total_size(self) -> int:
        return self.root.size

    def covered(self, paths: Iterable[str]) -> list[str]:
        """Every file a selection of files / folders takes (a folder = all files below it)."""
        out: list[str] = []
        seen: set[str] = set()

        def walk(node: Node) -> None:
            if node.is_dir:
                for child in node.children.values():
                    walk(child)
            elif node.path not in seen:
                seen.add(node.path)
                out.append(node.path)

        for p in paths:
            node = self.find(p)
            if node is not None:
                walk(node)
        return out

    def size_of(self, paths: Iterable[str]) -> int:
        return sum(n.size for p in paths if (n := self.find(p)) is not None)

    def encrypted_in(self, paths: Iterable[str]) -> bool:
        return any(n.encrypted for p in self.covered(paths) if (n := self.find(p)))


def parent_of(path: str) -> str:
    path = norm_name(path)
    return path.rsplit("/", 1)[0] if "/" in path else ROOT


def join(folder: str, name: str) -> str:
    folder = norm_name(folder)
    return f"{folder}/{name}" if folder else name


def breadcrumb(archive_name: str, folder: str) -> list[tuple[str, str]]:
    """(label, folder) from the archive's top down to ``folder``."""
    out = [(archive_name, ROOT)]
    walked: list[str] = []
    for part in [p for p in norm_name(folder).split("/") if p]:
        walked.append(part)
        out.append((part, "/".join(walked)))
    return out


def kind_of_entry(path: str) -> str:
    """The viewer's view kind for a file inside (by its name)."""
    return vf.kind_of_path(Path(path.rsplit("/", 1)[-1] or "x"))


def archive_stem(archive: Path) -> str:
    """``photos.tar.gz`` → ``photos``."""
    low = archive.name.lower()
    for compound in sorted(COMPOUND_EXTS, key=len, reverse=True):
        if low.endswith("." + compound):
            return archive.name[: -len(compound) - 1] or archive.name
    return archive.stem or archive.name


def extract_here_folder(archive: Path) -> Path:
    """ "Extract here": a new folder next to the archive, named after it (``photos (1)`` when
    ``photos`` is already there)."""
    stem = archive_stem(archive)
    candidate = archive.parent / stem
    n = 1
    while candidate.exists():
        candidate = archive.parent / f"{stem} ({n})"
        n += 1
    return candidate


def files_to_add(folder: str, local: Iterable[Path]) -> list[tuple[Path, str]]:
    """Files dropped into ``folder`` → (file on disk, name inside). A dropped folder brings
    everything in it (its own name kept on top); links and unreadable files are skipped."""
    out: list[tuple[Path, str]] = []
    for item in local:
        item = Path(item)
        try:
            if item.is_symlink():
                continue
            if item.is_dir():
                for base, dirs, names in os.walk(item):
                    dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
                    for name in sorted(names):
                        p = Path(base) / name
                        if p.is_file() and not p.is_symlink():
                            rel = p.relative_to(item.parent).as_posix()
                            out.append((p, join(folder, rel)))
            elif item.is_file():
                out.append((item, join(folder, item.name)))
        except OSError:
            continue
    return out


def clashes(tree: Tree, adds: list[tuple[Path, str]]) -> list[str]:
    """Names being added that the archive already has (as files)."""
    return [name for _p, name in adds if (n := tree.find(name)) is not None and not n.is_dir]


def folder_clash(tree: Tree, adds: list[tuple[Path, str]]) -> str | None:
    """A name being added where the archive has a folder, or below an existing file (can't be
    done: a name can't be a file and a folder at once)."""
    for _p, name in adds:
        node = tree.find(name)
        if node is not None and node.is_dir:
            return name
        parent = parent_of(name)
        while parent:
            n = tree.find(parent)
            if n is not None and not n.is_dir:
                return name
            parent = parent_of(parent)
    return None


def keep_both(tree: Tree, adds: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Added files whose name is taken get ``name (1).ext`` instead."""
    taken = {n for _p, n in adds} | set(tree.covered([ROOT]))
    out = []
    for path, name in adds:
        node = tree.find(name)
        if node is not None and not node.is_dir:
            folder, leaf = parent_of(name), name.rsplit("/", 1)[-1]
            stem, dot, ext = leaf.rpartition(".")
            if not dot or not stem:
                stem, ext = leaf, ""
            n = 1
            while True:
                candidate = join(folder, f"{stem} ({n}){'.' + ext if ext else ''}")
                if candidate not in taken and tree.find(candidate) is None:
                    break
                n += 1
            taken.add(candidate)
            name = candidate
        out.append((path, name))
    return out
