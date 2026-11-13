"""Just enough XMP for photo details: read a property's text, and remove properties.

When a detail is changed it's written to EXIF (what Windows, phones and most programs read),
and the same property is removed from the XMP packet, so an older value there can't win.
Plain text handling (XMP writers all use the element / attribute forms these patterns know);
ported line by line to ``web/src/properties/xmp.ts``."""

from __future__ import annotations

import re

ENTITIES = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&amp;": "&"}


def _unescape(text: str) -> str:
    return re.sub(r"&(lt|gt|quot|apos|amp);", lambda m: ENTITIES[m[0]], text)


def _name(prop: str) -> str:
    return re.escape(prop)


def get(xmp: str, prop: str) -> str | None:
    """The text of ``prop`` (e.g. "dc:title"); list items are joined with "; "."""
    n = _name(prop)
    m = re.search(rf"\s{n}=\"([^\"]*)\"", xmp)
    if m:
        return _unescape(m[1]).strip() or None
    m = re.search(rf"<{n}\b[^>]*?>([\s\S]*?)</{n}>", xmp)
    if not m:
        return None
    items = re.findall(r"<rdf:li\b[^>]*?>([\s\S]*?)</rdf:li>", m[1])
    if items:
        text = "; ".join(_unescape(i).strip() for i in items if i.strip())
    else:
        text = _unescape(re.sub(r"<[^>]*>", "", m[1])).strip()
    return text or None


def remove(xmp: str, props: list[str]) -> str:
    """``xmp`` without ``props`` (element, empty-element and attribute forms); a prop ending in
    "*" removes every property starting with it (e.g. "exif:GPS*")."""
    for prop in props:
        if prop.endswith("*"):
            n = _name(prop[:-1]) + r"[\w.-]*"
            names = sorted(set(re.findall(rf"<({n})\b", xmp) + re.findall(rf"\s({n})=\"", xmp)))
            xmp = remove(xmp, names) if names else xmp
            continue
        n = _name(prop)
        xmp = re.sub(rf"[ \t]*<{n}\b[^>]*?/>[ \t]*\r?\n?", "", xmp)
        xmp = re.sub(rf"[ \t]*<{n}\b[^>]*?>[\s\S]*?</{n}>[ \t]*\r?\n?", "", xmp)
        xmp = re.sub(rf"\s{n}=\"[^\"]*\"", "", xmp)
    return xmp
