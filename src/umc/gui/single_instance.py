"""One window for "Open with": a second start hands its file to the running app and quits.

Every GUI run listens on a local socket named after the user; ``--view FILE`` first tries to
send ``FILE`` there."""

from __future__ import annotations

import getpass
import json
import logging
import re

from PySide6.QtCore import QObject, Signal
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
    """Receives messages from later starts (``message_received(dict)``)."""

    message_received = Signal(dict)

    def __init__(self, parent: QObject | None = None, name: str | None = None) -> None:
        super().__init__(parent)
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def start(self) -> bool:
        if self.server.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)  # a stale socket left by a crashed run (Unix)
        ok = self.server.listen(self.name)
        if not ok:
            log.warning(
                "single instance: can't listen on %s: %s", self.name, self.server.errorString()
            )
        return ok

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
