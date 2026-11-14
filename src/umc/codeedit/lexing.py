"""Colouring code one line at a time (the desktop code editor colours each text block on its own,
so the lexer's state has to survive from one line to the next).

Pygments' ``RegexLexer`` keeps its state stack inside ``get_tokens_unprocessed``;
``_run_regex_lexer`` is a copy of that loop (Pygments, BSD-2-Clause, © the Pygments authors)
that starts from a given stack and hands the final one back. Each distinct state gets a small
int (``LineLexer`` interns them), which is what a Qt text block can store. Lexers of other
kinds, or any trouble, fall back to lexing the line on its own."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from pygments.lexer import ExtendedRegexLexer, RegexLexer
from pygments.lexers import get_lexer_by_name
from pygments.token import Error, Token, Whitespace, _TokenType
from pygments.util import ClassNotFound

from umc.codeedit.languages import LANGUAGES, PLAIN

KINDS: tuple[str, ...] = (
    "keyword",
    "string",
    "comment",
    "number",
    "function",
    "type",
    "tag",
    "attribute",
    "operator",
    "builtin",
)

# Most specific first: a token takes the kind of the first entry it is (a child of).
_KIND_OF: tuple[tuple[_TokenType, str], ...] = (
    (Token.Keyword.Type, "type"),
    (Token.Keyword, "keyword"),
    (Token.Comment, "comment"),
    (Token.Literal.String, "string"),
    (Token.Literal.Number, "number"),
    (Token.Name.Function, "function"),
    (Token.Name.Class, "function"),
    (Token.Name.Decorator, "function"),
    (Token.Name.Builtin, "builtin"),
    (Token.Name.Tag, "tag"),
    (Token.Name.Attribute, "attribute"),
    (Token.Operator, "operator"),
)


@dataclass(frozen=True)
class Span:
    start: int
    length: int
    kind: str  # one of KINDS


def kind_of(token_type: _TokenType) -> str | None:
    """The colour kind of a Pygments token type (None = plain text)."""
    for parent, kind in _KIND_OF:
        if token_type in parent:
            return kind
    return None


def _run_regex_lexer(
    lexer: RegexLexer, text: str, stack: tuple[str, ...]
) -> tuple[list[tuple[int, _TokenType, str]], tuple[str, ...]]:
    """RegexLexer.get_tokens_unprocessed from ``stack``; returns the tokens and the final stack."""
    tokens: list[tuple[int, _TokenType, str]] = []
    pos = 0
    tokendefs = lexer._tokens
    statestack = list(stack)
    statetokens = tokendefs[statestack[-1]]
    while True:
        for rexmatch, action, new_state in statetokens:
            m = rexmatch(text, pos)
            if m:
                if action is not None:
                    if type(action) is _TokenType:
                        tokens.append((pos, action, m.group()))
                    else:
                        tokens.extend(action(lexer, m))
                pos = m.end()
                if new_state is not None:
                    if isinstance(new_state, tuple):
                        for state in new_state:
                            if state == "#pop":
                                if len(statestack) > 1:
                                    statestack.pop()
                            elif state == "#push":
                                statestack.append(statestack[-1])
                            else:
                                statestack.append(state)
                    elif isinstance(new_state, int):
                        if abs(new_state) >= len(statestack):
                            del statestack[1:]
                        else:
                            del statestack[new_state:]
                    elif new_state == "#push":
                        statestack.append(statestack[-1])
                    else:
                        raise ValueError(f"wrong state def: {new_state!r}")
                    statetokens = tokendefs[statestack[-1]]
                break
        else:
            if pos >= len(text):
                break
            if text[pos] == "\n":  # only at the very end here: the line's own newline
                tokens.append((pos, Whitespace, "\n"))
                pos += 1
                continue
            tokens.append((pos, Error, text[pos]))
            pos += 1
    return tokens, tuple(statestack)


def _spans(tokens: Iterator[tuple[int, _TokenType, str]] | list, limit: int) -> list[Span]:
    spans: list[Span] = []
    for pos, ttype, value in tokens:
        kind = kind_of(ttype)
        if kind is None or not value or pos >= limit:
            continue
        length = min(len(value), limit - pos)
        last = spans[-1] if spans else None
        if last and last.kind == kind and last.start + last.length == pos:
            spans[-1] = Span(last.start, last.length + length, kind)
        else:
            spans.append(Span(pos, length, kind))
    return spans


_ROOT: tuple[tuple[str, ...], bool] = (("root",), False)


class LineLexer:
    """Lexes one line at a time; ``lex(line, state)`` → (spans, state after the line).

    A state is (Pygments stack, inside a block comment?). The second part exists because many
    Pygments lexers match a block comment (``/* … */``, ``<!-- … -->``) with one regex, which a
    single line can't satisfy; the language table's ``block_comment`` marks are followed here."""

    def __init__(self, language_id: str) -> None:
        lang = next((lang for lang in LANGUAGES if lang.id == language_id), PLAIN)
        self.block = lang.block_comment
        self.plain = lang.pygments == "text"
        try:
            self.lexer = get_lexer_by_name(lang.pygments, stripnl=False, ensurenl=False)
        except ClassNotFound:
            self.lexer, self.plain = None, True
        self.keeps_state = isinstance(self.lexer, RegexLexer) and not isinstance(
            self.lexer, ExtendedRegexLexer
        )
        self._states: list[tuple[tuple[str, ...], bool]] = [_ROOT]
        self._ids: dict[tuple[tuple[str, ...], bool], int] = {_ROOT: 0}

    def _state_id(self, state: tuple[tuple[str, ...], bool]) -> int:
        if state not in self._ids:
            self._ids[state] = len(self._states)
            self._states.append(state)
        return self._ids[state]

    def _lex_code(self, line: str, stack: tuple[str, ...]) -> tuple[list[Span], tuple[str, ...]]:
        text = line + "\n"  # end-of-line rules (comments to the end, …) need the newline
        if self.keeps_state:
            try:
                tokens, end = _run_regex_lexer(self.lexer, text, stack)
                return _spans(tokens, len(line)), end
            except Exception:
                pass  # fall back to the line on its own
        try:
            return _spans(self.lexer.get_tokens_unprocessed(text), len(line)), ("root",)
        except Exception:
            return [], ("root",)

    def _open_comment(self, line: str, spans: list[Span]) -> int | None:
        """Where a block comment starts that this line doesn't close (outside strings)."""
        if not self.block:
            return None
        opener, closer = self.block
        start = line.find(opener)
        while start >= 0:
            inside = any(
                s.kind in ("string", "comment") and s.start <= start < s.start + s.length
                for s in spans
            )
            if not inside and line.find(closer, start + len(opener)) < 0:
                return start
            start = line.find(opener, start + 1)
        return None

    def lex(self, line: str, state: int) -> tuple[list[Span], int]:
        if self.plain or self.lexer is None:
            return [], 0
        stack, in_comment = self._states[state] if 0 <= state < len(self._states) else _ROOT
        spans: list[Span] = []
        offset = 0
        if in_comment:
            end = line.find(self.block[1]) if self.block else 0
            if end < 0:
                return ([Span(0, len(line), "comment")] if line else []), state
            offset = end + len(self.block[1])
            spans.append(Span(0, offset, "comment"))
        rest = line[offset:]
        code, stack = self._lex_code(rest, stack)
        cut = self._open_comment(rest, code)
        if cut is not None:
            code = [s for s in code if s.start < cut]
            code = [Span(s.start, min(s.length, cut - s.start), s.kind) for s in code]
            code.append(Span(cut, len(rest) - cut, "comment"))
        spans += [Span(s.start + offset, s.length, s.kind) for s in code]
        return spans, self._state_id((stack, cut is not None))
