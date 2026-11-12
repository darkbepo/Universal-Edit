"""Plays "the running Universal Edit window" in its own process for the single-instance tests
(real use is always two programs; a Qt listener inside the test process crashed Qt on Windows).

    python tests/instance_window.py NAME LOCKFILE [LISTEN_AFTER_S]

Takes the lock at once and prints "holding", listens after LISTEN_AFTER_S (default 0) and
prints "ready", then one JSON line per message received; quits after one message or 20 s."""

import json
import os
import subprocess
import sys
from pathlib import Path


def start_window(name: str, lock: str, listen_after: float = 0.0) -> subprocess.Popen:
    """Start the stand-in window; returns once it holds the lock."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__)), name, lock, str(listen_after)],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "holding"
    return proc


def main() -> int:
    from PySide6.QtCore import QCoreApplication, QLockFile, QTimer

    from umc.gui.single_instance import InstanceServer

    name, lock_path = sys.argv[1], sys.argv[2]
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    app = QCoreApplication([])
    lock = QLockFile(lock_path)
    if not lock.tryLock(0):
        print("locked", flush=True)
        return 1
    print("holding", flush=True)
    server = InstanceServer(name=name, lock_path=lock_path)

    def got(msg: dict) -> None:
        print(json.dumps(msg), flush=True)
        QTimer.singleShot(200, app.quit)  # let the "ok" go out first

    def start() -> None:
        server.start(lock)
        print("ready", flush=True)

    server.message_received.connect(got)
    QTimer.singleShot(int(delay * 1000), start)
    QTimer.singleShot(20000, app.quit)
    app.exec()
    server.server.close()
    lock.unlock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
