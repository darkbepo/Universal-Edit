"""The vector editor's document and its edits (port: ``doc.ts``).

A :class:`VectorDoc` wraps the source-keeping XML tree. Elements are addressed by *paths*:
tuples of element-child indexes from the root (the root itself is ``()``). Every edit is a pure
function returning a new document; only the elements it changes lose their source text, so the
rest of the file saves exactly as it was read.

Coordinates given to edits are the root's user units (the ``viewBox`` space), which is what the
canvas works in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from umc.vectoredit import pathdata, shapes, xmlsrc
from umc.vectoredit.geom import IDENTITY, Box, Matrix, num, scale, translate
from umc.vectoredit.shapes import CONTAINERS, HIDDEN, SHAPES, kind
from umc.vectoredit.xmlsrc import Element, Node, Text

Path = tuple[int, ...]

DEFAULT_SIZE = (300.0, 150.0)  # what browsers use for an <svg> with no size at all
LAYER_ATTR = "inkscape:groupmode"


class VectorError(ValueError):
    """Why something can't be done; the message is shown as it is."""


@dataclass(frozen=True)
class VectorDoc:
    xml: xmlsrc.Document

    @property
    def root(self) -> Element:
        return self.xml.root


@dataclass(frozen=True)
class Item:
    """One entry of the layers list."""

    path: Path
    kind: str
    name: str  # the id, or the kind
    depth: int
    hidden: bool
    container: bool


def parse(text: str) -> VectorDoc:
    try:
        xml = xmlsrc.parse(text)
    except xmlsrc.XmlError as e:
        raise VectorError(f"This isn't a readable SVG file: {e}") from e
    if kind(xml.root) != "svg":
        raise VectorError("This XML file isn't an SVG drawing.")
    return VectorDoc(xml)


def write(doc: VectorDoc) -> str:
    return xmlsrc.write(doc.xml)


# ------------------------------------------------------------------------------- reading


def get(doc: VectorDoc, path: Path) -> Element:
    el = doc.root
    for k in path:
        el = el.elements[k]
    return el


def _slot(parent: Element, k: int) -> int:
    """Index in ``parent.children`` of its ``k``-th element."""
    seen = -1
    for i, c in enumerate(parent.children):
        if isinstance(c, Element):
            seen += 1
            if seen == k:
                return i
    raise IndexError(k)


def canvas(doc: VectorDoc) -> tuple[float, float, Box]:
    """(width, height) the drawing is shown at, and the ``viewBox`` it shows."""
    root = doc.root
    vb = shapes.numbers(root.get("viewBox"))
    view = (
        Box(vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3])
        if len(vb) == 4 and vb[2] > 0 and vb[3] > 0
        else None
    )
    w, h = shapes.length(root.get("width")), shapes.length(root.get("height"))
    if view is None:
        w = w or DEFAULT_SIZE[0]
        h = h or DEFAULT_SIZE[1]
        return w, h, Box(0, 0, w, h)
    if w is None and h is None:
        return view.width, view.height, view
    if w is None:
        w = h * view.width / view.height  # type: ignore[operator]
    if h is None:
        h = w * view.height / view.width
    return w, h, view


def is_layer(el: Element) -> bool:
    return kind(el) == "g" and el.get(LAYER_ATTR) == "layer"


def editable(el: Element) -> bool:
    k = kind(el)
    return k in SHAPES or k in CONTAINERS


def items(doc: VectorDoc) -> list[Item]:
    """Everything the layers list shows, in document order (bottom first)."""
    out: list[Item] = []

    def walk(el: Element, path: Path, depth: int) -> None:
        for k, child in enumerate(el.elements):
            if not editable(child):
                continue
            p = (*path, k)
            container = kind(child) in CONTAINERS
            name = child.get("inkscape:label") or child.get("id") or kind(child)
            out.append(Item(p, kind(child), name, depth, shapes.hidden(child), container))
            if container:
                walk(child, p, depth + 1)

    walk(doc.root, (), 0)
    return out


def parent_matrix(doc: VectorDoc, path: Path) -> Matrix:
    """Root user units → the coordinates ``path``'s element is written in (its parent's)."""
    m = IDENTITY
    el = doc.root
    for k in path[:-1]:
        el = el.elements[k]
        m = m @ shapes.own_transform(el)
    return m


def by_id(doc: VectorDoc) -> dict[str, Element]:
    """Every element with an id (the first one wins), worked out once per document."""
    cached = doc.__dict__.get("_by_id")
    if cached is None:
        cached = {}

        def walk(el: Element) -> None:
            i = el.get("id")
            if i is not None and i not in cached:
                cached[i] = el
            for c in el.elements:
                walk(c)

        walk(doc.root)
        object.__setattr__(doc, "_by_id", cached)
    return cached


def bounds(doc: VectorDoc, path: Path) -> Box | None:
    """The element's box in root user units."""
    b = shapes.bounds(get(doc, path), by_id(doc).get)
    return None if b is None else b.transformed(parent_matrix(doc, path))


def selection_bounds(doc: VectorDoc, paths: list[Path]) -> Box | None:
    box: Box | None = None
    for p in paths:
        b = bounds(doc, p)
        if b is not None:
            box = b if box is None else box.union(b)
    return box


def _pickable(doc: VectorDoc, el: Element, path: Path) -> list[Path]:
    """Paths a click can choose inside ``el``: its children, and the children of layers."""
    out: list[Path] = []
    for k, child in enumerate(el.elements):
        if not editable(child) or shapes.hidden(child):
            continue
        p = (*path, k)
        if is_layer(child):
            out += _pickable(doc, child, p)
        else:
            out.append(p)
    return out


def hit(doc: VectorDoc, x: float, y: float, inside: Path = (), pad: float = 0.0) -> Path | None:
    """The topmost thing whose box holds (x, y): a direct child of ``inside`` (layers are looked
    through), so clicking a group's part picks the group."""
    for p in reversed(_pickable(doc, get(doc, inside), inside)):
        b = bounds(doc, p)
        if b is not None and b.x0 - pad <= x <= b.x1 + pad and b.y0 - pad <= y <= b.y1 + pad:
            return p
    return None


def in_box(doc: VectorDoc, box: Box, inside: Path = ()) -> list[Path]:
    """Everything (as :func:`hit` would pick it) lying entirely inside ``box``."""
    out = []
    for p in _pickable(doc, get(doc, inside), inside):
        b = bounds(doc, p)
        if (
            b is not None
            and box.x0 <= b.x0
            and box.y0 <= b.y0
            and b.x1 <= box.x1
            and b.y1 <= box.y1
        ):
            out.append(p)
    return out


def ids(doc: VectorDoc) -> set[str]:
    return set(by_id(doc))


def unique_id(taken: set[str], base: str) -> str:
    base = re.sub(r"-?[0-9]+\Z", "", base) or "shape"
    n = 1
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


# ------------------------------------------------------------------------------- rebuilding


def replace_at(doc: VectorDoc, path: Path, new: Element) -> VectorDoc:
    """``doc`` with the element at ``path`` replaced (the same ``doc`` if it's unchanged)."""
    if get(doc, path) is new:
        return doc

    def down(el: Element, rest: Path) -> Element:
        if not rest:
            return new
        kids = list(el.children)
        i = _slot(el, rest[0])
        kids[i] = down(kids[i], rest[1:])  # type: ignore[arg-type]
        return el.with_children(kids)

    return VectorDoc(doc.xml.with_root(down(doc.root, path)))


def _update(doc: VectorDoc, path: Path, change) -> VectorDoc:
    return replace_at(doc, path, change(get(doc, path)))


def _indent_of(parent: Element, i: int) -> str:
    """The whitespace written before child ``i`` (or before the last element child)."""
    if i > 0 and isinstance(parent.children[i - 1], Text):
        raw = parent.children[i - 1].raw
        if raw.strip(" \t\r\n") == "":
            return raw
    return ""


def _remove_slots(kids: list[Node], slots: list[int]) -> list[Node]:
    """Drop the children at ``slots`` and the whitespace just before each."""
    drop = set(slots)
    for i in slots:
        before = kids[i - 1] if i > 0 else None
        if isinstance(before, Text) and before.raw.strip(" \t\r\n") == "":
            drop.add(i - 1)
    return [c for j, c in enumerate(kids) if j not in drop]


def _siblings(paths: list[Path]) -> Path:
    parents = {p[:-1] for p in paths}
    if len(parents) != 1:
        raise VectorError("Pick things that sit in the same group.")
    return next(iter(parents))


def _check(doc: VectorDoc, paths: list[Path]) -> list[Path]:
    if not paths:
        raise VectorError("Nothing is selected.")
    for p in paths:
        if not p:
            raise VectorError("The drawing itself can't be changed this way.")
        try:
            get(doc, p)
        except IndexError:
            raise VectorError("That part of the drawing isn't there any more.") from None
    return sorted(set(paths))


# ------------------------------------------------------------------------------- edits


def transform(doc: VectorDoc, paths: list[Path], m: Matrix) -> VectorDoc:
    """Apply ``m`` (in root user units) to each element."""
    for p in _check(doc, paths):
        pm = parent_matrix(doc, p)
        local = m if pm.is_identity() else pm.inverse() @ m @ pm
        doc = _update(doc, p, lambda el, local=local: shapes.transformed(el, local))
    return doc


def move(doc: VectorDoc, paths: list[Path], dx: float, dy: float) -> VectorDoc:
    return transform(doc, paths, translate(dx, dy))


def resize(doc: VectorDoc, paths: list[Path], box: Box) -> VectorDoc:
    """Scale the selection so its box becomes ``box`` (a side of 0 is refused)."""
    old = selection_bounds(doc, _check(doc, paths))
    if old is None:
        raise VectorError("The selection has no size.")
    if box.width <= 0 or box.height <= 0:
        raise VectorError("Width and height must be more than 0.")
    sx = box.width / old.width if old.width else 1.0
    sy = box.height / old.height if old.height else 1.0
    m = translate(box.x0, box.y0) @ scale(sx, sy) @ translate(-old.x0, -old.y0)
    return transform(doc, paths, m)


def set_style(doc: VectorDoc, paths: list[Path], name: str, value: str | None) -> VectorDoc:
    for p in _check(doc, paths):
        doc = _update(doc, p, lambda el: shapes.style_set(el, name, value))
    return doc


def set_hidden(doc: VectorDoc, paths: list[Path], hide: bool) -> VectorDoc:
    return set_style(doc, paths, "display", "none" if hide else None)


def rename(doc: VectorDoc, path: Path, new_id: str) -> VectorDoc:
    new_id = new_id.strip()
    el = get(doc, path)
    if new_id == (el.get("id") or ""):
        return doc
    if new_id and not re.fullmatch(r"[A-Za-z_][\w.\-]*", new_id, re.ASCII):
        raise VectorError("A name starts with a letter and has no spaces.")
    if new_id and new_id in ids(doc):
        raise VectorError(f"“{new_id}” is already used.")
    return replace_at(doc, path, el.set("id", new_id or None))


def delete(doc: VectorDoc, paths: list[Path]) -> VectorDoc:
    paths = _check(doc, paths)
    # Deepest / last first, so the paths still to do keep pointing at the same elements.
    for p in sorted(paths, key=lambda p: (len(p), p), reverse=True):
        if any(p[: len(q)] == q and p != q for q in paths):
            continue  # inside another deleted element
        parent = get(doc, p[:-1])
        kids = _remove_slots(list(parent.children), [_slot(parent, p[-1])])
        doc = replace_at(doc, p[:-1], parent.with_children(kids))
    return doc


def _reid(el: Element, taken: set[str]) -> Element:
    old = el.get("id")
    if old is not None:
        new = unique_id(taken, old)
        taken.add(new)
        el = el.set("id", new)
    kids = [_reid(c, taken) if isinstance(c, Element) else c for c in el.children]
    return el.with_children(kids)


def duplicate(doc: VectorDoc, paths: list[Path]) -> tuple[VectorDoc, list[Path]]:
    """Copies right above each original (same parent); returns the copies' paths."""
    paths = _check(doc, paths)
    taken = ids(doc)
    new_paths: list[Path] = []
    for p in sorted(paths, reverse=True):
        parent = get(doc, p[:-1])
        i = _slot(parent, p[-1])
        copy = _reid(parent.children[i], taken)  # type: ignore[arg-type]
        indent = _indent_of(parent, i)
        kids = list(parent.children)
        kids[i + 1 : i + 1] = ([Text(indent)] if indent else []) + [copy]
        doc = replace_at(doc, p[:-1], parent.with_children(kids))
    # Each copy sits right after its original, below the copies of the ones before it.
    for p in paths:
        before = sum(1 for q in paths if q[:-1] == p[:-1] and q[-1] < p[-1])
        new_paths.append((*p[:-1], p[-1] + before + 1))
    return doc, new_paths


def reorder(doc: VectorDoc, path: Path, where: str) -> tuple[VectorDoc, Path]:
    """``where`` = "forward", "backward", "front" or "back" among the element's siblings.
    Elements trade places; the whitespace between them stays where it was."""
    if not path:
        raise VectorError("The drawing itself can't be moved.")
    parent = get(doc, path[:-1])
    els = list(parent.elements)
    k = path[-1]
    n = len(els)
    target = {"forward": min(k + 1, n - 1), "backward": max(k - 1, 0), "front": n - 1, "back": 0}[
        where
    ]
    if target == k:
        return doc, path
    el = els.pop(k)
    els.insert(target, el)
    kids = list(parent.children)
    slots = [i for i, c in enumerate(kids) if isinstance(c, Element)]
    for slot, new in zip(slots, els, strict=True):
        kids[slot] = new
    return replace_at(doc, path[:-1], parent.with_children(kids)), (*path[:-1], target)


def group(doc: VectorDoc, paths: list[Path]) -> tuple[VectorDoc, Path]:
    """Put the elements into a new ``<g>`` where the topmost of them was."""
    paths = _check(doc, paths)
    parent_path = _siblings(paths)
    parent = get(doc, parent_path)
    chosen = [p[-1] for p in paths]
    kids = list(parent.children)
    slots = [_slot(parent, k) for k in chosen]
    members = [kids[s] for s in slots]
    ws = _indent_of(parent, slots[-1])  # e.g. "\n  ": the parts go one step deeper
    inner: list[Node] = []
    for mbr in members:
        inner += [Text(ws + "  "), mbr] if ws else [mbr]
    if ws:
        inner.append(Text(ws))
    g = xmlsrc.element("g", [("id", unique_id(ids(doc), "group"))], inner)
    kids[slots[-1]] = g
    kids = _remove_slots(kids, slots[:-1])
    new_parent = parent.with_children(kids)
    k = next(j for j, c in enumerate(new_parent.elements) if c is g)
    return replace_at(doc, parent_path, new_parent), (*parent_path, k)


def ungroup(doc: VectorDoc, path: Path) -> tuple[VectorDoc, list[Path]]:
    """Put a group's parts in its place: its transform and inherited style go down to them."""
    g = get(doc, path)
    if kind(g) not in CONTAINERS or not path:
        raise VectorError("Only a group can be ungrouped.")
    m = shapes.own_transform(g)
    passed = [(n, shapes.style_get(g, n)) for n in shapes.INHERITED if shapes.style_get(g, n)]
    opacity = shapes.length(shapes.style_get(g, "opacity"))
    members: list[Node] = []
    for c in g.children:
        if isinstance(c, Element) and editable(c):
            el = shapes.transformed(c, m)
            for name, value in passed:
                if shapes.style_get(el, name) is None:
                    el = shapes.style_set(el, name, value)
            if opacity is not None and opacity != 1:
                own = shapes.length(shapes.style_get(el, "opacity"))
                el = shapes.style_set(el, "opacity", num(opacity * (1.0 if own is None else own)))
            members.append(el)
        elif isinstance(c, Element) and kind(c) not in HIDDEN:
            members.append(shapes.transformed(c, m))
        elif isinstance(c, Element) or not isinstance(c, Text):
            members.append(c)  # <defs>, <title>, comments: kept with the parts
    parent = get(doc, path[:-1])
    kids = list(parent.children)
    i = _slot(parent, path[-1])
    indent = _indent_of(parent, i)
    spaced: list[Node] = []
    for j, mbr in enumerate(members):
        if j and indent:
            spaced.append(Text(indent))
        spaced.append(mbr)
    kids[i : i + 1] = spaced
    new_parent = parent.with_children(kids)
    firsts = [j for j, c in enumerate(new_parent.elements) if any(c is mm for mm in members)]
    return replace_at(doc, path[:-1], new_parent), [(*path[:-1], j) for j in firsts]


def add(doc: VectorDoc, el: Element, parent: Path = ()) -> tuple[VectorDoc, Path]:
    """Append ``el`` on top of ``parent``'s children (indented like the last one)."""
    host = get(doc, parent)
    kids = list(host.children)
    last = max((i for i, c in enumerate(kids) if isinstance(c, Element)), default=-1)
    if last >= 0:
        indent = _indent_of(host, last)
        kids[last + 1 : last + 1] = ([Text(indent)] if indent else []) + [el]
    else:  # nothing drawn yet: one step deeper than the closing tag
        tail = kids[-1] if kids and isinstance(kids[-1], Text) else None
        ws = tail.raw if tail is not None and tail.raw.strip(" \t\r\n") == "" else ""
        kids = [*kids[:-1], Text(ws + "  "), el, Text(ws)] if ws else [*kids, el]
    host = host.with_children(kids)
    k = next(j for j, c in enumerate(host.elements) if c is el)
    return replace_at(doc, parent, host), (*parent, k)


def set_text(doc: VectorDoc, path: Path, text: str) -> VectorDoc:
    el = get(doc, path)
    if kind(el) != "text":
        raise VectorError("Only text can be typed into.")
    if el.text() == text:
        return doc
    return replace_at(doc, path, el.with_children([Text(xmlsrc.escape_text(text))]))


def set_attr(doc: VectorDoc, path: Path, name: str, value: str | None) -> VectorDoc:
    """A plain attribute change (the Node tool's ``d``, a rectangle's corner radius …)."""
    return _update(doc, path, lambda el: el.set(name, value))


def new_shape(kind_: str, box: Box, style: list[tuple[str, str]], taken: set[str]) -> Element:
    """A rectangle / ellipse / line drawn from ``box`` (a line goes corner to corner)."""
    ident = unique_id(taken, {"rect": "rect", "ellipse": "ellipse", "line": "line"}[kind_])
    if kind_ == "rect":
        geo = [("x", box.x0), ("y", box.y0), ("width", box.width), ("height", box.height)]
    elif kind_ == "ellipse":
        geo = [
            ("cx", (box.x0 + box.x1) / 2),
            ("cy", (box.y0 + box.y1) / 2),
            ("rx", box.width / 2),
            ("ry", box.height / 2),
        ]
    else:
        geo = [("x1", box.x0), ("y1", box.y0), ("x2", box.x1), ("y2", box.y1)]
    attrs = [("id", ident), *((k, num(v)) for k, v in geo)]
    if style:
        attrs.append(("style", shapes.write_style(style)))
    return xmlsrc.element(kind_, attrs)


def to_path(doc: VectorDoc, paths: list[Path]) -> VectorDoc:
    """Turn simple shapes into paths (so their points can be moved); others are left."""
    for p in _check(doc, paths):
        new = shapes.to_path(get(doc, p))
        if new is not None:
            doc = replace_at(doc, p, new)
    return doc


def set_path(doc: VectorDoc, path: Path, segs: list) -> VectorDoc:
    """A path's points changed by the Node tool (``segs`` as pathdata parses them)."""
    el = get(doc, path)
    if kind(el) != "path":
        raise VectorError("Only paths have points to move.")
    return replace_at(doc, path, el.set("d", pathdata.write(segs)))


SVG_NS = "http://www.w3.org/2000/svg"


def copy_text(doc: VectorDoc, paths: list[Path]) -> str:
    """The elements as a small SVG file (their groups' transforms applied), for the clipboard."""
    parts = []
    for p in _check(doc, paths):
        el = shapes.transformed(get(doc, p), parent_matrix(doc, p))
        parts.append("  " + xmlsrc.write_node(el))
    return f'<svg xmlns="{SVG_NS}">\n' + "\n".join(parts) + "\n</svg>\n"


def paste_text(
    doc: VectorDoc, text: str, dx: float = 0.0, dy: float = 0.0, parent: Path = ()
) -> tuple[VectorDoc, list[Path]]:
    """Add the drawable parts of an SVG text (what ``copy_text`` made, or any SVG) on top of
    ``parent``, moved by (dx, dy), with ids that don't clash. Returns their paths."""
    source = parse(text)
    taken = ids(doc)
    out: list[Path] = []
    for el in source.root.elements:
        if not editable(el):
            continue
        el = shapes.transformed(_reid(el, taken), translate(dx, dy))
        doc, p = add(doc, el, parent)
        out.append(p)
    if not out:
        raise VectorError("There's nothing to paste.")
    return doc, out
