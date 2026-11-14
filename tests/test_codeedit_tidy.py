"""Tidy for the code editor (same cases as web/tests/codeedit-tidy.test.ts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umc.codeedit.tidy import TIDY, TidyError, tidy

CASES = json.loads(
    (Path(__file__).parent / "fixtures/codeedit_tidy_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_shared_cases(case):
    if case.get("error"):
        with pytest.raises(TidyError, match="line"):
            tidy(case["language"], case["text"])
    else:
        assert tidy(case["language"], case["text"]) == case["result"]


def test_tidy_twice_changes_nothing():
    for case in CASES:
        if not case.get("error"):
            once = tidy(case["language"], case["text"])
            assert tidy(case["language"], once) == once, case["id"]


def test_tidy_languages():
    assert {"json", "xml", "svg"} == TIDY
