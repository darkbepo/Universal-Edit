"""Run small jobs off the UI thread and hand the result back on it.

Each ``submit`` returns a ticket; a job's result is dropped when a newer job of the same group
was submitted (``exclusive``) or the group was cancelled since, so a slow load never shows up
after the user moved on to the next file.

Two lanes: "pool" (2 threads: pictures, thumbnails, text, 3D) and "pdf" (1 thread) for everything
that touches PyMuPDF — MuPDF has one shared context and isn't safe on two threads at once, even
for different documents (PDF pages, documents turned into PDFs, which also use it). Every
runner's "pdf" jobs also take one process-wide lock, so two windows (the viewer, the converter's
Properties panel) never use it together."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, Signal

from umc.core.errors import ConversionError

log = logging.getLogger(__name__)

PDF_LOCK = threading.Lock()  # PyMuPDF: one thread at a time in the whole process


class _Bridge(QObject):
    finished = Signal(int, bool, object)  # ticket, ok, result or message


class TaskRunner(QObject):
    def __init__(self, parent: QObject | None = None, threads: int = 2) -> None:
        super().__init__(parent)
        self.pools = {"pool": QThreadPool(self), "pdf": QThreadPool(self)}
        self.pools["pool"].setMaxThreadCount(threads)
        self.pools["pdf"].setMaxThreadCount(1)
        self._bridge = _Bridge(self)
        self._bridge.finished.connect(self._finished)
        self._next = 0
        self._latest: dict[str, int] = {}  # exclusive group → newest ticket
        self._cancelled: dict[str, int] = {}  # group → tickets up to this one are dropped
        self._callbacks: dict[int, tuple[str, bool, Callable, Callable]] = {}

    def submit(
        self,
        group: str,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_failed: Callable[[str], None] | None = None,
        exclusive: bool = True,
        lane: str = "pool",
    ) -> int:
        """Run ``fn`` in ``lane``. With ``exclusive``, only the newest job of ``group`` reports;
        otherwise every job reports unless ``cancel_group(group)`` came after it."""
        self._next += 1
        ticket = self._next
        if exclusive:
            self._latest[group] = ticket
        fail = on_failed or (lambda _m: None)
        self._callbacks[ticket] = (group, exclusive, on_done, fail)
        bridge = self._bridge

        def job() -> None:
            if self.is_stale(ticket):  # cancelled while it waited: skip the work
                with contextlib.suppress(RuntimeError):
                    bridge.finished.emit(ticket, False, "")
                return
            try:
                if lane == "pdf":
                    with PDF_LOCK:
                        ok, result = True, fn()
                else:
                    ok, result = True, fn()
            except ConversionError as exc:
                ok, result = False, str(exc)
            except Exception:
                log.exception("viewer job failed")
                ok, result = False, ""
            with contextlib.suppress(RuntimeError):  # the window is already gone
                bridge.finished.emit(ticket, ok, result)

        self.pools[lane].start(job)
        return ticket

    def cancel_group(self, group: str) -> None:
        self._latest[group] = -1
        self._cancelled[group] = self._next

    def is_stale(self, ticket: int) -> bool:
        entry = self._callbacks.get(ticket)
        if entry is None:
            return True
        group, exclusive, _done, _fail = entry
        if exclusive and self._latest.get(group) != ticket:
            return True
        return ticket <= self._cancelled.get(group, 0)

    def _finished(self, ticket: int, ok: bool, result: object) -> None:
        stale = self.is_stale(ticket)
        entry = self._callbacks.pop(ticket, None)
        if entry is None or stale:
            return
        _group, _exclusive, on_done, on_failed = entry
        if ok:
            on_done(result)
        else:
            on_failed(result)

    def shutdown(self) -> None:
        """Forget queued jobs and every pending result, without waiting for running ones (a
        LibreOffice preview can take a while; its result is simply dropped)."""
        for pool in self.pools.values():
            pool.clear()
        self._callbacks.clear()

    def wait(self, ms: int = 5000) -> bool:
        return all(pool.waitForDone(ms) for pool in self.pools.values())
