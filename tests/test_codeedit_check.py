"""JSON / YAML / XML checks for the code editor (same cases as web/tests/codeedit-check.test.ts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umc.codeedit.check import CHECKED, check

CASES = json.loads(
    (Path(__file__).parent / "fixtures/codeedit_check_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_shared_cases(case):
    problems = check(case["language"], case["text"])
    if case.get("ok"):
        assert problems == []
        return
    assert problems, case["id"]
    first = problems[0]
    assert first.line == case["line"]
    if "column" in case:
        assert first.column == case["column"]
    assert first.message and "\n" not in first.message
    assert "Traceback" not in first.message and "<block" not in first.message


def test_checked_languages():
    assert {"json", "yaml", "xml", "svg"} <= CHECKED
    assert "css" not in CHECKED
