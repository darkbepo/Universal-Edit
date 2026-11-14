"""Byte-exact text files for the code editor (same cases as web/tests/codeedit-textfile.test.ts)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from umc.codeedit.textfile import decode, encode, is_binary, looks_like_text

CASES = json.loads(
    (Path(__file__).parent / "fixtures/codeedit_cases.json").read_text(encoding="utf-8")
)
BY_ID = {c["id"]: c for c in CASES["cases"]}


def _bytes(case: dict) -> bytes:
    return base64.b64decode(case["bytes_b64"])


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_looks_like_text(case):
    assert looks_like_text(_bytes(case)[:8192]) == case["looks_like_text"]


@pytest.mark.parametrize("case", [c for c in CASES["cases"] if "text" in c], ids=lambda c: c["id"])
def test_decode_fields(case):
    f = decode(_bytes(case))
    assert (f.text, f.encoding, f.bom, f.newline, f.mixed) == (
        case["text"],
        case["encoding"],
        case["bom"],
        case["newline"],
        case["mixed"],
    )


@pytest.mark.parametrize("case", [c for c in CASES["cases"] if "text" in c], ids=lambda c: c["id"])
def test_untouched_round_trip_is_identical(case):
    data = _bytes(case)
    f = decode(data)
    assert encode(f, f.text) == data


@pytest.mark.parametrize("resave", CASES["resaves"], ids=lambda c: c["id"])
def test_resave(resave):
    f = decode(_bytes(BY_ID[resave["from"]]))
    got = encode(f, f.text, newline=resave.get("newline"), bom=resave.get("bom"))
    assert got == base64.b64decode(resave["expect_b64"])


def test_edited_text_uses_the_file_newline():
    f = decode(b"one\r\ntwo\r\n")
    assert encode(f, "one\ntwo\nthree\n") == b"one\r\ntwo\r\nthree\r\n"


def test_windows_1252_save():
    f = decode(b"abc")
    assert encode(f, "€x", encoding="windows-1252") == b"\x80x"
    with pytest.raises(ValueError, match="encoding"):
        encode(f, "日本", encoding="windows-1252")
    with pytest.raises(ValueError, match="encoding"):
        encode(f, "\x80", encoding="windows-1252")  # byte 0x80 means "€" there


def test_binary_refused():
    assert is_binary(b"ab\x00cd")
    assert not is_binary(b"\xff\xfeh\x00i\x00")  # UTF-16 with a BOM is text
    with pytest.raises(ValueError, match="binary"):
        decode(b"ab\x00cd")
