"""Text editing helpers of the code editor (pure; the website ports them in step 3)."""

from __future__ import annotations

from umc.codeedit.editing import (
    detect_indent,
    indent_lines,
    matching_bracket,
    next_line_indent,
    outdent_lines,
    toggle_comment,
)


def test_detect_indent():
    assert detect_indent("a\n\tb\n\tc") == "\t"
    assert detect_indent("a\n  b\n    c\n  d") == "  "
    assert detect_indent("x") == "    "


def test_indent_and_outdent():
    assert indent_lines(["a", "", "b"], "  ") == ["  a", "", "  b"]
    assert outdent_lines(["    a", "  b", "\tc", "d"], "    ") == ["a", "b", "c", "d"]


def test_toggle_line_comments():
    assert toggle_comment(["x = 1", "  y = 2"], "python") == ["# x = 1", "#   y = 2"]
    assert toggle_comment(["# x = 1", "#   y = 2"], "python") == ["x = 1", "  y = 2"]
    js = ["  a", "    b"]
    assert toggle_comment(js, "javascript") == ["  // a", "  //   b"]
    assert toggle_comment(toggle_comment(js, "javascript"), "javascript") == js


def test_toggle_block_comments():
    css = ["a {}", "b {}"]
    assert toggle_comment(css, "css") == ["/* a {}", "b {} */"]
    assert toggle_comment(["/* a {}", "b {} */"], "css") == css
    assert toggle_comment(["x"], "text") == ["x"]


def test_next_line_indent():
    assert next_line_indent("    if x:", "    ") == "        "
    assert next_line_indent("  a {", "  ") == "    "
    assert next_line_indent("\tfoo()", "\t") == "\t"
    assert next_line_indent("<div>", "  ") == "  "


def test_matching_bracket():
    assert matching_bracket("f(a[1])", 1) == 6
    assert matching_bracket("f(a[1])", 6) == 1
    assert matching_bracket("f(a[1])", 3) == 5
    assert matching_bracket("f(a", 1) is None
    assert matching_bracket("abc", 1) is None
