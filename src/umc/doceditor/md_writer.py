"""Write the editor's HTML subset as Markdown (no Qt).

The output is read back by ``io.open_document`` (markdown-it: CommonMark with tables and
strikethrough) to the same subset, so saving a Markdown file over itself never changes it.
What Markdown has no syntax for is written as inline HTML
(``<u>``, ``<s>``, ``<br>``, styled ``<span>``, ``<a href>`` for addresses markdown-it would
rewrite), which markdown-it passes through. Blocks with alignment, an indent or a page break,
and tables a pipe table can't hold, are written as one line of raw HTML (alignment as the
``align`` attribute GitHub shows); an empty paragraph is ``<span></span>``.
"""

from __future__ import annotations

import html as _html
import re
from copy import deepcopy

import lxml.html
from lxml import etree
from markdown_it.common.normalize_url import normalizeLink, validateLink

NBSP = "\xa0"
# Escaped wherever they appear in text (CommonMark reads "\\x" back as "x").
_ALWAYS = {"\\": "\\\\", "`": "\\`", "*": "\\*", "_": "\\_", "[": "\\[", "]": "\\]"}
_ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
# At the start of a line these would start a heading, quote, list, rule or code fence.
_LINE_START = re.compile(r"^(?:[#>+=~-]|\d+[.)](?=\s|$))")
_TASK = re.compile(r"(\[[ xX]\])(?:[ ]+|$)")
_TRAILING_HASHES = re.compile(r"(#+)$")
# markdown-it decodes entity references in a link's address.
_ENTITY_LIKE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_VOID = frozenset({"br", "img", "hr"})
_STYLED_BLOCKS = frozenset({"p", "h1", "h2", "h3", "li"})


def _tag(node: etree._Element) -> str:
    return node.tag.lower() if isinstance(node.tag, str) else ""


def _escape(text: str, table: bool) -> str:
    out = []
    for c in text:
        if c in _ALWAYS:
            out.append(_ALWAYS[c])
        elif c in _ENTITIES:
            out.append(_ENTITIES[c])
        elif c == "\t":  # Markdown would read a raw tab as spaces
            out.append("&#9;")
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
            if node.get("width") or node.get("height"):  # Markdown has no size: raw HTML
                attrs = "".join(
                    f' {k}="{_html.escape(node.get(k) or "", quote=True)}"'
                    for k in ("src", "width", "height")
                    if node.get(k)
                )
                return f"<img{attrs}>"
            return f"![]({_link_target(node.get('src') or '')})"
        inner = self._children(node)
        if tag in ("b", "i"):
            if not inner.strip(" "):
                return inner
            mark = "**" if tag == "b" else "*"
            parent = node.getparent()
            if (  # "***x***" is read as <i><b>; "**_x_**" as <b><i>
                tag == "i"
                and parent is not None
                and _tag(parent) == "b"
                and not parent.text
                and parent[0] is node
                and not node.tail
            ):
                mark = "_"
            lead = inner[: len(inner) - len(inner.lstrip(" "))]
            trail = inner[len(inner.rstrip(" ")) :]
            return f"{lead}{mark}{inner.strip(' ')}{mark}{trail}"
        if tag in ("u", "s"):
            return f"<{tag}>{inner}</{tag}>"
        if tag == "span" and node.get("style"):
            style = _html.escape(node.get("style") or "", quote=True)
            return f'<span style="{style}">{inner}</span>'
        if tag == "a" and node.get("href"):
            href = node.get("href") or ""
            if not _keeps_link(href):  # markdown-it would change the address: raw HTML
                return f'<a href="{_html.escape(href, quote=True)}">{inner}</a>'
            return f"[{inner}]({_link_target(href)})"
        return inner


def _keeps_link(url: str) -> bool:
    """True when markdown-it reads ``[x](url)`` back as exactly ``url``."""
    return (
        validateLink(url)
        and normalizeLink(url) == url
        and _ENTITY_LIKE.search(url) is None
        and "\\" not in url
    )


def _raw_text(text: str) -> str:
    """Text inside raw HTML, kept on one line (a blank line would end the HTML block)."""
    text = _html.escape(text, quote=False)
    return text.replace("\r", "&#13;").replace("\n", "&#10;")


def _raw_attrs(node: etree._Element, tag: str) -> str:
    attrs = []
    for key, value in node.attrib.items():
        if key == "style" and tag in _STYLED_BLOCKS:
            rest = []
            for part in value.split(";"):
                if part.startswith("text-align:"):  # GitHub keeps align="", drops style=""
                    attrs.append(("align", part.removeprefix("text-align:")))
                elif part:
                    rest.append(part)
            if rest:
                attrs.append(("style", ";".join(rest)))
        else:
            attrs.append((key, value))
    return "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in attrs)


def _raw(node: etree._Element) -> str:
    """A block of the subset as one line of HTML that reads back to the same subset."""
    out: list[str] = []

    def walk(el: etree._Element) -> None:
        tag = _tag(el)
        if not tag:
            return
        out.append(f"<{tag}{_raw_attrs(el, tag)}>")
        if tag in _VOID:
            return
        out.append(_raw_text(el.text or ""))
        for child in el:
            walk(child)
            out.append(_raw_text(child.tail or ""))
        out.append(f"</{tag}>")

    walk(node)
    return "".join(out)


def _styled(node: etree._Element) -> bool:
    return bool(node.get("style")) or any(li.get("style") for li in node.iter("li"))


def _link_target(url: str) -> str:
    url = url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")  # as markdown-it reads it
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
            if tag == "pre":  # one <pre> per code line: consecutive ones are one block ...
                lines = []
                while i < len(children) and _tag(children[i]) == "pre":
                    text = _pre_text(children[i])
                    lines.append(text.removesuffix("\n"))
                    i += 1
                    if text.endswith("\n"):  # ... but Markdown's whole code block ends one
                        break
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
        if tag in _STYLED_BLOCKS | {"ul", "ol"} and _styled(node):
            return _raw(node)  # alignment, indent, page break: no Markdown form
        if tag in ("h1", "h2", "h3"):
            text = _Inline().render(node)
            text = _TRAILING_HASHES.sub(lambda m: "\\#" * len(m.group(1)), text)
            return f"{'#' * int(tag[1])} {text}" if text else "#" * int(tag[1])  # stays a heading
        if tag == "p":
            return _Inline().render(node) or "<span></span>"  # an empty paragraph stays one
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

    def _list(self, node: etree._Element, tag: str, level: int, alt: bool = False) -> list[str]:
        """``alt``: the other marker ("* ", "1) "), which starts a new list right after one."""
        lines: list[str] = []
        marker = ("1) " if alt else "1. ") if tag == "ol" else ("* " if alt else "- ")
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
            task = _TASK.match(holder.text or "")
            if task:  # "[ ] todo" / "[x] done": a checkbox on GitHub, so not escaped
                holder.text = holder.text[task.end() :]
            text = _Inline().render(holder)
            if task:
                text = task.group(1) + (" " + text if text else "")
            text = text or "<span></span>"  # an empty item stays an item
            lines.append(f"{pad}{marker}{text}")
            prev, alt_sub = "", False
            for sub in nested:  # two lists of one kind in a row would be read as one
                alt_sub = _tag(sub) == prev and not alt_sub
                prev = _tag(sub)
                lines.extend(self._list(sub, prev, level + 1, alt_sub))
        return lines

    def _table(self, node: etree._Element) -> str | None:
        if not _pipe_table(node):
            return _raw(node)
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


def _pipe_table(table: etree._Element) -> bool:
    """True when a pipe table reads back to ``table``: plain rows of equal length, each cell one
    plain paragraph (what markdown-it's tables become in the subset)."""
    if table.attrib or not len(table) or not _no_text(table):
        return False
    width = len(table[0])
    for tr in table:
        if _tag(tr) != "tr" or tr.attrib or not _no_text(tr):
            return False
        if len(tr) != width or not width:
            return False
        for td in tr:
            if _tag(td) != "td" or td.attrib or len(td) != 1 or not _no_text(td):
                return False
            if _tag(td[0]) != "p" or td[0].attrib:
                return False
    return True


def _no_text(node: etree._Element) -> bool:
    """Only white space among the node's own text (its text and its children's tails)."""
    return not (node.text or "").strip() and not any((c.tail or "").strip() for c in node)


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
    if not html.strip() or html == "<p></p>":  # the empty document (opening "" gives "<p></p>")
        return ""
    root = lxml.html.fragment_fromstring(html, create_parent="div")
    blocks = _Writer().blocks(root)
    return "\n\n".join(blocks) + "\n" if blocks else ""
