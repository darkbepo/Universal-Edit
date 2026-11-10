"""The shared document editor vectors (also asserted by the website) match the desktop code."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "fixtures" / "doceditor-vectors.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "make_doceditor_vectors", ROOT / "scripts" / "make_doceditor_vectors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vectors_match_desktop():
    assert VECTORS.exists(), "run scripts/make_doceditor_vectors.py"
    expected = _generator().render()
    on_disk = VECTORS.read_bytes().decode("utf-8").replace(chr(13) + chr(10), chr(10))
    assert on_disk == expected, "run scripts/make_doceditor_vectors.py"
    data = json.loads(expected)
    assert all(data[k] for k in ("normalise", "markdown", "txt", "cases"))
