"""The shared cases the website's port replays (tests/fixtures/videoedit_cases.json) must still be
what the desktop code produces. If this fails after a model change, run
``python scripts/make_videoedit_cases.py`` on purpose and commit the new file."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "fixtures" / "videoedit_cases.json").read_text("utf-8"))

_spec = importlib.util.spec_from_file_location(
    "make_videoedit_cases", ROOT / "scripts" / "make_videoedit_cases.py"
)
assert _spec is not None and _spec.loader is not None
maker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(maker)


def assert_same(expected: Any, actual: Any, where: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and expected.keys() == actual.keys(), where
        for k in expected:
            assert_same(expected[k], actual[k], f"{where}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(expected) == len(actual), where
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            assert_same(e, a, f"{where}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, abs=1e-9), where
    else:
        assert expected == actual, where


def test_cases_still_match_the_desktop():
    assert len(CASES["layout"]) >= 8 and len(CASES["edits"]) >= 35
    assert sum(1 for c in CASES["edits"] if c["error"]) >= 8
    for case in CASES["layout"]:
        project = maker.project_from_dict(case["project"])
        got = maker.run_layout(project)
        for key in ("spans", "length", "output_size", "output_fps"):
            assert_same(case[key], got[key], f"{case['name']}.{key}")
    for case in CASES["edits"]:
        got = maker.run_edit(case)
        for key in ("result", "id", "error"):
            assert_same(case[key], got[key], f"{case['name']}.{key}")


def test_file_case_matches_save_project():
    maker_file = maker.file_case()
    assert CASES["file"]["text"] == maker_file["text"]
    assert_same(CASES["file"]["project"], maker_file["project"])
