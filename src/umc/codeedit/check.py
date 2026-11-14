"""Checks for JSON / YAML / XML in the code editor: where the first mistake is, in plain words.

JSON is read by our own small scanner (``scan_json``, ported line by line to
``web/src/codeedit/check.ts``) so both apps point at the same line and column; browsers' own
``JSON.parse`` messages and positions differ. YAML and XML use each app's library (PyYAML /
lxml here); the shared cases (``tests/fixtures/codeedit_check_cases.json``) only hold errors both
libraries place on the same line."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHECKED: frozenset[str] = frozenset({"json", "yaml", "xml", "svg"})
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_WS = " \t\r\n"


@dataclass(frozen=True)
class Problem:
    line: int  # 1-based
    column: int  # 1-based; 0 = the whole line
    message: str


class JsonError(ValueError):
    def __init__(self, pos: int, message: str) -> None:
        super().__init__(message)
        self.pos = pos
        self.message = message


@dataclass(frozen=True)
class Token:
    kind: str  # "{", "}", "[", "]", ":", ",", "string", "number", "literal"
    text: str  # exactly as written


def _line_col(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    return line, pos - (text.rfind("\n", 0, pos) + 1) + 1


def scan_json(text: str) -> list[Token]:
    """The file's tokens, or JsonError at the first mistake. An empty file is fine ([])."""
    tokens: list[Token] = []
    n = len(text)
    pos = 0

    def ws() -> None:
        nonlocal pos
        while pos < n and text[pos] in _WS:
            pos += 1

    def string() -> None:
        nonlocal pos
        start = pos
        pos += 1
        while pos < n:
            ch = text[pos]
            if ch == '"':
                pos += 1
                tokens.append(Token("string", text[start:pos]))
                return
            if ch == "\\":
                nxt = text[pos + 1 : pos + 2]
                if nxt == "u":
                    if not re.fullmatch(r"[0-9a-fA-F]{4}", text[pos + 2 : pos + 6]):
                        raise JsonError(pos, "A \\u code needs four hex digits")
                    pos += 6
                    continue
                if nxt and nxt in '"\\/bfnrt':
                    pos += 2
                    continue
                raise JsonError(pos, "Unknown \\ code in a string")
            if ch == "\n" or ord(ch) < 0x20:
                break
            pos += 1
        raise JsonError(start, "Missing closing quote")

    def value(in_object_key: bool = False) -> None:
        nonlocal pos
        ws()
        if pos >= n:
            raise JsonError(pos, "Missing value")
        ch = text[pos]
        if ch == "{":
            obj()
        elif ch == "[":
            arr()
        elif ch == '"':
            string()
        elif ch == "-" or ch.isdigit():
            m = _NUMBER.match(text, pos)
            end = m.end() if m else pos
            if not m or (end < n and (text[end].isdigit() or text[end] in ".eE")):
                raise JsonError(end if m else pos, "That number isn't valid")
            tokens.append(Token("number", text[pos:end]))
            pos = end
        else:
            for word in ("true", "false", "null"):
                if text.startswith(word, pos):
                    tokens.append(Token("literal", word))
                    pos += len(word)
                    return
            raise JsonError(pos, "Missing value")

    def obj() -> None:
        nonlocal pos
        tokens.append(Token("{", "{"))
        pos += 1
        ws()
        if pos < n and text[pos] == "}":
            tokens.append(Token("}", "}"))
            pos += 1
            return
        while True:
            ws()
            if pos < n and text[pos] == "}" and tokens[-1].kind == ",":
                raise JsonError(pos, "Trailing comma")
            if pos >= n or text[pos] != '"':
                raise JsonError(pos, "A key must be in double quotes")
            string()
            ws()
            if pos >= n or text[pos] != ":":
                raise JsonError(pos, "Missing colon after the key")
            tokens.append(Token(":", ":"))
            pos += 1
            value()
            ws()
            if pos < n and text[pos] == ",":
                tokens.append(Token(",", ","))
                pos += 1
                continue
            if pos < n and text[pos] == "}":
                tokens.append(Token("}", "}"))
                pos += 1
                return
            raise JsonError(pos, "Missing comma or closing bracket")

    def arr() -> None:
        nonlocal pos
        tokens.append(Token("[", "["))
        pos += 1
        ws()
        if pos < n and text[pos] == "]":
            tokens.append(Token("]", "]"))
            pos += 1
            return
        while True:
            ws()
            if pos < n and text[pos] == "]" and tokens[-1].kind == ",":
                raise JsonError(pos, "Trailing comma")
            value()
            ws()
            if pos < n and text[pos] == ",":
                tokens.append(Token(",", ","))
                pos += 1
                continue
            if pos < n and text[pos] == "]":
                tokens.append(Token("]", "]"))
                pos += 1
                return
            raise JsonError(pos, "Missing comma or closing bracket")

    ws()
    if pos >= n:
        return []
    value()
    ws()
    if pos < n:
        raise JsonError(pos, "Unexpected text after the end")
    return tokens


def _check_json(text: str) -> list[Problem]:
    try:
        scan_json(text)
    except JsonError as e:
        line, col = _line_col(text, e.pos)
        return [Problem(line, col, e.message)]
    return []


def _yaml_message(problem: str) -> str:
    p = problem.lower()
    if "'\\t'" in p or "tab" in p:
        return "Tabs can't be used for indenting in YAML"
    if "end of stream" in p:
        return "Missing closing quote or bracket"
    if "expected ',' or ']'" in p or "expected ',' or '}'" in p:
        return "Missing comma or closing bracket"
    if "mapping values are not allowed" in p:
        return "A colon is in the wrong place (put the text in quotes?)"
    return "This line doesn't fit the indenting around it"


def _check_yaml(text: str) -> list[Problem]:
    import yaml

    try:
        for _ in yaml.safe_load_all(text):
            pass
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark else 1
        return [Problem(line, 0, _yaml_message(str(e.problem or e.context or "")))]
    except yaml.YAMLError:
        return [Problem(1, 0, "This isn't valid YAML")]
    return []


def _xml_message(msg: str) -> str:
    if "tag mismatch" in msg:
        return "The closing tag doesn't match the open one"
    if "EntityRef" in msg or "entity" in msg.lower():
        return "A & must be written as &amp;"
    if "Extra content" in msg:
        return "Only one top element is allowed"
    if "Premature end" in msg:
        return "A tag isn't closed"
    if "attribute" in msg.lower():
        return "An attribute isn't written right"
    return "This isn't valid XML"


_DECL_ENCODING = re.compile(r"(<\?xml[^>]*?)\s+encoding\s*=\s*(['\"])[^'\"]*\2", re.S)


def _check_xml(text: str) -> list[Problem]:
    from lxml import etree

    # the text is already decoded: drop the declared encoding, it would clash with UTF-8 bytes
    data = _DECL_ENCODING.sub(r"\1", text, count=1).encode("utf-8")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        return [Problem(e.lineno or 1, 0, _xml_message(e.msg or ""))]
    return []


def check(language_id: str, text: str) -> list[Problem]:
    """The mistakes in ``text`` (only the first one); [] when fine or not a checked language."""
    if language_id == "json":
        return _check_json(text)
    if language_id == "yaml":
        return _check_yaml(text)
    if language_id in ("xml", "svg"):
        if not text.strip():
            return []
        return _check_xml(text)
    return []
