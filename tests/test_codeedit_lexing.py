"""Line-by-line colouring for the code editor (Pygments with the lexer state kept per line)."""

from __future__ import annotations

from pygments.token import Token

from umc.codeedit import lexing
from umc.codeedit.languages import LANGUAGES
from umc.codeedit.lexing import LineLexer, kind_of


def _lex_all(language: str, lines: list[str]):
    lexer = LineLexer(language)
    state, out = 0, []
    for line in lines:
        spans, state = lexer.lex(line, state)
        out.append((spans, state))
    return out


def _kinds_at(spans, line: str) -> list[str | None]:
    """The kind of every character of the line."""
    kinds: list[str | None] = [None] * len(line)
    for s in spans:
        for i in range(s.start, s.start + s.length):
            kinds[i] = s.kind
    return kinds


def test_css_block_comment_spans_lines():
    lines = ["a { color: red; } /* one", "two", "three */ b {}"]
    result = _lex_all("css", lines)
    assert [(s.start, s.length, s.kind) for s in result[1][0]] == [(0, 3, "comment")]
    third = _kinds_at(result[2][0], lines[2])
    assert set(third[: len("three */")]) == {"comment"}
    assert third[lines[2].index("b")] != "comment"


def test_python_triple_quoted_string():
    lines = ['x = """a', "b", '"""', "y = 1"]
    result = _lex_all("python", lines)
    assert set(_kinds_at(result[1][0], "b")) == {"string"}
    assert _kinds_at(result[3][0], lines[3])[4] == "number"


def test_same_stack_same_state():
    lines = ["a {} /* start", "middle one", "middle two", "end */", "b {}"]
    states = [s for _spans, s in _lex_all("css", lines)]
    assert states[1] == states[2] != 0
    assert states[3] == 0 and states[4] == 0


def test_a_quote_after_an_open_comment_doesnt_leak():
    lines = ['int a; /* "x', "still comment", "*/ int b = 2;", "int c = 3;"]
    result = _lex_all("java", lines)
    last = _kinds_at(result[3][0], lines[3])
    assert "string" not in last and "number" in last


def test_a_very_long_line_is_skipped_quickly():
    import time

    lexer = LineLexer("javascript")
    line = "var a=1;" * 200_000  # 1.6 MB minified, one line
    start = time.perf_counter()
    spans, state = lexer.lex(line, 0)
    assert time.perf_counter() - start < 0.2
    assert spans == [] and state == 0


def test_kinds():
    assert kind_of(Token.Keyword.Type) == "type"
    assert kind_of(Token.Keyword) == "keyword"
    assert kind_of(Token.Name.Function) == "function"
    assert kind_of(Token.Comment.Single) == "comment"
    assert kind_of(Token.Literal.String.Double) == "string"
    assert kind_of(Token.Text) is None


def test_plain_text_has_no_spans():
    assert LineLexer("text").lex("anything at all", 0) == ([], 0)


def test_lexer_trouble_falls_back(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lexing, "_run_regex_lexer", broken)
    spans, state = LineLexer("css").lex("a { color: red }", 0)
    assert spans and state == 0


def test_every_language_lexes():
    for lang in LANGUAGES:
        spans, state = LineLexer(lang.id).lex("a = 1 // x", 0)
        assert isinstance(spans, list) and isinstance(state, int), lang.id
