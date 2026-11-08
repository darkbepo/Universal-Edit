"""Locating and running external programs with cancellation support."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from umc.core.errors import ConversionCancelled, ConversionError

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


def bundled_bin_dir() -> Path:
    """Folder holding binaries shipped with the app (PyInstaller) or fetched for development."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "bin"
    return Path(__file__).resolve().parents[3] / "packaging" / "bin"


def find_executable(names: Iterable[str], extra_paths: Iterable[str | Path] = ()) -> str | None:
    """Look for an executable: bundled folder first, then ``PATH``, then ``extra_paths``."""
    names = list(names)
    exe_names = [n + ".exe" for n in names] if IS_WINDOWS else names
    bundled = bundled_bin_dir()
    for n in exe_names:
        p = bundled / n
        if p.is_file():
            return str(p)
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    for p in extra_paths:
        p = Path(os.path.expandvars(str(p)))
        if p.is_file():
            return str(p)
    return None


def _creationflags() -> int:
    # Don't flash a console window for every subprocess in the windowed .exe.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def run(
    cmd: Sequence[str] | str,
    *,
    cancel_event: threading.Event | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
    timeout: float | None = None,
    error_message: str = "The conversion failed.",
    cwd: str | Path | None = None,
) -> str:
    """Run ``cmd``; stream stdout lines to ``on_stdout_line``; honour cancellation.

    ``cmd`` may be a string on Windows for programs that parse their own command line
    (msiexec). Returns captured stderr. Raises :class:`ConversionError` on a non-zero exit code.
    """
    if isinstance(cmd, str):
        log.info("run: %s", cmd)
    else:
        cmd = list(cmd)
        log.info("run: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
        cwd=cwd,
    )
    stderr_tail: deque[str] = deque(maxlen=200)

    def pump_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if on_stdout_line is not None:
                try:
                    on_stdout_line(line.rstrip("\n"))
                except Exception:  # never let a progress callback kill the pump
                    log.exception("stdout callback failed")

    def pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip("\n"))

    threads = [
        threading.Thread(target=pump_stdout, daemon=True),
        threading.Thread(target=pump_stderr, daemon=True),
    ]
    for t in threads:
        t.start()

    started = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _kill(proc)
                raise ConversionCancelled()
            if timeout is not None and time.monotonic() - started > timeout:
                _kill(proc)
                raise ConversionError(f"{error_message} (it took too long and was stopped)")
            time.sleep(0.1)
    finally:
        for t in threads:
            t.join(timeout=2)

    stderr = "\n".join(stderr_tail)
    if proc.returncode != 0:
        log.error("command failed (%s):\n%s", proc.returncode, stderr)
        raise ConversionError(error_message, details=stderr)
    return stderr


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
