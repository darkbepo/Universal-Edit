"""Subtitle editor model: the shared cases (web/tests/subedit.test.ts checks the same file)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from umc.subedit import checks, edits, formats, io
from umc.subedit.formats import Cue, SubtitleError
from umc.subedit.history import History

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "subedit_cases.json").read_text(encoding="utf-8")
)


def cue_json(c: Cue) -> dict:
    return {"start": c.start, "end": c.end, "text": c.text, "extra": [list(kv) for kv in c.extra]}


def from_json(d: dict) -> Cue:
    return Cue(d["start"], d["end"], d["text"], tuple(tuple(kv) for kv in d["extra"]))


@pytest.mark.parametrize("case", CASES["files"], ids=lambda c: c["name"])
def test_file_cases(case: dict) -> None:
    doc = formats.parse(case["text"], case["format"])
    assert doc.format == case["doc"]["format"]
    assert [cue_json(c) for c in doc.cues] == case["doc"]["cues"]
    assert (doc.header, doc.footer, list(doc.fields)) == (
        case["doc"]["header"],
        case["doc"]["footer"],
        case["doc"]["fields"],
    )
    for fmt, want in case["write"].items():
        if isinstance(want, dict):
            with pytest.raises(SubtitleError, match=want["error"]):
                formats.write(doc, fmt)
        else:
            assert formats.write(doc, fmt) == want, fmt
        assert formats.lost_parts(doc, fmt) == case["lost"][fmt]
    assert list(formats.save_formats(doc)) == case["save_formats"]
    assert [asdict(p) for p in checks.check(doc.cues, case["format"])] == case["problems"]


@pytest.mark.parametrize("case", CASES["files"], ids=lambda c: c["name"])
def test_written_files_read_back(case: dict) -> None:
    """What we write, we read again with the same times (ASS / LRC: to 10 ms)."""
    doc = formats.parse(case["text"], case["format"])
    for fmt, text in case["write"].items():
        if isinstance(text, dict):
            continue
        keep_comments = formats.markup_kind(doc.format) == formats.markup_kind(fmt) == "ass"
        shown = [c for c in doc.cues if keep_comments or c.get("kind") != "Comment"]
        if fmt == "lrc":
            shown.sort(key=lambda c: c.start)
        step = 1 if fmt in ("srt", "vtt") else 10
        times = [((c.start + step // 2) // step, (c.end + step // 2) // step) for c in shown]
        again = formats.parse(text, fmt)
        assert [(c.start // step, c.end // step) for c in again.cues] == times, fmt


def _run(op: str, cues: tuple[Cue, ...], a: dict):
    if op == "set_text":
        return edits.set_text(cues, a["i"], a["text"])
    if op == "set_times":
        return edits.set_times(cues, a["i"], start=a.get("start"), end=a.get("end"))
    if op == "set_length":
        return edits.set_length(cues, a["i"], a["length"])
    if op == "shift":
        return edits.shift(cues, a["delta"], a.get("indices"))
    if op == "sync":
        return edits.sync(cues, a["a"], a["a_time"], a["b"], a["b_time"])
    if op == "add":
        return edits.add(cues, a["after"], a.get("length", edits.NEW_CUE_MS))
    if op == "delete":
        return edits.delete(cues, a["indices"])
    if op == "split":
        return edits.split(cues, a["i"], a.get("at"))
    if op == "join":
        return edits.join(cues, a["indices"])
    if op == "sort":
        return edits.sort(cues)
    if op == "replace_all":
        return edits.replace_all(
            cues,
            a["query"],
            a["replacement"],
            case=a["case"],
            whole_word=a["whole_word"],
            regex=a["regex"],
        )
    if op == "cue_at":
        return edits.cue_at(cues, a["ms"])
    raise AssertionError(op)


@pytest.mark.parametrize("case", CASES["edits"], ids=lambda c: c["op"])
def test_edit_cases(case: dict) -> None:
    base = tuple(from_json(c) for c in CASES["base"])
    got = _run(case["op"], base, case["args"])
    want = case["result"]
    if isinstance(want, list):
        assert got == want
    elif "value" in want:
        assert ([cue_json(c) for c in got[0]], got[1]) == (want["cues"], want["value"])
    else:
        assert [cue_json(c) for c in got] == want["cues"]


def test_base_problems_and_markup_and_clock() -> None:
    base = tuple(from_json(c) for c in CASES["base"])
    assert [asdict(p) for p in checks.check(base, "srt")] == CASES["base_problems"]
    for m in CASES["markup"]:
        assert formats.convert_markup(m["text"], m["src"], m["dst"]) == m["out"]
    for ms, text in CASES["clock"]:
        assert formats.clock(ms) == text


def test_sync_refuses_same_start_and_reversal() -> None:
    cues = (Cue(1000, 2000, "a"), Cue(1000, 3000, "b"), Cue(5000, 6000, "c"))
    with pytest.raises(ValueError, match="same"):
        edits.sync(cues, 0, 0, 1, 500)
    with pytest.raises(ValueError, match="order"):
        edits.sync(cues, 0, 5000, 2, 1000)


def test_untouched_file_saves_the_same_bytes() -> None:
    for case in CASES["files"]:
        if case["format"] not in formats.WRITE_FORMATS:
            continue
        for raw in (
            case["text"].replace("\n", "\r\n").encode("utf-8"),
            b"\xef\xbb\xbf" + case["text"].encode("utf-8"),
        ):
            opened = io.open_bytes(raw, case["format"])
            assert io.to_bytes(opened, opened.doc, case["format"]).data == raw


def test_edited_file_keeps_encoding_and_line_endings() -> None:
    raw = "1\r\n00:00:01,000 --> 00:00:02,000\r\nCaf\xe9\r\n".encode("cp1252")
    opened = io.open_bytes(raw, "srt")
    assert opened.doc.cues[0].text == "Café"
    doc = formats.SubDoc("srt", edits.shift(opened.doc.cues, 500))
    saved = io.to_bytes(opened, doc, "srt")
    assert saved.data == "1\r\n00:00:01,500 --> 00:00:02,500\r\nCaf\xe9\r\n".encode("cp1252")
    assert not saved.utf8
    # text Windows-1252 can't hold → UTF-8, and VTT is always UTF-8
    doc2 = formats.SubDoc("srt", edits.set_text(opened.doc.cues, 0, "日本"))
    out = io.to_bytes(opened, doc2, "srt")
    assert out.utf8 and "日本".encode() in out.data
    vtt = io.to_bytes(opened, opened.doc, "vtt")
    assert vtt.utf8 and vtt.data.startswith(b"WEBVTT\r\n\r\n") and "Café".encode() in vtt.data


def test_reopen_with_charset_saves_utf8() -> None:
    raw = "1\n00:00:01,000 --> 00:00:02,000\nПривет\n".encode("windows-1251")
    plain = io.open_bytes(raw, "srt")
    assert plain.doc.cues[0].text != "Привет"
    opened = io.open_bytes(raw, "srt", charset="windows-1251")
    assert opened.doc.cues[0].text == "Привет"
    saved = io.to_bytes(opened, opened.doc, "srt")
    assert saved.utf8 and saved.data == "1\n00:00:01,000 --> 00:00:02,000\nПривет\n".encode()


def test_bad_files_are_refused_in_plain_words() -> None:
    with pytest.raises(SubtitleError, match="WEBVTT"):
        io.open_bytes(b"00:01.000 --> 00:02.000\nx\n", "vtt")
    with pytest.raises(SubtitleError, match="Events"):
        io.open_bytes(b"[Script Info]\n", "ass")
    with pytest.raises(SubtitleError, match="text"):
        io.open_bytes(b"\x00\x01\x02", "srt")
    with pytest.raises(SubtitleError, match="too big"):
        io.open_bytes(b" " * (io.MAX_OPEN_BYTES + 1), "srt")


def test_new_document_and_history() -> None:
    opened = io.new_document("srt")
    assert opened.doc.cues == ()
    h = History(opened.doc)
    cues, at = edits.add(h.doc.cues, -1)
    h.push(formats.SubDoc("srt", edits.set_text(cues, at, "hi")), "Add")
    assert h.dirty and h.can_undo
    assert h.undo() == "Add" and not h.dirty
    assert h.redo() == "Add" and h.doc.cues[0].text == "hi"
    h.mark_saved()
    assert not h.dirty
    assert io.to_bytes(opened, h.doc, "srt").data == b"1\n00:00:00,000 --> 00:00:02,000\nhi\n"


def test_typing_in_one_cue_is_one_undo_step() -> None:
    doc = formats.SubDoc("srt", (Cue(0, 1000, ""), Cue(1000, 2000, "")))
    h = History(doc)
    for text in ("h", "hi", "hi!"):
        h.push(formats.SubDoc("srt", edits.set_text(h.doc.cues, 0, text)), "Edit text", (0,))
    h.push(formats.SubDoc("srt", edits.set_text(h.doc.cues, 1, "x")), "Edit text", (1,))
    assert h.undo() and h.doc.cues[0].text == "hi!" and h.doc.cues[1].text == ""
    assert h.undo() and h.doc == doc and not h.can_undo
    h.redo()
    h.push(formats.SubDoc("srt", edits.set_text(h.doc.cues, 0, "again")), "Edit text", (0,))
    assert h.undo() and h.doc.cues[0].text == "hi!"  # an undo ends the merging run
