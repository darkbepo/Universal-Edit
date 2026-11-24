"""An XML tree that remembers its source text (``xmlsrc.ts`` is the same, line by line).

Saving an edited SVG must not reformat the rest of the file: every node keeps the exact text it
was read from, and the writer re-emits that text for anything an edit didn't touch. A changed
element rebuilds only its start tag (attribute order kept, new ones appended); its children are
written from their own source again. Same bytes in both apps.

Elements are immutable: ``set`` / ``with_children`` return a new element without the source
text it no longer matches. Refused: entity declarations (billion laughs), unknown entities,
nesting deeper than :data:`MAX_DEPTH`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

MAX_DEPTH = 256

_NAME = r"[A-Za-z_:\u00C0-\uFFFF\U00010000-\U0010FFFF][^ \t\r\n/>=<\"'&]*"
_START = re.compile(r"<(" + _NAME + r")")
_ATTR = re.compile(r"[ \t\r\n]+(" + _NAME + r")[ \t\r\n]*=[ \t\r\n]*(?:\"([^\"<]*)\"|'([^'<]*)')")
_TAG_END = re.compile(r"[ \t\r\n]*(/?)>")
_END = re.compile(r"</(" + _NAME + r")[ \t\r\n]*>")
_REF = re.compile(r"&(#x[0-9A-Fa-f]+|#[0-9]+|[A-Za-z]+);")
_NAMED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


class XmlError(ValueError):
    pass


@dataclass(frozen=True)
class Text:
    raw: str  # as written (entities not decoded)

    @property
    def value(self) -> str:
        return unescape(self.raw)


@dataclass(frozen=True)
class Other:
    """A comment, processing instruction, CDATA section or doctype, kept as written."""

    raw: str

    @property
    def cdata(self) -> str | None:
        return self.raw[9:-3] if self.raw.startswith("<![CDATA[") else None


@dataclass(frozen=True)
class Element:
    name: str
    attrs: tuple[tuple[str, str], ...] = ()
    children: tuple[Node, ...] = ()
    start_src: str | None = None  # the start tag as written, while attrs are unchanged
    end_src: str | None = None  # the end tag as written ("" when the tag closed itself)
    src: str | None = None  # the whole element as written, while nothing in it changed

    def get(self, name: str, default: str | None = None) -> str | None:
        for k, v in self.attrs:
            if k == name:
                return v
        return default

    def set(self, name: str, value: str | None) -> Element:
        """Set (or with None remove) one attribute; a new one goes last."""
        attrs = [(k, v) for k, v in self.attrs if k != name or value is not None]
        if value is not None:
            for i, (k, _v) in enumerate(attrs):
                if k == name:
                    attrs[i] = (k, value)
                    break
            else:
                attrs.append((name, value))
        new = tuple(attrs)
        if new == self.attrs:
            return self
        return replace(self, attrs=new, start_src=None, src=None)

    def with_children(self, children: tuple[Node, ...] | list[Node]) -> Element:
        children = tuple(children)
        if children == self.children:
            return self
        return replace(self, children=children, src=None)

    @property
    def elements(self) -> tuple[Element, ...]:
        return tuple(c for c in self.children if isinstance(c, Element))

    def text(self) -> str:
        """All text inside (CDATA included), as a browser's ``textContent``."""
        out = []
        for c in self.children:
            if isinstance(c, Text):
                out.append(c.value)
            elif isinstance(c, Element):
                out.append(c.text())
            elif c.cdata is not None:
                out.append(c.cdata)
        return "".join(out)


Node = Element | Text | Other


@dataclass(frozen=True)
class Document:
    before: tuple[Node, ...]  # XML declaration, comments, doctype, whitespace before the root
    root: Element
    after: tuple[Node, ...]

    def with_root(self, root: Element) -> Document:
        return replace(self, root=root)


def unescape(raw: str) -> str:
    def one(m: re.Match) -> str:
        ref = m.group(1)
        if ref.startswith("#x"):
            return _char(int(ref[2:], 16))
        if ref.startswith("#"):
            return _char(int(ref[1:]))
        if ref in _NAMED:
            return _NAMED[ref]
        raise XmlError(f"Unknown entity &{ref};")

    if "&" in raw and re.search(r"&(?!(#x[0-9A-Fa-f]+|#[0-9]+|[A-Za-z]+);)", raw):
        raise XmlError("A lone '&' in the text.")
    return _REF.sub(one, raw)


def _char(code: int) -> str:
    if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        raise XmlError(f"Bad character reference {code}.")
    return chr(code)


def _attr_value(raw: str) -> str:
    return unescape(
        raw.replace("\r\n", "\n").replace("\t", " ").replace("\n", " ").replace("\r", " ")
    )


def escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
        .replace("\t", "&#9;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _other(text: str, pos: int) -> int:
    """End of the comment / PI / CDATA / doctype starting at ``pos``."""
    for opener, closer in (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>")):
        if text.startswith(opener, pos):
            end = text.find(closer, pos + len(opener))
            if end < 0:
                raise XmlError(f"Unclosed {opener} on line {_line(text, pos)}.")
            return end + len(closer)
    if text.startswith("<!DOCTYPE", pos):
        depth, k = 0, pos
        while k < len(text):
            ch = text[k]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == ">" and depth <= 0:
                raw = text[pos : k + 1]
                if "<!ENTITY" in raw:
                    raise XmlError("Files that declare their own entities aren't opened.")
                return k + 1
            k += 1
        raise XmlError("Unclosed <!DOCTYPE.")
    raise XmlError(f"Unexpected '<!' on line {_line(text, pos)}.")


def parse(text: str) -> Document:
    if text.startswith("﻿"):
        raise XmlError("Decode the file first (it starts with a byte order mark).")
    before: list[Node] = []
    after: list[Node] = []
    root: Element | None = None
    # stack of (name, attrs, start_src, start_pos, children)
    stack: list[tuple[str, tuple, str, int, list[Node]]] = []
    pos, n = 0, len(text)
    while pos < n:
        lt = text.find("<", pos)
        if lt < 0:
            lt = n
        if lt > pos:
            raw = text[pos:lt]
            if stack:
                unescape(raw)  # check it
                stack[-1][4].append(Text(raw))
            elif raw.strip(" \t\r\n"):
                raise XmlError(f"Text outside the root element on line {_line(text, pos)}.")
            else:
                (before if root is None else after).append(Text(raw))
            pos = lt
            continue
        if text.startswith("</", pos):
            m = _END.match(text, pos)
            if m is None or not stack or stack[-1][0] != m.group(1):
                raise XmlError(f"Unexpected end tag on line {_line(text, pos)}.")
            name, attrs, start_src, start_pos, children = stack.pop()
            el = Element(
                name, attrs, tuple(children), start_src, m.group(0), text[start_pos : m.end()]
            )
            pos = m.end()
            if stack:
                stack[-1][4].append(el)
            else:
                root = el
            continue
        if text.startswith("<!", pos) or text.startswith("<?", pos):
            end = _other(text, pos)
            node = Other(text[pos:end])
            if stack:
                if node.raw.startswith(("<!DOCTYPE", "<?xml ")):
                    raise XmlError(f"Misplaced declaration on line {_line(text, pos)}.")
                stack[-1][4].append(node)
            elif node.raw.startswith("<![CDATA["):
                raise XmlError("CDATA outside the root element.")
            else:
                (before if root is None else after).append(node)
            pos = end
            continue
        m = _START.match(text, pos)
        if m is None:
            raise XmlError(f"Bad '<' on line {_line(text, pos)}.")
        if root is not None and not stack:
            raise XmlError(f"A second root element on line {_line(text, pos)}.")
        name, k = m.group(1), m.end()
        attrs: list[tuple[str, str]] = []
        while True:
            a = _ATTR.match(text, k)
            if a is None:
                break
            key = a.group(1)
            if any(key == old for old, _ in attrs):
                raise XmlError(f"Attribute {key} twice on line {_line(text, pos)}.")
            raw = a.group(2) if a.group(2) is not None else a.group(3)
            attrs.append((key, _attr_value(raw)))
            k = a.end()
        e = _TAG_END.match(text, k)
        if e is None:
            raise XmlError(f"Bad start tag <{name}> on line {_line(text, pos)}.")
        start_src = text[pos : e.end()]
        if e.group(1):  # <name/>
            el = Element(name, tuple(attrs), (), start_src, "", start_src)
            if stack:
                stack[-1][4].append(el)
            else:
                root = el
        else:
            if len(stack) >= MAX_DEPTH:
                raise XmlError("The drawing is nested too deeply.")
            stack.append((name, tuple(attrs), start_src, pos, []))
        pos = e.end()
    if stack:
        raise XmlError(f"<{stack[-1][0]}> is never closed.")
    if root is None:
        raise XmlError("No root element.")
    return Document(tuple(before), root, tuple(after))


def write_node(node: Node) -> str:
    if isinstance(node, (Text, Other)):
        return node.raw
    if node.src is not None:
        return node.src
    closed_itself = node.end_src == ""
    start = node.start_src
    if start is None or (closed_itself and node.children):
        start = "<" + node.name
        start += "".join(f' {k}="{escape_attr(v)}"' for k, v in node.attrs)
        start += "/>" if not node.children else ">"
    if start.endswith("/>"):
        return start
    inner = "".join(write_node(c) for c in node.children)
    end = node.end_src if node.end_src else f"</{node.name}>"
    return start + inner + end


def write(doc: Document) -> str:
    return (
        "".join(write_node(n) for n in doc.before)
        + write_node(doc.root)
        + "".join(write_node(n) for n in doc.after)
    )


def element(name: str, attrs: list[tuple[str, str]] | None = None, children=()) -> Element:
    """A new element (no source text: written from its parts)."""
    return Element(name, tuple(attrs or ()), tuple(children))
