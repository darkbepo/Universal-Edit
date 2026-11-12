"""Run small jobs off the UI thread and hand the result back on it.

Each ``submit`` returns a ticket; results of a job whose ticket was superseded (``cancel_group``
or a newer job in the same group) are dropped, so a slow load never shows up after the user
moved on to the next file."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, Signal

from umc.core.errors import ConversionError

log = logging.getLogger(__name__)


class _Bridge(QObject):
    finished = Signal(int, bool, object)  # ticket, ok, result or message


class TaskRunner(QObject):
    """A private pool (so closing the window can wait for exactly its own jobs)."""

    def __init__(self, parent: QObject | None = None, threads: int = 2) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self._bridge = _Bridge(self)
        self._bridge.finished.connect(self._finished)
        self._next = 0
        self._latest: dict[str, int] = {}  # group → newest ticket
        self._callbacks: dict[int, tuple[str, Callable, Callable]] = {}

    def submit(
        self,
        group: str,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_failed: Callable[[str], None] | None = None,
        exclusive: bool = True,
    ) -> int:
        """Run ``fn`` on the pool. With ``exclusive``, only the newest job of ``group`` reports."""
        self._next += 1
        ticket = self._next
        if exclusive:
            self._latest[group] = ticket
        self._callbacks[ticket] = (
            group if exclusive else "",
            on_done,
            on_failed or (lambda m: None),
        )
        bridge = self._bridge

        def job() -> None:
            try:
                ok, result = True, fn()
            except ConversionError as exc:
                ok, result = False, str(exc)
            except Exception:
                log.exception("viewer job failed")
                ok, result = False, ""
            with contextlib.suppress(RuntimeError):  # the window is already gone
                bridge.finished.emit(ticket, ok, result)

        self.pool.start(job)
        return ticket

    def cancel_group(self, group: str) -> None:
        self._latest[group] = -1

    def _finished(self, ticket: int, ok: bool, result: object) -> None:
        group, on_done, on_failed = self._callbacks.pop(ticket, ("", None, None))
        if on_done is None or (group and self._latest.get(group) != ticket):
            return
        if ok:
            on_done(result)
        else:
            on_failed(result)

    def shutdown(self, ms: int = 5000) -> None:
        """Forget queued jobs and wait for running ones (call before the window goes away)."""
        self.pool.clear()
        self.pool.waitForDone(ms)
        self._callbacks.clear()
