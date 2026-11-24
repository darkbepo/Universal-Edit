"""Reading files out of NSIS installers (Ghostscript's installer can't run silently)."""

import threading

import pytest

from tests.nsis_sample import build
from umc.core.errors import ConversionCancelled
from umc.tools import nsis


def _sample(tmp_path):
    return build(
        [
            ("file", "$PLUGINSDIR\\System.dll", b"plugin"),  # the installer's own helper
            ("dir", "$INSTDIR"),
            ("file", "readme.txt", b"old readme"),
            ("set", 31, "$OUTDIR"),  # File /r keeps $OUTDIR in a variable
            ("dir", "$31\\bin"),
            ("file", "gs.exe", b"program"),
            ("file", "$OUTDIR\\copy.exe", b"program"),  # same bytes, stored once
            ("dir", "$31"),
            ("file", "lib\\init.ps", b"%!PS"),
            ("file", "readme.txt", b"new readme"),  # a later File wins
            ("file", "..\\escape.txt", b"no"),
            ("file", "C:\\Windows\\evil.dll", b"no"),
            ("dir", "C:\\Elsewhere"),
            ("file", "outside.txt", b"no"),
        ],
        tmp_path / "setup.exe",
    )


def test_read_lists_only_files_below_instdir(tmp_path):
    info = nsis.read(_sample(tmp_path))
    assert sorted(e.path for e in info.entries) == [
        "bin/copy.exe",
        "bin/gs.exe",
        "lib/init.ps",
        "readme.txt",
    ]


def test_extract_writes_the_bytes_and_dates(tmp_path):
    info = nsis.read(_sample(tmp_path))
    seen = []
    written = nsis.extract(info, tmp_path / "out", progress=seen.append)
    out = tmp_path / "out"
    assert (out / "bin" / "gs.exe").read_bytes() == b"program"
    assert (out / "bin" / "copy.exe").read_bytes() == b"program"
    assert (out / "lib" / "init.ps").read_bytes() == b"%!PS"
    assert (out / "readme.txt").read_bytes() == b"new readme"
    assert len(written) == 4 and seen[-1] == 1.0
    assert int((out / "lib" / "init.ps").stat().st_mtime) == 1577836800  # 2020-01-01 UTC
    assert not (tmp_path / "escape.txt").exists()


def test_extract_only_some_files(tmp_path):
    info = nsis.read(_sample(tmp_path))
    nsis.extract(info, tmp_path / "out", only=lambda p: p.startswith("bin/"))
    files = sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert files == ["copy.exe", "gs.exe"]


def test_extract_honours_cancel(tmp_path):
    info = nsis.read(_sample(tmp_path))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionCancelled):
        nsis.extract(info, tmp_path / "out", cancel_event=cancel)


def test_other_files_are_refused_plainly(tmp_path):
    plain = tmp_path / "plain.exe"
    plain.write_bytes(b"MZ" + b"\0" * 5000)
    with pytest.raises(nsis.NsisError, match="no NSIS data"):
        nsis.read(plain)
    ansi = build([("dir", "$INSTDIR")], tmp_path / "ansi.exe", unicode=False)
    with pytest.raises(nsis.NsisError, match="Unicode"):
        nsis.read(ansi)
    cut = tmp_path / "cut.exe"
    cut.write_bytes(_sample(tmp_path).read_bytes()[:1100])
    with pytest.raises(nsis.NsisError):
        nsis.read(cut)
