"""A single conversion request, independent of any GUI toolkit."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from umc.core.errors import ConversionCancelled

if TYPE_CHECKING:
    from umc.core.detect import DetectedFile
    from umc.core.registry import Target


def _noop(_percent: float, _message: str) -> None:
    pass


@dataclass
class ConversionJob:
    files: list[DetectedFile]
    target: Target
    options: Any
    # None → write each output next to its source file.
    output_dir: Path | None = None
    on_progress: Callable[[float, str], None] = _noop
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def report(self, fraction: float, message: str = "") -> None:
        """Report progress as a fraction 0..1."""
        self.on_progress(max(0.0, min(1.0, fraction)) * 100.0, message)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ConversionCancelled()

    def output_dir_for(self, source: Path) -> Path:
        out = self.output_dir or source.parent
        out.mkdir(parents=True, exist_ok=True)
        return out

    def sub_progress(self, start: float, end: float, message: str = "") -> Callable[[float], None]:
        """Return a callback mapping 0..1 onto the [start, end] slice of the overall progress."""

        def cb(fraction: float) -> None:
            self.report(start + (end - start) * max(0.0, min(1.0, fraction)), message)

        return cb


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """``directory/stem.ext``, or ``stem (1).ext`` etc. if that already exists."""
    suffix = f".{ext}" if ext else ""
    candidate = directory / f"{stem}{suffix}"
    n = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){suffix}"
        n += 1
    return candidate
