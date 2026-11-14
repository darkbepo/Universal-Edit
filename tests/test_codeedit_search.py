"""Find and replace for the code editor."""

from __future__ import annotations

import pytest

from umc.codeedit.search import SearchError, find_all, replace_all, replacement_at

PLAIN = {"case": False, "whole_word": False, "regex": False}


def test_find_plain():
    assert find_all("Cat cat cat", "cat", **PLAIN) == [(0, 3), (4, 7), (8, 11)]
    assert find_all("Cat cat cat", "cat", **{**PLAIN, "case": True}) == [(4, 7), (8, 11)]
    assert find_all("a(b", "(", **PLAIN) == [(1, 2)]
    assert find_all("abc", "", **PLAIN) == []


def test_find_whole_word():
    assert find_all("cat catalog", "cat", case=True, whole_word=True, regex=False) == [(0, 3)]


def test_find_regex():
    assert find_all("a1 b22", r"\d+", **{**PLAIN, "regex": True}) == [(1, 2), (4, 6)]
    assert find_all("abc", "x*", **{**PLAIN, "regex": True}) == []
    with pytest.raises(SearchError):
        find_all("abc", "(", **{**PLAIN, "regex": True})


def test_replacement_at():
    regex = {**PLAIN, "regex": True}
    assert replacement_at("x1 y2", r"(\w)(\d)", r"\2\1", 3, **regex) == (3, 5, "2y")
    assert replacement_at("a-b", "-", "+", 1, **PLAIN) == (1, 2, "+")
    assert replacement_at("a-b", "-", "+", 0, **PLAIN) is None  # no match starts there


def test_replace_all():
    assert replace_all("a-b-c", "-", "+", **PLAIN) == ("a+b+c", 2)
    regex = {**PLAIN, "regex": True}
    assert replace_all("x1 y2", r"(\w)(\d)", r"\2\1", **regex) == ("1x 2y", 2)
    assert replace_all("a-b", "-", r"\1", **PLAIN) == (r"a\1b", 1)
    assert replace_all("abc", "", "x", **PLAIN) == ("abc", 0)
