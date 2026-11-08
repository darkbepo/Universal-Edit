"""Runs a ConversionJob on a background thread and reports back through Qt signals."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot

from umc.core.errors import ConversionCancelled, ConversionError, ToolMissingError
from umc.core.job import ConversionJob

log = logging.getLogger(__name__)


class ConversionWorker(QObject):
    progress = Signal(float, str)
    succeeded = Signal(list)  # list[str] output paths
    failed = Signal(str)
    tool_missing = Signal(str, str, str)  # tool, download url, message
    cancelled = Signal()
    done = Signal()  # always emitted last

    def __init__(self, job: ConversionJob) -> None:
        super().__init__()
        self.job = job
        job.on_progress = lambda pct, msg: self.progress.emit(pct, msg)

    @Slot()
    def run(self) -> None:
        try:
            outputs = self.job.target.converter.run(self.job)
            self.succeeded.emit([str(p) for p in outputs])
        except ConversionCancelled:
            self.cancelled.emit()
        except ToolMissingError as exc:
            self.tool_missing.emit(exc.tool, exc.download_url, str(exc))
        except ConversionError as exc:
            log.error("conversion failed: %s\n%s", exc, exc.details)
            self.failed.emit(str(exc))
        except Exception:
            log.exception("unexpected error during conversion")
            self.failed.emit("Something went wrong. Details were written to the log file.")
        finally:
            self.done.emit()

    def cancel(self) -> None:
        self.job.cancel_event.set()


def start_worker(job: ConversionJob, parent: QObject) -> tuple[QThread, ConversionWorker]:
    thread = QThread(parent)
    worker = ConversionWorker(job)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    # The caller must keep references to both objects until ``thread.finished``; the Python
    # wrapper owns the worker, so dropping it earlier would free it while run() is unwinding.
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
