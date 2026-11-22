"""XML text ↔ a tree of values (port: web/src/dataedit/xmltree.ts).

Our own small reader / writer, so both apps read and write the same: the data converter's
convention — attributes are "@name" keys, text beside child elements is "#text", repeated
child elements become a list, an element with only text is its value (typed by
``cells.guess``: "12" → a number, "" → null). The root is ``{tag: value}``.

Written back with the file's declaration and indentation. Comments, processing instructions,
the DOCTYPE and the order of differently named children mixed together can't be kept:
``Parsed.lost`` lists what the file has of them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from umc.dataedit.cells import WS, Num, guess


class XmlError(ValueError):
    def __init__(self, pos: int, message: str) -> None:
        super().__init__(message)
        self.pos = pos
        self.message = message


@dataclass
class Parsed:
    root: dict[str, Any]
    declaration: str  # "<?xml …?>" as written, or ""
    indent: str | None  # None = all on one line
    lost: list[str] = field(default_factory=list)


_TOKEN = re.compile(
    r"(?P<comment><!--.*?-->)"
    r"|<!\[CDATA\[(?P<cdata>.*?)\]\]>"
    r"|(?P<pi><\?.*?\?>)"
    r"|(?P<doctype><!DOCTYPE(?:\"[^\"]*\"|'[^']*'|\[[^\]]*\]|[^>])*>)"
    r"|</(?P<end>[^\s>]+)\s*>"
    r"|<(?P<start>[^\s/>!?]+)(?P<attrs>(?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*(?P<close>/?)>"
    r"|(?P<text>[^<]+)",
    re.S,
)
_ATTR = re.compile(r"([^\s=/>]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_ENTITY = re.compile(r"&(?:(amp|lt|gt|quot|apos)|#([0-9]+)|#x([0-9a-fA-F]+));")
_NAMED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def unescape(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        if m[1]:
            return _NAMED[m[1]]
        code = int(m[2]) if m[2] else int(m[3], 16)
        return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else m[0]

    return _ENTITY.sub(one, text) if "&" in text else text


class _Node:
    __slots__ = ("tag", "attrs", "children", "texts", "mixed")

    def __init__(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        self.tag, self.attrs = tag, attrs
        self.children: list[tuple[str, Any]] = []
        self.texts: list[str] = []
        self.mixed = False  # a child tag came back after another one (order can't be kept)


def _value(node: _Node) -> Any:
    text = "".join(node.texts)
    if not node.attrs and not node.children:
        return guess("" if not text.strip(WS) else text)
    out: dict[str, Any] = {f"@{k}": guess(v) for k, v in node.attrs}
    listed: set[str] = set()
    for tag, value in node.children:
        if tag in listed:
            out[tag].append(value)
        elif tag in out:
            out[tag] = [out[tag], value]
            listed.add(tag)
        else:
            out[tag] = value
    text = text.strip(WS)
    if text:
        out["#text"] = guess(text)
    return out


def parse(text: str) -> Parsed:
    """Raises XmlError at the first mistake."""
    stack: list[_Node] = []
    root: tuple[str, Any] | None = None
    declaration = ""
    lost: set[str] = set()
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if m is None:
            raise XmlError(pos, "A tag isn't closed or has a mistake")
        if m["comment"] is not None:
            lost.add("comments")
        elif m["pi"] is not None:
            if pos == 0 and m["pi"][:5] == "<?xml" and m["pi"][5:6] in WS:
                declaration = m["pi"]
            else:
                lost.add("processing instructions")
        elif m["doctype"] is not None:
            lost.add("the DOCTYPE")
        elif m["cdata"] is not None:
            if not stack:
                raise XmlError(pos, "Text outside the root element")
            stack[-1].texts.append(m["cdata"])
        elif m["text"] is not None:
            if stack:
                stack[-1].texts.append(unescape(m["text"]))
            elif m["text"].strip(WS):
                raise XmlError(pos, "Text outside the root element")
        elif m["end"] is not None:
            if not stack or stack[-1].tag != m["end"]:
                want = f"</{stack[-1].tag}>" if stack else "nothing"
                raise XmlError(pos, f"</{m['end']}> found where {want} was expected")
            node = stack.pop()
            done = (node.tag, _value(node))
            if node.mixed:
                lost.add("the order of differently named elements mixed together")
            if node.children and "".join(node.texts).strip(WS):
                lost.add("where text sits between child elements")
            if stack:
                _add_child(stack[-1], done)
            else:
                root = done
        else:
            if root is not None and not stack:
                raise XmlError(pos, "There is more than one root element")
            attrs = [
                (a[1], unescape(re.sub("[\t\n]", " ", a[2] if a[2] is not None else a[3])))
                for a in _ATTR.finditer(m["attrs"])
            ]
            node = _Node(m["start"], attrs)
            if m["close"]:
                done = (node.tag, _value(node))
                if stack:
                    _add_child(stack[-1], done)
                else:
                    root = done
            else:
                stack.append(node)
        pos = m.end()
    if stack:
        raise XmlError(n, f"<{stack[-1].tag}> is never closed")
    if root is None:
        raise XmlError(n, "There is no root element")
    return Parsed({root[0]: root[1]}, declaration, detect_indent(text, declaration), sorted(lost))


def _add_child(parent: _Node, child: tuple[str, Any]) -> None:
    tags = [t for t, _ in parent.children]
    if tags and child[0] in tags and tags[-1] != child[0]:
        parent.mixed = True
    parent.children.append(child)


def detect_indent(text: str, declaration: str = "") -> str | None:
    """The indentation step of the first indented element: "" when children start lines
    without one, None when the root is all on one line."""
    body = text[len(declaration) :].strip(WS)
    m = re.search(r"\n([ \t]+)<", body)
    if m:
        return m[1]
    return "" if "\n" in body else None


# ---------------------------------------------------------------------------- writing
def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Num):
        return value.text
    return str(value)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return (
        _escape(text)
        .replace('"', "&quot;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
        .replace("\r", "&#13;")
    )


def _element(tag: str, value: Any, indent: str | None, level: int) -> str:
    if isinstance(value, list):  # a list inside a list: its items are <item> elements
        value = {"item": value}
    attrs, text, children = "", "", []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "#text":
                text = _scalar(v)
            elif k.startswith("@"):
                attrs += f' {k[1:]}="{_escape_attr(_scalar(v))}"'
            elif isinstance(v, list):
                children.extend((k, x) for x in v)
            else:
                children.append((k, v))
    else:
        text = _scalar(value)
    if not children:
        return f"<{tag}{attrs}/>" if not text else f"<{tag}{attrs}>{_escape(text)}</{tag}>"
    pad = "" if indent is None else "\n" + indent * (level + 1)
    body = "".join(pad + _element(t, v, indent, level + 1) for t, v in children)
    end = "" if indent is None else "\n" + indent * level
    return f"<{tag}{attrs}>{_escape(text)}{body}{end}</{tag}>"


def dumps(root: Any, declaration: str, indent: str | None) -> str:
    """The tree as XML text ("\\n" line endings, no final newline)."""
    if (
        isinstance(root, dict)
        and len(root) == 1
        and not isinstance(next(iter(root.values())), list)
    ):
        ((tag, value),) = root.items()
    else:  # the table became the whole document: wrap it
        tag, value = "root", root
    out = _element(tag, value, indent, 0)
    return f"{declaration}\n{out}" if declaration else out
