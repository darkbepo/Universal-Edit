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
    assert all(data[k] for k in ("normalise", "markdown", "txt", "docx", "cases"))


def test_markdown_vectors_reopen_unchanged():
    """Saving a Markdown file nobody edited must not change it: what the writer makes reads
    back to the HTML of the first open."""
    from umc.doceditor.html_subset import normalise_html_report
    from umc.doceditor.io import md_to_html

    data = json.loads(VECTORS.read_bytes().decode("utf-8"))
    for v in data["markdown"]:
        assert normalise_html_report(md_to_html(v["md_out"]))[0] == v["html"], v["md"]
