"""One window for "Open with": a second start hands its file to the running app and quits.

Every GUI run listens on a local socket named after the user; ``--view FILE`` first tries to
send ``FILE`` there."""

from __future__ import annotations

import getpass
import json
import logging
import re

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "UniversalEdit-" + re.sub(r"[^A-Za-z0-9_-]", "_", user)[:40]


def send_to_running(message: dict, name: str | None = None, timeout_ms: int = 500) -> bool:
    """True if a running Universal Edit took the message."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((json.dumps(message) + "\n").encode("utf-8"))
    sock.flush()  # usually sends it all at once
    ok = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(2000)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(1000)
    return ok


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

    def start(self) -> bool:
        """True if this copy now answers later starts."""
        path = self.lock_path
        if path is None:
            from umc.core.paths import app_data_dir

            path = str(app_data_dir() / f"{self.name}.lock")
        lock = QLockFile(path)
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
                        self.message_received.emit(msg)

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                read()
