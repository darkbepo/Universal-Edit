"""What the vector editor understands of an SVG element (port: ``shapes.ts``).

Bounds, styles and the geometry attributes of the simple shapes. Everything works on
``xmlsrc.Element`` and returns new elements; nothing here knows about the document around it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from umc.vectoredit import pathdata, xmlsrc
from umc.vectoredit.geom import IDENTITY, Box, Matrix, format_transform, num, parse_transform
from umc.vectoredit.xmlsrc import Element

SHAPES = (
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "image",
    "use",
)
CONTAINERS = ("g", "a", "switch")
# Never drawn by themselves: not in the layers list, never selected.
HIDDEN = (
    "defs", "metadata", "title", "desc", "style", "script", "namedview", "clipPath", "mask",
    "pattern", "marker", "symbol", "linearGradient", "radialGradient", "filter", "font",
)  # fmt: skip
# Properties a group passes on to what it holds (pushed down when ungrouping).
INHERITED = (
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "font-family", "font-size",
    "font-weight", "font-style", "text-anchor", "color",
)  # fmt: skip
DEFAULT_FONT_SIZE = 16.0

_LENGTH = re.compile(r"[ \t\r\n]*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(px)?[ \t\r\n]*")
_NUMBERS = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def local(name: str) -> str:
    """``svg:rect`` → ``rect``."""
    return name.rsplit(":", 1)[-1]


def kind(el: Element) -> str:
    return local(el.name)


def length(value: str | None) -> float | None:
    """A plain number or ``px`` length; anything else (%, em, mm …) → None."""
    if value is None:
        return None
    m = _LENGTH.fullmatch(value)
    return float(m.group(1)) if m else None


def numbers(value: str | None) -> list[float]:
    return [float(v) for v in _NUMBERS.findall(value or "")]


# ------------------------------------------------------------------------------- style


def parse_style(text: str | None) -> list[tuple[str, str]]:
    """``style="a:b; c:d"`` → ``[("a", "b"), ("c", "d")]`` (order kept, names trimmed)."""
    out: list[tuple[str, str]] = []
    for part in (text or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip(" \t\r\n"), v.strip(" \t\r\n")
        if k:
            out.append((k, v))
    return out


def write_style(items: list[tuple[str, str]]) -> str:
    return ";".join(f"{k}:{v}" for k, v in items)


def style_get(el: Element, name: str) -> str | None:
    """The element's own value (the ``style`` attribute wins over the attribute)."""
    for k, v in parse_style(el.get("style")):
        if k == name:
            return v
    return el.get(name)


def style_set(el: Element, name: str, value: str | None) -> Element:
    """Change ``name`` where it was written; a new one goes into ``style`` if the element has
    one, else into an attribute. None removes it from both places."""
    items = parse_style(el.get("style"))
    in_style = any(k == name for k, _ in items)
    if value is None:
        if in_style:
            items = [(k, v) for k, v in items if k != name]
            el = el.set("style", write_style(items) if items else None)
        return el.set(name, None)
    if in_style:
        el = el.set("style", write_style([(k, value if k == name else v) for k, v in items]))
        return el.set(name, None) if el.get(name) is not None else el
    if el.get(name) is not None or el.get("style") is None:
        return el.set(name, value)
    return el.set("style", write_style([*items, (name, value)]))


def hidden(el: Element) -> bool:
    return style_get(el, "display") == "none"


# ------------------------------------------------------------------------------- geometry


def own_transform(el: Element) -> Matrix:
    try:
        return parse_transform(el.get("transform") or "")
    except ValueError:
        return IDENTITY


def _points(el: Element) -> list[tuple[float, float]]:
    v = numbers(el.get("points"))
    return [(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]


def text_of(el: Element) -> str:
    return el.text()


Find = Callable[[str], "Element | None"]
MAX_USE_DEPTH = 8


def _href(el: Element) -> str | None:
    ref = el.get("href") or el.get("xlink:href")
    return ref[1:] if ref and ref.startswith("#") else None


def local_bounds(el: Element, find: Find | None = None, depth: int = 0) -> Box | None:
    """The element's box in its own coordinates (before its ``transform``); None if empty or
    unknown. Text is estimated (0.6 em per character), as no fonts are measured here. ``find``
    looks an id up, so a ``<use>`` without a size takes the box of what it shows."""
    k = kind(el)
    if k == "use" and el.get("width") is None and find is not None and depth < MAX_USE_DEPTH:
        ref = _href(el)
        target = find(ref) if ref else None
        b = None if target is None else bounds(target, find, depth + 1)
        if b is None:
            return None
        x, y = length(el.get("x", "0")) or 0.0, length(el.get("y", "0")) or 0.0
        return Box(b.x0 + x, b.y0 + y, b.x1 + x, b.y1 + y)
    if k in ("rect", "image", "use"):
        x, y = length(el.get("x", "0")), length(el.get("y", "0"))
        w, h = length(el.get("width")), length(el.get("height"))
        if None in (x, y, w, h) or w <= 0 or h <= 0:  # type: ignore[operator]
            return None
        return Box(x, y, x + w, y + h)  # type: ignore[operator]
    if k in ("circle", "ellipse"):
        cx, cy = length(el.get("cx", "0")), length(el.get("cy", "0"))
        if k == "circle":
            rx = ry = length(el.get("r"))
        else:
            rx, ry = length(el.get("rx")), length(el.get("ry"))
        if None in (cx, cy, rx, ry) or rx <= 0 or ry <= 0:  # type: ignore[operator]
            return None
        return Box(cx - rx, cy - ry, cx + rx, cy + ry)  # type: ignore[operator]
    if k == "line":
        vals = [length(el.get(a, "0")) for a in ("x1", "y1", "x2", "y2")]
        if None in vals:
            return None
        x1, y1, x2, y2 = vals  # type: ignore[misc]
        return Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if k in ("polyline", "polygon"):
        pts = _points(el)
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Box(min(xs), min(ys), max(xs), max(ys))
    if k == "path":
        return pathdata.bounds(pathdata.parse(el.get("d") or ""))
    if k == "text":
        xs, ys = numbers(el.get("x", "0")), numbers(el.get("y", "0"))
        x, y = (xs or [0.0])[0], (ys or [0.0])[0]
        size = length(style_get(el, "font-size")) or DEFAULT_FONT_SIZE
        chars = len(text_of(el))
        if chars == 0:
            return None
        w = chars * 0.6 * size
        anchor = style_get(el, "text-anchor")
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        return Box(x, y - 0.8 * size, x + w, y + 0.2 * size)
    if k in CONTAINERS or k == "svg":
        box: Box | None = None
        for child in el.elements:
            if kind(child) in HIDDEN or hidden(child):
                continue
            b = bounds(child, find, depth)
            if b is not None:
                box = b if box is None else box.union(b)
        return box
    return None


def bounds(el: Element, find: Find | None = None, depth: int = 0) -> Box | None:
    """The element's box in its parent's coordinates (its own ``transform`` applied)."""
    if kind(el) == "svg":
        return local_bounds(el, find, depth)
    b = local_bounds(el, find, depth)
    return None if b is None else b.transformed(own_transform(el))


# ------------------------------------------------------------------------------- changing


def _set_nums(el: Element, values: dict[str, float]) -> Element:
    for name, v in values.items():
        el = el.set(name, num(v))
    return el


_LEADING_TRANSLATE = re.compile(r"[ \t\r\n]*translate[ \t\r\n]*\(([^)]*)\)[ \t\r\n]*,?[ \t\r\n]*")


def _fold(el: Element, m: Matrix) -> Element:
    """Put ``m`` in front of the element's own transform. A move keeps the rest readable: it
    becomes (or adds to) a leading ``translate(…)``; anything else is written as one matrix."""
    text = el.get("transform") or ""
    own = own_transform(el)
    if m.is_translate() and text.strip(" \t\r\n") and not own.is_translate():
        lead = _LEADING_TRANSLATE.match(text)
        rest = text[lead.end() :] if lead else text.strip(" \t\r\n")
        tx, ty = m.e, m.f
        if lead:
            v = numbers(lead.group(1))
            if len(v) in (1, 2):
                tx, ty = tx + v[0], ty + (v[1] if len(v) == 2 else 0.0)
            else:
                rest = text.strip(" \t\r\n")
        move = format_transform(Matrix(1, 0, 0, 1, tx, ty))
        return el.set("transform", f"{move} {rest}".strip() if move else rest)
    return el.set("transform", format_transform(m @ own) or None)


def transformed(el: Element, m: Matrix) -> Element:
    """``el`` moved / scaled by ``m`` (given in the parent's coordinates).

    Simple shapes without a transform of their own get their geometry rewritten when the change
    keeps them axis-aligned (paths and point lists always do); everything else gets ``m``
    folded into its ``transform``. Stroke widths stay as they are.
    """
    if m.is_identity():
        return el
    k = kind(el)
    plain = own_transform(el).is_identity()
    aligned = m.b == 0 and m.c == 0
    if plain and k == "path":
        return el.set("d", pathdata.write(pathdata.transform(pathdata.parse(el.get("d") or ""), m)))
    if plain and k in ("polyline", "polygon", "line"):
        if k == "line":
            vals = [length(el.get(a, "0")) for a in ("x1", "y1", "x2", "y2")]
            if None not in vals:
                x1, y1 = m.apply(vals[0], vals[1])  # type: ignore[arg-type]
                x2, y2 = m.apply(vals[2], vals[3])  # type: ignore[arg-type]
                return _set_nums(el, {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        else:
            pts = [m.apply(x, y) for x, y in _points(el)]
            return el.set("points", " ".join(f"{num(x)},{num(y)}" for x, y in pts))
    if plain and aligned and m.a > 0 and m.d > 0:
        x, y = length(el.get("x", "0")), length(el.get("y", "0"))
        w, h = length(el.get("width")), length(el.get("height"))
        sized = w is not None and h is not None
        if k in ("rect", "image", "use") and None not in (x, y) and (sized or m.is_translate()):
            x0, y0 = m.apply(x, y)  # type: ignore[arg-type]
            out = _set_nums(el, {"x": x0, "y": y0})
            if sized:
                out = _set_nums(out, {"width": w * m.a, "height": h * m.d})  # type: ignore[operator]
            if k == "rect" and (m.a != 1 or m.d != 1):
                for attr, s in (("rx", m.a), ("ry", m.d)):
                    r = length(el.get(attr))
                    if r is not None:
                        out = _set_nums(out, {attr: r * s})
            return out
        if k == "ellipse" or (k == "circle" and m.a == m.d):
            b = local_bounds(el)
            if b is not None:
                cx, cy = m.apply((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
                out = _set_nums(el, {"cx": cx, "cy": cy})
                if k == "circle":
                    return _set_nums(out, {"r": b.width / 2 * m.a})
                return _set_nums(out, {"rx": b.width / 2 * m.a, "ry": b.height / 2 * m.d})
        positioned = any(c.get("x") or c.get("y") for c in el.elements)
        if k == "text" and m.is_translate() and not positioned:
            xs, ys = numbers(el.get("x", "0")), numbers(el.get("y", "0"))
            if len(ys) <= 1:
                x, y = m.apply((xs or [0.0])[0], (ys or [0.0])[0])
                return _set_nums(el, {"x": x, "y": y})
    return _fold(el, m)


def path_data(el: Element) -> str | None:
    """The outline of a simple shape as path data (absolute), or None if it has no outline."""
    k = kind(el)
    if k == "path":
        return el.get("d") or ""
    if k == "rect":
        b = local_bounds(el)
        if b is None:
            return None
        rx, ry = length(el.get("rx")), length(el.get("ry"))
        rx = ry if rx is None else rx
        ry = rx if ry is None else ry
        rx = min(max(rx or 0.0, 0.0), b.width / 2)
        ry = min(max(ry or 0.0, 0.0), b.height / 2)
        if rx == 0 or ry == 0:
            segs = [("M", b.x0, b.y0), ("L", b.x1, b.y0), ("L", b.x1, b.y1), ("L", b.x0, b.y1),
                    ("Z",)]  # fmt: skip
        else:
            arc = ("A", rx, ry, 0.0, 0.0, 1.0)
            segs = [
                ("M", b.x0 + rx, b.y0), ("L", b.x1 - rx, b.y0), (*arc, b.x1, b.y0 + ry),
                ("L", b.x1, b.y1 - ry), (*arc, b.x1 - rx, b.y1), ("L", b.x0 + rx, b.y1),
                (*arc, b.x0, b.y1 - ry), ("L", b.x0, b.y0 + ry), (*arc, b.x0 + rx, b.y0), ("Z",),
            ]  # fmt: skip
        return pathdata.write(segs)
    if k in ("circle", "ellipse"):
        b = local_bounds(el)
        if b is None:
            return None
        rx, ry = b.width / 2, b.height / 2
        cy = (b.y0 + b.y1) / 2
        arc = ("A", rx, ry, 0.0, 1.0, 0.0)
        return pathdata.write([("M", b.x0, cy), (*arc, b.x1, cy), (*arc, b.x0, cy), ("Z",)])
    if k == "line":
        vals = [length(el.get(a, "0")) for a in ("x1", "y1", "x2", "y2")]
        if None in vals:
            return None
        return pathdata.write([("M", vals[0], vals[1]), ("L", vals[2], vals[3])])  # type: ignore[list-item]
    if k in ("polyline", "polygon"):
        pts = _points(el)
        if not pts:
            return None
        segs: list = [("M", *pts[0]), *(("L", *p) for p in pts[1:])]
        if k == "polygon":
            segs.append(("Z",))
        return pathdata.write(segs)
    return None


GEOMETRY = {
    "rect": ("x", "y", "width", "height", "rx", "ry"),
    "circle": ("cx", "cy", "r"),
    "ellipse": ("cx", "cy", "rx", "ry"),
    "line": ("x1", "y1", "x2", "y2"),
    "polyline": ("points",),
    "polygon": ("points",),
}


def to_path(el: Element) -> Element | None:
    """The same shape as a ``<path>`` (its other attributes kept, in order), or None."""
    k = kind(el)
    if k == "path":
        return el
    d = path_data(el)
    if d is None:
        return None
    prefix = el.name[: len(el.name) - len(k)]  # "svg:" stays "svg:"
    drop = GEOMETRY[k]
    attrs = []
    placed = False
    for name, value in el.attrs:
        if name in drop:
            if not placed:
                attrs.append(("d", d))
                placed = True
            continue
        attrs.append((name, value))
    if not placed:
        attrs.append(("d", d))
    return xmlsrc.element(prefix + "path", attrs, el.children)
