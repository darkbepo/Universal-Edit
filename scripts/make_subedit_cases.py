"""Write tests/fixtures/subedit_cases.json from the desktop subtitle model.

Both test suites (tests/test_subedit.py, web/tests/subedit.test.ts) check these cases, so the
web port must give the same cues, files, problems and edits. Regenerate only on purpose:
    python scripts/make_subedit_cases.py
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from umc.subedit import checks, edits, formats

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "subedit_cases.json"

ASS = """[Script Info]
; made by hand
ScriptType: v4.00+
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: Top,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,\
10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.20,Top,Ann,0,0,0,,{\\i1}Hi{\\i0}, you\\Nthere, {\\pos(10,20)}ok
Comment: 0,0:00:02.00,0:00:02.50,Top,,0,0,0,,a note
Dialogue: 1,0:00:04.05,0:00:05.00,Top,,0,0,0,,Comma, in, text

[Fonts]
fontname: x.ttf
"""

SSA = """[Script Info]
ScriptType: v4.00

[V4 Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, \
Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, \
Encoding
Style: Default,Arial,20,16777215,65535,65535,0,0,0,1,2,0,2,10,10,10,0,0

[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: Marked=0,0:00:00.50,0:00:01.50,Default,,0000,0000,0000,,Old {\\b1}style{\\b0}
"""

FILES = [
    (
        "srt basic",
        "srt",
        "1\n00:00:01,000 --> 00:00:02,500\n<i>Hello</i> there\nsecond <font color=red>line</font>"
        "\n\n2\n00:00:03,000 --> 00:00:04,000 X1:10 X2:20\nBye & so\n",
    ),
    (
        "srt loose",
        "srt",
        "\n\n1\n0:00:01.5 --> 0:00:02,25\nshort hours\n\n\n3\n00:00:05,000 --> 00:00:09,000\n"
        "line one\n\nafter a blank\n\n00:00:10,000 --> 00:00:10,400\nno index\n",
    ),
    (
        "vtt",
        "vtt",
        "WEBVTT - film\nKind: captions\n\nNOTE first note\n\nSTYLE\n::cue { color: red }\n\n"
        "intro\n00:01.000 --> 00:02.000 line:0 align:start\n<v Bob>Hi <c.loud>there</c></v>\n\n"
        "01:00:00.000 --> 01:00:01.000\n<b>bold</b> <00:01:00.500>karaoke\n\n"
        "NOTE late note\n\n00:00:03.000 --> 00:00:03.500\nfast fast fast fast fast fast\n",
    ),
    ("ass", "ass", ASS),
    ("ssa", "ssa", SSA),
    (
        "lrc",
        "lrc",
        "[ti:Song]\n[ar:Someone]\n[00:01.00]First\n[00:03.50][01:00.00]Twice\n[00:05.00]\n"
        "[00:06.5]Third <00:07.00>word\n[00:20.123]Last\n",
    ),
    (
        "smi",
        "smi",
        "<SAMI><HEAD><TITLE>x</TITLE></HEAD><BODY>\n<SYNC Start=1000><P Class=ENCC>Hello<br>"
        "<i>world</i>\n<SYNC Start=2500><P Class=ENCC>&nbsp;\n<SYNC Start=3000><P>A &amp; B\n"
        "</BODY></SAMI>\n",
    ),
    ("srt empty", "srt", ""),
    (
        "ass hidden events",
        "ass",
        "[Script Info]\nScriptType: v4.00+\n\n[Events]\n; made by hand\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "; a note\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Kept\n"
        "Dialogue: 0,bad,0:00:03.00,Default,,0,0,0,,Unreadable time\n"
        "Picture: 0,0:00:04.00,0:00:05.00,Default,,0,0,0,,logo.png\n",
    ),
    (
        "vtt notes between cues",
        "vtt",
        "WEBVTT\n\n00:01.000 --> 00:02.000\nOne\n\nNOTE after one\n\nNOTE\nsecond note\n\n"
        "id2\n00:03.000 --> 00:04.000\nTwo\n\nNOTE at the end\n",
    ),
]


def cue_json(c: formats.Cue) -> dict:
    return {"start": c.start, "end": c.end, "text": c.text, "extra": [list(kv) for kv in c.extra]}


def doc_json(d: formats.SubDoc) -> dict:
    return {
        "format": d.format,
        "cues": [cue_json(c) for c in d.cues],
        "header": d.header,
        "footer": d.footer,
        "fields": list(d.fields),
    }


def file_case(name: str, fmt: str, text: str) -> dict:
    doc = formats.parse(text, fmt)
    writes, lost = {}, {}
    for out in ("srt", "vtt", "ass", "ssa", "lrc"):
        try:
            writes[out] = formats.write(doc, out)
        except formats.SubtitleError as e:
            writes[out] = {"error": str(e)}
        lost[out] = formats.lost_parts(doc, out)
    return {
        "name": name,
        "format": fmt,
        "text": text,
        "doc": doc_json(doc),
        "write": writes,
        "lost": lost,
        "save_formats": list(formats.save_formats(doc)),
        "problems": [asdict(p) for p in checks.check(doc.cues, fmt)],
    }


def C(start: int, end: int, text: str, extra: tuple = ()) -> formats.Cue:
    return formats.Cue(start, end, text, extra)


BASE = (
    C(1000, 2000, "One two three four"),
    C(2500, 4000, "Line a\nLine b", (("id", "x"), ("kind", "Dialogue"))),
    C(5000, 5000, ""),
    C(4500, 9000, "Overlapping text that is quite a lot longer than forty two characters"),
)


NOTED = BASE[:1] + (C(2500, 4000, "Line a\nLine b", (("id", "x"), ("after", "NOTE n"))),)


def edit_cases() -> list[dict]:
    cases: list[tuple[str, dict, object]] = [
        ("set_text", {"i": 0, "text": "new"}, lambda: edits.set_text(BASE, 0, "new")),
        ("set_times", {"i": 0, "start": 2500}, lambda: edits.set_times(BASE, 0, start=2500)),
        ("set_times", {"i": 1, "end": 2000}, lambda: edits.set_times(BASE, 1, end=2000)),
        ("set_times", {"i": 1, "start": -5}, lambda: edits.set_times(BASE, 1, start=-5)),
        ("set_length", {"i": 1, "length": 700}, lambda: edits.set_length(BASE, 1, 700)),
        ("shift", {"delta": 1500}, lambda: edits.shift(BASE, 1500)),
        ("shift", {"delta": -3000}, lambda: edits.shift(BASE, -3000)),
        ("shift", {"delta": -3000, "indices": [1, 3]}, lambda: edits.shift(BASE, -3000, [1, 3])),
        ("sync", {"a": 0, "a_time": 1500, "b": 3, "b_time": 10000},
         lambda: edits.sync(BASE, 0, 1500, 3, 10000)),
        ("sync", {"a": 0, "a_time": 0, "b": 1, "b_time": 1001},
         lambda: edits.sync(BASE, 0, 0, 1, 1001)),
        ("add", {"after": 0}, lambda: edits.add(BASE, 0)),
        ("add", {"after": 1}, lambda: edits.add(BASE, 1)),
        ("add", {"after": -1}, lambda: edits.add(BASE, -1)),
        ("add", {"after": 3, "length": 500}, lambda: edits.add(BASE, 3, 500)),
        ("delete", {"indices": [0, 2]}, lambda: edits.delete(BASE, [0, 2])),
        ("split", {"i": 0}, lambda: edits.split(BASE, 0)),
        ("split", {"i": 0, "at": 3}, lambda: edits.split(BASE, 0, 3)),
        ("split", {"i": 1}, lambda: edits.split(BASE, 1)),
        ("split", {"i": 2}, lambda: edits.split(BASE, 2)),
        ("join", {"indices": [1, 0]}, lambda: edits.join(BASE, [1, 0])),
        ("join", {"indices": [0, 2, 3]}, lambda: edits.join(BASE, [0, 2, 3])),
        ("sort", {}, lambda: edits.sort(BASE)),
        ("replace_all", {"query": "line", "replacement": "Row", "case": False,
                         "whole_word": True, "regex": False},
         lambda: edits.replace_all(BASE, "line", "Row", case=False, whole_word=True, regex=False)),
        ("replace_all", {"query": r"(\w+) (\w+)", "replacement": r"\2 \1", "case": True,
                         "whole_word": False, "regex": True},
         lambda: edits.replace_all(BASE, r"(\w+) (\w+)", r"\2 \1", case=True, whole_word=False,
                                   regex=True)),
        ("cue_at", {"ms": 4600}, lambda: edits.cue_at(BASE, 4600)),
        ("split", {"i": 1, "note": True}, lambda: edits.split(NOTED, 1)),
        ("add", {"after": 1, "note": True}, lambda: edits.add(NOTED, 1)),
    ]  # fmt: skip
    out = []
    for op, args, run in cases:
        try:
            result = run()
        except ValueError as e:
            result = {"error": str(e)}
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], int):
            result = {"cues": [cue_json(c) for c in result[0]], "value": result[1]}
        elif isinstance(result, tuple):
            result = {"cues": [cue_json(c) for c in result]}
        out.append({"op": op, "args": args, "result": result})
    return out


MARKUP = [
    ("{\\i1}a{\\i0} {\\b1\\fs20}b{\\b0}\\hc {\\pos(1,2)}d", "ass", "srt"),
    ("{\\i1}a{\\i0}", "ass", "lrc"),
    ("<i>a</i> <font color=red>b</font> {x}", "srt", "ass"),
    ("<v Bob>a</v> <c.x>b</c> <00:01.000>c <B>d</B>", "vtt", "srt"),
    ("<i>a</i> <font color=red>b</font>", "srt", "vtt"),
    ("<i>a</i> <b>b", "smi", "lrc"),
    ("<i>a</i> < 3", "srt", "srt"),
    ("plain <tag>", "lrc", "srt"),
]


def main() -> None:
    data = {
        "files": [file_case(*f) for f in FILES],
        "base": [cue_json(c) for c in BASE],
        "noted": [cue_json(c) for c in NOTED],
        "edits": edit_cases(),
        "base_problems": [asdict(p) for p in checks.check(BASE, "srt")],
        "markup": [
            {"text": t, "src": s, "dst": d, "out": formats.convert_markup(t, s, d)}
            for t, s, d in MARKUP
        ],
        "clock": [[ms, formats.clock(ms)] for ms in (0, 999, 61_005, 3_723_004, 360_000_000)],
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
