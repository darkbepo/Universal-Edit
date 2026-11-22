"""YAML text ↔ a tree of values (port: web/src/dataedit/yamltree.ts).

Each app's YAML library only reads the structure (PyYAML ``compose`` here, the ``yaml``
package's failsafe schema on the web): every scalar arrives as its text plus whether it was
quoted. Our own ``resolve`` then decides its kind, the same in both apps — numbers keep their
spelling (``Num``), so nothing is rounded when the file is written back. Booleans follow
PyYAML (YAML 1.1: yes / no / on / off too), as the data converter does.

Written by our own block-style writer with the file's indentation; a string is quoted only
when ``resolve`` would read it back as something else. Comments can't be kept."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from umc.dataedit import jsontree
from umc.dataedit.cells import WS, Num

MAX_DEPTH = 256

_NULL = frozenset({"", "~", "null", "Null", "NULL"})
_TRUE = frozenset({"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"})
_FALSE = frozenset({"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"})
_NUMBER = re.compile(
    r"[-+]?[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?"  # 12, 1_000, 1.5, 1., 2e3
    r"|[-+]?\.[0-9][0-9_]*(?:[eE][-+]?[0-9]+)?"  # .5
    r"|[-+]?0x[0-9a-fA-F_]+|[-+]?0o[0-7_]+|[-+]?0b[01_]+"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
)


class YamlError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line = line  # 1-based, 0 = unknown
        self.message = message


@dataclass
class Parsed:
    root: Any
    indent: str
    start_marker: bool  # the file began with "---"
    lost: list[str] = field(default_factory=list)


def resolve(text: str) -> Any:
    """A plain (unquoted) scalar's value."""
    if text in _NULL:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if _NUMBER.fullmatch(text):
        return Num(text)
    return text


def parse(text: str) -> Parsed:
    """Raises YamlError (several documents, odd keys, the library's own complaints)."""
    import yaml

    try:
        docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        raise YamlError(mark.line + 1 if mark else 0, exc.problem or "Not valid YAML") from exc
    except yaml.YAMLError as exc:
        raise YamlError(0, str(exc)) from exc
    if len(docs) > 1:
        raise YamlError(0, "The file holds several YAML documents; the editor opens one")
    root = _node(docs[0], 0) if docs else None
    return Parsed(root, detect_indent(text), _starts_with_marker(text), lost_parts(text))


def _node(node: Any, depth: int) -> Any:
    import yaml

    if depth > MAX_DEPTH:
        raise YamlError(0, f"Nested too deeply (more than {MAX_DEPTH} levels)")
    if isinstance(node, yaml.ScalarNode):
        return resolve(node.value) if node.style is None else node.value
    if isinstance(node, yaml.SequenceNode):
        return [_node(x, depth + 1) for x in node.value]
    out: dict[str, Any] = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode):
            raise YamlError(key.start_mark.line + 1, "A key that is a list or a map can't be shown")
        out[key.value] = _node(value, depth + 1)
    return out


def _starts_with_marker(text: str) -> bool:
    for line in text.split("\n"):
        stripped = line.strip(WS)
        if stripped and not stripped.startswith("#") and not stripped.startswith("%"):
            return stripped == "---" or stripped.startswith("--- ")
    return False


def lost_parts(text: str) -> list[str]:
    """What saving an edited file can't keep (a # at a line start or after a space)."""
    return ["comments"] if re.search(r"(?:^|[ \t])#", text, re.M) else []


def detect_indent(text: str) -> str:
    m = re.search(r"\n( +)[^ \n#]", text)
    return m[1] if m else "  "


# ---------------------------------------------------------------------------- writing
_PLAIN_START = re.compile(r"[^\s\-?:,\[\]{}#&*!|>'\"%@`]")


def _plain_ok(text: str, key: bool = False) -> bool:
    """Whether ``text`` can be written unquoted and read back as this same text (a key only
    needs to be safe to write: whether it was quoted isn't kept)."""
    if not text or not _PLAIN_START.match(text) or text != text.strip(WS):
        return False
    if any(ord(ch) < 0x20 or ch in "\x7f﻿" or 0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        return False
    if ": " in text or " #" in text or text.endswith(":"):
        return False
    if text.startswith(("---", "...")):
        return False
    return key or resolve(text) == text


def _quote(text: str) -> str:
    """A double-quoted YAML string (JSON escapes are valid there)."""
    return jsontree.quote(text)


def scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Num):
        return value.text
    return value if _plain_ok(value) else _quote(value)


def _empty(value: Any) -> str | None:
    if isinstance(value, dict) and not value:
        return "{}"
    if isinstance(value, list) and not value:
        return "[]"
    return None if isinstance(value, dict | list) else scalar(value)


def _lines(value: Any, pad: str, indent: str) -> list[str]:
    """A non-empty map or list, each line starting with ``pad``."""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = k if _plain_ok(k, key=True) else _quote(k)
            inline = _empty(v)
            if inline is not None:
                out.append(f"{pad}{key}: {inline}")
            else:
                out.append(f"{pad}{key}:")
                out.extend(_lines(v, pad + indent, indent))
    else:
        for v in value:
            inline = _empty(v)
            if inline is not None:
                out.append(f"{pad}- {inline}")
            else:
                sub = _lines(v, pad + "  ", indent)
                out.append(f"{pad}- {sub[0][len(pad) + 2 :]}")
                out.extend(sub[1:])
    return out


def dumps(root: Any, indent: str = "  ", start_marker: bool = False) -> str:
    """The tree as YAML text with a final newline."""
    inline = _empty(root)
    body = [inline] if inline is not None else _lines(root, "", indent)
    return ("---\n" if start_marker else "") + "\n".join(body) + "\n"
