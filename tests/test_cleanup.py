import os
import time
from pathlib import Path

from umc.core.cleanup import LOG_MAX_AGE, TEMP_MAX_AGE, sweep_leftovers

DAY = 24 * 3600


def _age(path: Path, seconds: float, now: float) -> None:
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(p, (now - seconds, now - seconds))


def _make(path: Path, size: int = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_removes_stale_temp_folders_but_keeps_fresh_and_foreign(tmp_path):
    now = time.time()
    temp, tools = tmp_path / "temp", tmp_path / "tools"
    stale = temp / "umc-slides-abc"
    _make(stale / "img00000.png")
    _age(stale, TEMP_MAX_AGE + DAY, now)
    fresh = temp / "umc-lo-xyz"
    _make(fresh / "profile" / "x")
    other = temp / "someone-elses"
    _make(other / "f")
    _age(other, 30 * DAY, now)

    freed = sweep_leftovers(temp_dir=temp, tools=tools, now=now)

    assert not stale.exists()
    assert fresh.exists()
    assert other.exists()
    assert freed == 1000


def test_a_recently_written_file_keeps_an_old_folder(tmp_path):
    # A long conversion only touches files deep inside its folder.
    now = time.time()
    busy = tmp_path / "umc-2pass-1"
    _make(busy / "pass-0.log")
    _age(busy, TEMP_MAX_AGE + DAY, now)
    os.utime(busy / "pass-0.log", (now, now))

    sweep_leftovers(temp_dir=tmp_path, now=now)

    assert busy.exists()


def test_removes_old_installer_logs_and_unfinished_downloads(tmp_path):
    now = time.time()
    tools = tmp_path / "tools"
    old_log = _make(tools / "libreoffice-unpack.log", 5000)
    _age(old_log, LOG_MAX_AGE + DAY, now)
    new_log = _make(tools / "calibre-unpack.log")
    _age(new_log, DAY, now)
    partial = _make(tools / "downloads" / "LibreOffice_26.8.2_Win_x86-64.msi", 3000)
    _age(partial, TEMP_MAX_AGE + DAY, now)
    installed = _make(tools / "libreoffice" / "program" / "soffice.exe")
    _age(tools / "libreoffice", 300 * DAY, now)

    freed = sweep_leftovers(temp_dir=tmp_path / "no-temp", tools=tools, now=now)

    assert not old_log.exists()
    assert new_log.exists()
    assert not partial.exists()
    assert installed.exists()  # installed tools are never touched
    assert freed == 8000
