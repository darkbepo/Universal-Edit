"""Write the editor's HTML subset as Markdown (no Qt).

The output is read back by ``io.open_document`` (Python-Markdown with the ``tables``,
``fenced_code`` and ``sane_lists`` extensions) to the same subset, so saving a Markdown file
over itself never changes it. What Markdown has no syntax for is written as inline HTML
(``<u>``, ``<s>``, ``<br>``, styled ``<span>``), which Python-Markdown passes through.
Paragraph alignment, indents and page breaks have no Markdown form and are left out.
"""

from __future__ import annotations

import html as _html
import re
from copy import deepcopy

import lxml.html
from lxml import etree

NBSP = "\xa0"
# Escaped wherever they appear in text (Python-Markdown reads "\\x" back as "x").
_ALWAYS = {"\\": "\\\\", "`": "\\`", "*": "\\*", "_": "\\_", "[": "\\[", "]": "\\]"}
_ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
# At the start of a line these would start a heading, quote, list, rule or code fence.
_LINE_START = re.compile(r"^(?:[#>+=~-]|\d+[.)](?=\s|$))")
_TRAILING_HASHES = re.compile(r"(#+)$")


def _tag(node: etree._Element) -> str:
    return node.tag.lower() if isinstance(node.tag, str) else ""


def _escape(text: str, table: bool) -> str:
    out = []
    for c in text:
        if c in _ALWAYS:
            out.append(_ALWAYS[c])
        elif c in _ENTITIES:
            out.append(_ENTITIES[c])
        elif c == "|" and table:
            out.append("\\|")
        else:
            out.append(c)
    return "".join(out)


def _line_start(text: str) -> str:
    """Keep the start of a paragraph line from being read as Markdown syntax."""
    i = 0
    lead = []
    while i < len(text) and text[i] in (NBSP, "\t", " "):  # would become indented code
        lead.append({NBSP: "&nbsp;", "\t": "&#9;", " ": "&nbsp;"}[text[i]])
        i += 1
    rest = text[i:]
    m = _LINE_START.match(rest)
    if m:
        first = m.group(0)
        if first[0].isdigit():  # "1." -> "1\."
            rest = first[:-1] + "\\" + first[-1] + rest[len(first) :]
        else:
            rest = f"&#{ord(first)};" + rest[1:]
    return "".join(lead) + rest


class _Inline:
    """Inline content of one block as one Markdown line."""

    def __init__(self, table: bool = False) -> None:
        self.table = table

    def render(self, node: etree._Element) -> str:
        return _line_start(self._children(node).strip(" "))

    def _children(self, node: etree._Element) -> str:
        parts = [_escape(node.text or "", self.table)]
        for child in node:
            parts.append(self._node(child))
            parts.append(_escape(child.tail or "", self.table))
        return "".join(parts)

    def _node(self, node: etree._Element) -> str:
        tag = _tag(node)
        if tag == "br":
            return "<br>"
        if tag == "img":
            return f"![]({_link_target(node.get('src') or '')})"
        inner = self._children(node)
        if tag in ("b", "i"):
            if not inner.strip(" "):
                return inner
            mark = "**" if tag == "b" else "*"
            lead = inner[: len(inner) - len(inner.lstrip(" "))]
            trail = inner[len(inner.rstrip(" ")) :]
            return f"{lead}{mark}{inner.strip(' ')}{mark}{trail}"
        if tag in ("u", "s"):
            return f"<{tag}>{inner}</{tag}>"
        if tag == "span" and node.get("style"):
            style = _html.escape(node.get("style") or "", quote=True)
            return f'<span style="{style}">{inner}</span>'
        if tag == "a" and node.get("href"):
            return f"[{inner}]({_link_target(node.get('href') or '')})"
        return inner


def _link_target(url: str) -> str:
    url = url.replace("<", "%3C").replace(">", "%3E")
    return f"<{url}>" if re.search(r"[\s()]", url) else url


def _pre_text(node: etree._Element) -> str:
    parts = [node.text or ""]
    for child in node:
        parts.append("\n" if _tag(child) == "br" else "".join(child.itertext()))
        parts.append(child.tail or "")
    return "".join(parts)


def _fence(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", code)), default=2)
    mark = "`" * max(3, longest + 1)
    return f"{mark}\n{code}\n{mark}"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


class _Writer:
    def blocks(self, root: etree._Element) -> list[str]:
        out: list[str] = []
        children = [c for c in root if _tag(c)]
        prev = ""
        i = 0
        while i < len(children):
            node = children[i]
            tag = _tag(node)
            if tag == "pre":  # one <pre> per code line: consecutive ones are one block
                lines = []
                while i < len(children) and _tag(children[i]) == "pre":
                    lines.append(_pre_text(children[i]).removesuffix("\n"))
                    i += 1
                out.append(_fence("\n".join(lines)))
                prev = tag
                continue
            i += 1
            block = self.block(node, tag)
            if block is None:
                continue
            if tag == prev and tag in ("ul", "ol", "blockquote"):
                out.append("<!-- -->")  # two in a row would otherwise be read as one
            prev = tag
            out.append(block)
        return out

    def block(self, node: etree._Element, tag: str) -> str | None:
        if tag in ("h1", "h2", "h3"):
            text = _Inline().render(node)
            text = _TRAILING_HASHES.sub(lambda m: "\\#" * len(m.group(1)), text)
            return f"{'#' * int(tag[1])} {text}" if text else None
        if tag == "p":
            return _Inline().render(node) or None
        if tag == "blockquote":
            inner = "\n\n".join(_Writer().blocks(node))
            return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
        if tag in ("ul", "ol"):
            return "\n".join(self._list(node, tag, 0))
        if tag == "table":
            return self._table(node)
        if tag == "hr":
            return "---"
        return _Inline().render(node) or None

    def _list(self, node: etree._Element, tag: str, level: int) -> list[str]:
        lines: list[str] = []
        marker = "1. " if tag == "ol" else "- "
        pad = "    " * level
        for li in node:
            if _tag(li) != "li":
                continue
            holder = etree.Element("li")
            holder.text = li.text
            nested = []
            for child in list(li):
                if _tag(child) in ("ul", "ol"):
                    nested.append(child)
                else:
                    holder.append(child)  # moves it with its tail
            text = _Inline().render(holder) or "<span></span>"  # an empty item stays an item
            lines.append(f"{pad}{marker}{text}")
            for sub in nested:
                lines.extend(self._list(sub, _tag(sub), level + 1))
        return lines

    def _table(self, node: etree._Element) -> str | None:
        rows = []
        for tr in _rows(node):
            cells = []
            for td in tr:
                if _tag(td) not in ("td", "th"):
                    continue
                cells.append("<br>".join(_cell_lines(td)))
            rows.append(cells)
        rows = [r for r in rows if r]
        if not rows:
            return None
        width = max(len(r) for r in rows)
        lines = []
        for n, cells in enumerate(rows):
            cells = cells + [""] * (width - len(cells))
            lines.append("| " + " | ".join(cells) + " |")
            if n == 0:
                lines.append("|" + "|".join([" --- "] * width) + "|")
        return "\n".join(lines)


def _rows(table: etree._Element) -> list[etree._Element]:
    rows = []
    for child in table:
        if _tag(child) == "tr":
            rows.append(child)
        elif _tag(child) in ("thead", "tbody", "tfoot"):
            rows.extend(tr for tr in child if _tag(tr) == "tr")
    return rows


def _cell_lines(td: etree._Element) -> list[str]:
    """The text lines of a table cell (its paragraphs, list items, nested table cells)."""
    lines = []
    for el in td.iter():
        tag = _tag(el)
        if el is td or tag not in ("p", "h1", "h2", "h3", "li", "pre"):
            continue
        holder = etree.Element("p")
        holder.text = el.text
        for child in el:
            if _tag(child) in ("ul", "ol", "table"):
                continue
            holder.append(deepcopy(child))  # with its tail
        text = _Inline(table=True).render(holder)
        if text:
            lines.append(text)
    return lines


def to_markdown(html: str) -> str:
    """The editor's (normalised) HTML subset as Markdown, ending with one newline."""
    if not html.strip():
        return ""
    root = lxml.html.fragment_fromstring(html, create_parent="div")
    blocks = _Writer().blocks(root)
    return "\n\n".join(blocks) + "\n" if blocks else ""
