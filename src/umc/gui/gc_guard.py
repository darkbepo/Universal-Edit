"""Run Python's cyclic garbage collector on the GUI thread only.

If the collector kicks in on a worker thread it may free a PySide wrapper whose C++ object lives
on the GUI thread, which crashes the process (access violation). So automatic collection is turned
off and a timer on the GUI thread does the same job.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QTimer

_timer: QTimer | None = None


def _collect() -> None:
    counts, limits = gc.get_count(), gc.get_threshold()
    for generation in (2, 1, 0):
        if counts[generation] > limits[generation]:
            gc.collect(generation)
            return


def install_gui_gc(app: QCoreApplication, interval_ms: int = 1000) -> None:
    """Disable automatic collection and collect from a timer on the GUI thread (idempotent)."""
    global _timer
    gc.disable()
    if _timer is not None:
        return
    _timer = QTimer(app)
    _timer.setInterval(interval_ms)
    _timer.timeout.connect(_collect)
    _timer.start()
