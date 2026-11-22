"""JSON text ↔ values (port: web/src/dataedit/jsontree.ts).

Read through the code editor's scanner, so both apps accept the same files and every number
keeps its spelling (``Num``). Values are dicts (key order kept), lists, text, bools, None and
``Num``. Written back with the file's own indentation (or minified)."""

from __future__ import annotations

import json
import re
from typing import Any

from umc.codeedit.check import Token, scan_json
from umc.dataedit.cells import WS, Num


def parse(text: str) -> Any:
    """Raises codeedit.check.JsonError at the first mistake; an empty text is None."""
    tokens = scan_json(text)
    if not tokens:
        return None
    value, _ = _value(tokens, 0)
    return value


def _value(tokens: list[Token], i: int) -> tuple[Any, int]:
    tok = tokens[i]
    if tok.kind == "{":
        out: dict[str, Any] = {}
        i += 1
        while tokens[i].kind != "}":
            key = json.loads(tokens[i].text)
            out[key], i = _value(tokens, i + 2)  # past the key and ":"
            if tokens[i].kind == ",":
                i += 1
        return out, i + 1
    if tok.kind == "[":
        items: list[Any] = []
        i += 1
        while tokens[i].kind != "]":
            item, i = _value(tokens, i)
            items.append(item)
            if tokens[i].kind == ",":
                i += 1
        return items, i + 1
    if tok.kind == "string":
        return json.loads(tok.text), i + 1
    if tok.kind == "number":
        return Num(tok.text), i + 1
    return {"true": True, "false": False, "null": None}[tok.text], i + 1


_ESCAPE = re.compile(r'["\\\x00-\x1f\ud800-\udfff]')
_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def quote(text: str) -> str:
    """A JSON string; control characters and lone surrogates as \\u codes (lower-case hex)."""
    return '"' + _ESCAPE.sub(lambda m: _SHORT.get(m[0]) or f"\\u{ord(m[0]):04x}", text) + '"'


def dumps(value: Any, indent: str | None, level: int = 0) -> str:
    """``indent`` None = minified (no spaces at all)."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        if indent is None:
            return "{" + ",".join(f"{quote(k)}:{dumps(v, None)}" for k, v in value.items()) + "}"
        pad = "\n" + indent * (level + 1)
        body = ",".join(f"{pad}{quote(k)}: {dumps(v, indent, level + 1)}" for k, v in value.items())
        return "{" + body + "\n" + indent * level + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if indent is None:
            return "[" + ",".join(dumps(v, None) for v in value) + "]"
        pad = "\n" + indent * (level + 1)
        body = ",".join(pad + dumps(v, indent, level + 1) for v in value)
        return "[" + body + "\n" + indent * level + "]"
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, Num):
        return value.text
    if value is None:
        return "null"
    return "true" if value else "false"


def detect_indent(text: str) -> str | None:
    """The file's indentation step: None when it is all on one line."""
    body = text.strip(WS)
    if "\n" not in body:
        return None
    m = re.search(r"\n([ \t]+)\S", body)
    return m.group(1) if m else "  "
