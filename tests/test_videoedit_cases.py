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


def _colour_ok(c: Any) -> bool:
    return (
        isinstance(c, list) and len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c)
    )


def test_render_sections_are_well_formed():
    for key in ("grade", "transition", "fade", "text"):
        assert CASES[key], key
    assert len(CASES["grade"]) >= 12
    for g in CASES["grade"]:
        assert _colour_ok(g["rgb"]) and _colour_ok(g["out"])
        assert all(isinstance(g[k], int) for k in ("brightness", "contrast", "saturation"))
    kinds = set()
    for tr in CASES["transition"]:
        kinds.add(tr["kind"])
        assert _colour_ok(tr["a"]) and _colour_ok(tr["b"]) and tr["width"] == 64
        assert set(tr["columns"]) == {"0", "16", "32", "48", "63"}
        assert all(_colour_ok(c) for c in tr["columns"].values())
    assert kinds == {k for k, _ in maker.TRANSITIONS if k != "none"}
    for f in CASES["fade"]:
        assert _colour_ok(f["rgb"]) and _colour_ok(f["background"]) and _colour_ok(f["out"])
    anchors = set()
    for t in CASES["text"]:
        anchors.add(t["item"]["anchor"])
        assert t["item"]["type"] == "text" and len(t["size"]) == 2
        assert t["column"] in (0, 1, 2) and t["row"] in (0, 1, 2)
    assert len(anchors) == 9


def test_file_case_matches_save_project():
    maker_file = maker.file_case()
    assert CASES["file"]["text"] == maker_file["text"]
    assert_same(CASES["file"]["project"], maker_file["project"])
