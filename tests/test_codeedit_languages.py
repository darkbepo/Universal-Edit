"""The code editor's language table (shared with the website as languages.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umc.codeedit import languages
from umc.codeedit.languages import LANGUAGES, PLAIN, language_for

ROOT = Path(__file__).parent.parent


def test_web_copy_matches():
    web = json.loads((ROOT / "web/src/codeedit/languages.json").read_text(encoding="utf-8"))
    assert web == json.loads(json.dumps(languages.export())), "run python -m umc.codeedit.languages"


def test_lookup():
    assert language_for("style.CSS").id == "css"
    assert language_for("app.tsx").id == "typescript"
    assert language_for("Dockerfile").id == "dockerfile"
    assert language_for(".gitignore").id == "gitignore"
    assert language_for("notes.xyz") is PLAIN
    assert language_for("run.bat").line_comment == "REM"


def test_no_extension_twice():
    exts = [e for lang in LANGUAGES for e in lang.exts]
    names = [n for lang in LANGUAGES for n in lang.filenames]
    assert len(exts) == len(set(exts))
    assert len(names) == len(set(names))
    assert all(e == e.lower() and not e.startswith(".") for e in exts)


def test_lexers_exist():
    lexers = pytest.importorskip("pygments.lexers")
    for lang in LANGUAGES:
        lexers.get_lexer_by_name(lang.pygments)
