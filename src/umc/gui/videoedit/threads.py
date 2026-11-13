"""Background QThreads of the video editor: how their worker objects are let go.

One rule everywhere (CLAUDE.md: keep the worker and its QThread until ``QThread.finished``):
a worker is never handed to ``deleteLater`` from ``thread.finished``. That signal is emitted
on the worker thread, where Qt would delete the worker at once, while the GUI thread drops the
last Python reference (the worker is Python-owned) and deletes it too: a double delete.
Instead, a slot on the GUI thread waits for the thread to end and drops the references there.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Slot

_STRAGGLERS: list[_Straggler] = []  # threads that outlived their owner; kept until they end


class _Straggler(QObject):
    """Holds a still-running thread and its worker. It lives on the GUI thread, so
    ``release`` (connected to ``thread.finished``) runs there."""

    def __init__(self, thread: QThread, worker: QObject) -> None:
        super().__init__()
        self.thread_ref: QThread | None = thread
        self.worker_ref: QObject | None = worker

    @Slot()
    def release(self) -> None:
        thread = self.thread_ref
        if thread is None:  # already released
            return
        thread.wait()  # QThread.finished comes just before the thread really ends
        self.thread_ref, self.worker_ref = None, None  # deleted here, on the GUI thread
        if self in _STRAGGLERS:
            _STRAGGLERS.remove(self)


def keep_until_finished(thread: QThread, worker: QObject) -> None:
    """Detach a still-running thread from its owner and keep it (and its worker) alive until
    it ends: a running QThread must never be destroyed."""
    thread.setParent(None)
    entry = _Straggler(thread, worker)
    _STRAGGLERS.append(entry)
    thread.finished.connect(entry.release)
    if thread.isFinished():  # finished before the connection was made
        entry.release()


def stragglers() -> int:
    """How many threads are still being kept alive (for tests)."""
    return len(_STRAGGLERS)
