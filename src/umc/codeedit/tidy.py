"""Tidy for the code editor: re-indent valid JSON and XML without changing what they mean.

JSON is re-emitted from ``check.scan_json``'s tokens (numbers and strings stay exactly as
written: ``1.0``, ``1e5`` and ``\\u00e9`` are kept). XML: an element whose children are only
elements (comments, processing instructions) gets each on its own line, 2 spaces deeper; an
element with any text or CDATA in it is kept exactly as written. A trailing newline is kept if
the text had one. ``web/src/codeedit/tidy.ts`` is a line-by-line port (shared cases:
``tests/fixtures/codeedit_tidy_cases.json``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from umc.codeedit.check import check, scan_json

TIDY: frozenset[str] = frozenset({"json", "xml", "svg"})
INDENT = "  "


class TidyError(ValueError):
    """Why the text can't be tidied (shown as it is)."""


def _json(text: str) -> str:
    tokens = scan_json(text)
    if not tokens:
        return text
    out: list[str] = []
    depth = 0
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1].kind if i + 1 < len(tokens) else ""
        if tok.kind in "{[":
            if nxt in "}]" and nxt:
                out.append(tok.text)  # empty: stays short, closed by the next token
                continue
            depth += 1
            out.append(tok.text + "\n" + INDENT * depth)
        elif tok.kind in "}]":
            prev = tokens[i - 1].kind
            if prev in "{[":
                out.append(tok.text)
            else:
                depth -= 1
                out.append("\n" + INDENT * depth + tok.text)
        elif tok.kind == ",":
            out.append(",\n" + INDENT * depth)
        elif tok.kind == ":":
            out.append(": ")
        else:
            out.append(tok.text)
    return "".join(out)


# ---------------------------------------------------------------------------- XML
_XML_TOKEN = re.compile(
    r"<!--.*?-->"  # comment
    r"|<!\[CDATA\[.*?\]\]>"  # CDATA
    r"|<\?.*?\?>"  # declaration / processing instruction
    r"|<![^>]*>"  # DOCTYPE (simple)
    r"|<(?:\"[^\"]*\"|'[^']*'|[^'\">])*>"  # a tag (quotes may hold >)
    r"|[^<]+",  # text
    re.S,
)


@dataclass
class _Node:
    kind: str  # "element", "text", "other" (comment / PI / doctype), "cdata"
    start: int
    end: int = 0
    open_tag: str = ""
    close_tag: str = ""
    children: list[_Node] = field(default_factory=list)


def _xml_tree(text: str) -> list[_Node]:
    root = _Node("element", 0)
    stack = [root]
    for m in _XML_TOKEN.finditer(text):
        tok = m.group()
        if (
            tok.startswith("<!--")
            or tok.startswith("<?")
            or (tok.startswith("<!") and not tok.startswith("<![CDATA["))
        ):
            stack[-1].children.append(_Node("other", m.start(), m.end()))
        elif tok.startswith("<![CDATA["):
            stack[-1].children.append(_Node("cdata", m.start(), m.end()))
        elif tok.startswith("</"):
            node = stack.pop()
            node.close_tag = tok
            node.end = m.end()
        elif tok.startswith("<"):
            node = _Node("element", m.start(), open_tag=tok)
            stack[-1].children.append(node)
            if tok.endswith("/>"):
                node.end = m.end()
            else:
                stack.append(node)
        else:
            stack[-1].children.append(_Node("text", m.start(), m.end()))
    return root.children


def _blank(text: str, node: _Node) -> bool:
    return node.kind == "text" and not text[node.start : node.end].strip()


def _xml_emit(node: _Node, text: str, depth: int) -> str:
    if node.kind != "element" or not node.close_tag:
        return text[node.start : node.end]
    kids = [c for c in node.children if not _blank(text, c)]
    if not kids or any(c.kind in ("text", "cdata") for c in kids):
        return text[node.start : node.end]  # text inside: exactly as written
    inner = "".join("\n" + INDENT * (depth + 1) + _xml_emit(c, text, depth + 1) for c in kids)
    return node.open_tag + inner + "\n" + INDENT * depth + node.close_tag


def _xml(text: str) -> str:
    items = [n for n in _xml_tree(text) if not _blank(text, n)]
    return "\n".join(_xml_emit(n, text, 0) for n in items)


def tidy(language_id: str, text: str) -> str:
    """The tidied text. Raises TidyError while the check finds a mistake."""
    if language_id not in TIDY:
        raise TidyError("Tidy works on JSON and XML files.")
    problems = check(language_id, text)
    if problems:
        p = problems[0]
        raise TidyError(f"Fix the mistake on line {p.line} first: {p.message}.")
    body = _json(text) if language_id == "json" else _xml(text)
    return body + ("\n" if text.endswith("\n") and not body.endswith("\n") else "")
