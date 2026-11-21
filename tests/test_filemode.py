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
