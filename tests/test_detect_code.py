"""Code files are accepted as the "code" family (the code editor's step 1)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from umc.core.detect import MediaFamily, detect
from umc.core.registry import targets_for

FIXTURE = Path(__file__).parent / "fixtures/detect_code_cases.json"


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _ts_packets(packet: int, header: int) -> bytes:
    one = b"\x00" * header + b"\x47" + b"\x00" * (packet - header - 1)
    return one * 3


def test_css_is_code(tmp_path):
    f = detect(_write(tmp_path, "a.css", b"a { color: red; }\n"))
    assert f is not None and f.family == MediaFamily.CODE and f.ext == "css"


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("a.html", MediaFamily.DOCUMENT),
        ("a.json", MediaFamily.DOCUMENT),
        ("a.svg", MediaFamily.VECTOR),
        ("a.srt", MediaFamily.SUBTITLE),
        ("a.txt", MediaFamily.DOCUMENT),
    ],
)
def test_other_families_unchanged(tmp_path, name, family):
    assert detect(_write(tmp_path, name, b"x\n")).family == family


def test_named_files(tmp_path):
    docker = detect(_write(tmp_path, "Dockerfile", b"FROM python\n"))
    assert (docker.family, docker.ext) == (MediaFamily.CODE, "dockerfile")
    ignore = detect(_write(tmp_path, ".gitignore", b"*.pyc\n"))
    assert (ignore.family, ignore.ext) == (MediaFamily.CODE, "gitignore")
    assert ignore.stem == ".gitignore"


def test_ts_typescript(tmp_path):
    f = detect(_write(tmp_path, "a.ts", b"const x: number = 1;\n"))
    assert (f.family, f.ext) == (MediaFamily.CODE, "ts")


@pytest.mark.parametrize(("packet", "header"), [(188, 0), (192, 4)])
def test_ts_video(tmp_path, packet, header):
    f = detect(_write(tmp_path, "rec.ts", _ts_packets(packet, header)))
    assert (f.family, f.ext) == (MediaFamily.VIDEO, "ts")


def test_ts_video_that_starts_mid_packet(tmp_path):
    f = detect(_write(tmp_path, "cut.ts", b"\x00\x01\x02" + _ts_packets(188, 0) * 4))
    assert f.family == MediaFamily.VIDEO


def test_folders_and_missing_files_are_refused(tmp_path):
    (tmp_path / "myfolder").mkdir()
    (tmp_path / "proj.v2").mkdir()
    assert detect(tmp_path / "myfolder") is None
    assert detect(tmp_path / "proj.v2") is None
    assert detect(tmp_path / "gone.zzz") is None


def test_code_exts_match_the_shared_file():
    """The website builds the same set (web/tests/detect-code.test.ts reads this file too)."""
    from umc.core.detect import CODE_EXTS

    shared = json.loads((FIXTURE.parent / "code_exts.json").read_text(encoding="utf-8"))
    assert sorted(CODE_EXTS) == shared["code_exts"]


def test_unknown_text_file(tmp_path):
    nfo = detect(_write(tmp_path, "notes.nfo", b"Release notes\r\n"))
    assert (nfo.family, nfo.ext) == (MediaFamily.CODE, "nfo")
    readme = detect(_write(tmp_path, "README", b"Read me first.\n"))
    assert (readme.family, readme.ext) == (MediaFamily.CODE, "")


def test_unknown_binary_refused(tmp_path):
    assert detect(_write(tmp_path, "blob.dat", bytes(range(256)) * 40)) is None


def test_pack_targets_only(tmp_path):
    f = detect(_write(tmp_path, "a.css", b"a {}\n"))
    assert {t.id for t in targets_for([f])} == {"pack:zip", "pack:7z", "pack:tar.gz"}


def test_every_family_has_icon_and_noun():
    from umc.gui.icons import FAMILY_ICON
    from umc.gui.main_window import FAMILY_NOUN
    from umc.properties.read import NOUN

    for family in MediaFamily:
        assert family in FAMILY_ICON and family in FAMILY_NOUN, family
        if family != MediaFamily.PROJECT:
            assert family in NOUN, family


def test_shared_cases(tmp_path):
    """The same cases the website checks (web/tests/detect-code.test.ts)."""
    for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
        sub = tmp_path / case["id"]
        sub.mkdir()
        f = detect(_write(sub, case["name"], base64.b64decode(case["bytes_b64"])))
        got = None if f is None else {"family": str(f.family), "ext": f.ext}
        want = None if case["family"] is None else {"family": case["family"], "ext": case["ext"]}
        assert got == want, case["id"]
