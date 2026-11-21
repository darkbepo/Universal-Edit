"""Saved files keep who may read them (mkstemp's temp files are private)."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

from umc.core.filemode import current_umask, match_mode

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no POSIX permission bits")


def test_existing_file_mode_is_copied(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    os.chmod(target, 0o640)
    fd, tmp = tempfile.mkstemp(dir=tmp_path)
    os.close(fd)
    match_mode(tmp, target)
    assert os.stat(tmp).st_mode & 0o777 == 0o640


def test_new_file_gets_the_usual_mode(tmp_path):
    fd, tmp = tempfile.mkstemp(dir=tmp_path)
    os.close(fd)
    match_mode(tmp, tmp_path / "new.txt")
    assert os.stat(tmp).st_mode & 0o777 == 0o666 & ~current_umask()


def test_umask_is_read_without_changing_it():
    before = os.umask(0o027)
    try:
        assert current_umask() == 0o027
        assert os.umask(0o027) == 0o027  # still what was set
    finally:
        os.umask(before)


def test_umask_from_a_probe_file_where_there_is_no_proc(tmp_path, monkeypatch):
    """macOS / BSD: the kernel applies the umask to a probe file in the target folder."""
    import pathlib

    real = pathlib.Path.read_text

    def no_proc(self, *a, **k):
        if str(self) == "/proc/self/status":
            raise OSError("no /proc")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", no_proc)
    before = os.umask(0o077)
    try:
        # the probe can't show the run bits, which a new file (0666) never gets anyway
        assert 0o666 & ~current_umask(tmp_path) == 0o600
        assert 0o666 & ~current_umask() == 0o600  # unknown: stays private
    finally:
        os.umask(before)
    assert list(tmp_path.iterdir()) == []  # the probe is gone


def test_special_bits_are_not_copied(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    os.chmod(target, 0o4755)
    fd, tmp = tempfile.mkstemp(dir=tmp_path)
    os.close(fd)
    match_mode(tmp, target)
    assert os.stat(tmp).st_mode & 0o7777 == 0o755
