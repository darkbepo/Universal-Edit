"""One window for "Open with": a second start hands its file to the running app and quits.

Every GUI run listens on a local socket named after the user; ``--view FILE`` first tries to
send ``FILE`` there."""

from __future__ import annotations

import getpass
import json
import logging
import re
import sys
import threading
import time

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer

log = logging.getLogger(__name__)

ACK = b"ok\n"


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "UniversalEdit-" + re.sub(r"[^A-Za-z0-9_-]", "_", user)[:40]


def _server_address(name: str) -> str:
    """Where QLocalServer listens: a named pipe on Windows, a socket file in Qt's temp folder
    elsewhere (QLocalServer.fullServerName for a plain name)."""
    if sys.platform == "win32":
        return "\\\\.\\pipe\\" + name
    from PySide6.QtCore import QDir

    return QDir.tempPath().rstrip("/") + "/" + name


def _exchange(address: str, data: bytes, timeout_s: float) -> bytes:
    """Send ``data`` and return the reply line (b"" if none). Plain Python on purpose: no Qt
    socket, so it works from any thread and never holds up a server in the same process."""
    if sys.platform == "win32":
        result: list[bytes] = []

        def talk() -> None:
            try:
                with open(address, "r+b", buffering=0) as pipe:  # noqa: PTH123 (a named pipe)
                    pipe.write(data)
                    reply = b""
                    while b"\n" not in reply:
                        chunk = pipe.read(64)
                        if not chunk:
                            break
                        reply += chunk
                    result.append(reply)
            except OSError:
                pass

        t = threading.Thread(target=talk, daemon=True)  # a pipe read has no timeout of its own
        t.start()
        t.join(timeout_s)
        return result[0] if result else b""
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(address)
            sock.sendall(data)
            reply = b""
            while b"\n" not in reply:
                chunk = sock.recv(64)
                if not chunk:
                    break
                reply += chunk
            return reply
    except OSError:
        return b""


def send_to_running(message: dict, name: str | None = None, timeout_ms: int = 3000) -> bool:
    """True if a running Universal Edit took the message (it answers "ok" once it has read it;
    hanging up right after writing could lose the message on Windows)."""
    data = (json.dumps(message) + "\n").encode("utf-8")
    reply = _exchange(_server_address(name or server_name()), data, timeout_ms / 1000)
    return reply.startswith(ACK)


def claim(
    message: dict, name: str | None = None, lock_path: str | None = None, wait_s: float = 8.0
) -> tuple[bool, QLockFile | None]:
    """For ``--view`` / ``--convert``: hand ``message`` to a running copy, or become it.

    Returns (handed over, lock). Explorer starts one copy per selected file at the same
    moment, before any of them listens: the one that gets the lock becomes the window, the
    others keep offering their file until it listens (up to ``wait_s``). (False, None) = no
    copy answered in time: open a window of our own."""

    if send_to_running(message, name):
        return True, None
    server = InstanceServer(name=name, lock_path=lock_path)
    lock = QLockFile(server.lock_file())
    if lock.tryLock(0):
        return False, lock
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if send_to_running(message, name, timeout_ms=1000):
            return True, None
        time.sleep(0.1)
    return False, None


class InstanceServer(QObject):
    """Receives messages from later starts (``message_received(dict)``).

    Only the first running copy listens: it holds a lock file in the app-data folder (Qt
    notices a lock left by a crashed run from its process id). A later normal start doesn't
    take the socket over — on Windows a second named pipe would happily listen too, and on Unix
    removing the socket file would cut the first copy off."""

    message_received = Signal(dict)

    def __init__(
        self, parent: QObject | None = None, name: str | None = None, lock_path: str | None = None
    ) -> None:
        super().__init__(parent)
        self.name = name or server_name()
        self.lock_path = lock_path
        self.lock: QLockFile | None = None
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def lock_file(self) -> str:
        if self.lock_path is not None:
            return self.lock_path
        from umc.core.paths import app_data_dir

        return str(app_data_dir() / f"{self.name}.lock")

    def start(self, lock: QLockFile | None = None) -> bool:
        """True if this copy now answers later starts (``lock``: already taken by ``claim``)."""
        if lock is None:
            lock = QLockFile(self.lock_file())
            if not lock.tryLock(0):
                return False  # another copy is running and answers
        self.lock = lock
        QLocalServer.removeServer(self.name)  # a socket file left by a crashed run (Unix)
        if not self.server.listen(self.name):
            why = self.server.errorString()
            log.warning("single instance: can't listen on %s: %s", self.name, why)
            return False
        return True

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf) -> None:
                buf.extend(bytes(sock.readAll()))
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf[:] = rest
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        sock.write(ACK)  # the sender waits for this before hanging up
                        sock.flush()
                        self.message_received.emit(msg)

            sock.readyRead.connect(read)
            # the sender only hangs up after our "ok", so everything has been read by then
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                read()
