"""Run one file through the document converter (Pandoc / LibreOffice route planner)."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

from umc.converters.document import DocumentConverter, DocumentOptions
from umc.core import options as opts
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob


def convert_file(
    src: Path, dst_ext: str, dest: Path, cancel_event: threading.Event | None = None
) -> Path:
    """Convert ``src`` to ``dst_ext`` and move the result to ``dest``."""
    src, dest = Path(src), Path(dest)
    converter = DocumentConverter()
    files = [detect(src)]
    target = next((t for t in converter.targets(files) if t.id == f"doc:{dst_ext}"), None)
    if target is None:
        raise ConversionError(f"Can't save as {dst_ext.upper()}.")
    with tempfile.TemporaryDirectory(prefix="umc-docedit-") as tmp:
        job = ConversionJob(
            files=files,
            target=target,
            options=opts.build(DocumentOptions, opts.defaults(DocumentOptions)),
            output_dir=Path(tmp) / "out",
        )
        if cancel_event is not None:
            job.cancel_event = cancel_event
        [out] = converter.run(job)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(out, dest)
        except OSError:
            shutil.move(str(out), str(dest))  # another drive
    return dest
