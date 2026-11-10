"""Save an edited document: .ora keeps the layers; other formats go through the image converter
(so quality, metadata, transparency background and every format keep working)."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from umc.converters.image import IMAGE_FORMATS, ImageConverter, ImageOptions
from umc.converters.imaging import can_save
from umc.core import options as opts
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob
from umc.editor.document import Document
from umc.editor.ora import write_ora

SAVE_FORMATS: list[tuple[str, str]] = [("ora", "OpenRaster (keeps layers)")] + [
    (tid, label) for tid, (label, pil) in IMAGE_FORMATS.items() if can_save(pil)
]


def save_options_cls() -> type:
    return ImageOptions


def make_save_job(
    doc: Document, dest: Path, target_id: str, option_values: dict | None, tmp: Path
) -> ConversionJob:
    src = tmp / f"{dest.stem or 'image'}.png"
    doc.composite().save(src, format="PNG")
    converter = ImageConverter()
    files = [detect(src)]
    target = next((t for t in converter.targets(files) if t.id == target_id), None)
    if target is None:
        raise ConversionError(f"Can't save as {target_id.upper()}.")
    values = {**opts.defaults(ImageOptions), **(option_values or {})}
    return ConversionJob(
        files=files,
        target=target,
        options=opts.build(ImageOptions, values),
        output_dir=tmp / "out",
    )


def save_document(
    doc: Document,
    dest: Path,
    target_id: str,
    option_values: dict | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Write ``doc`` to ``dest`` (replacing it — the user already confirmed). ``dest`` is left
    untouched if anything fails."""
    dest = Path(dest)
    if target_id == "ora":
        write_ora(doc, dest)
        return dest
    with tempfile.TemporaryDirectory(prefix="umc-edit-") as tmp:
        job = make_save_job(doc, dest, target_id, option_values, Path(tmp))
        if on_progress is not None:
            job.on_progress = on_progress
        if cancel_event is not None:
            job.cancel_event = cancel_event
        try:
            [out] = job.target.converter.run(job)
            os.replace(out, dest)
        except ConversionError:
            raise
        except OSError as exc:
            raise ConversionError(
                f"Couldn't write “{dest.name}”. Is it open in another program?", details=repr(exc)
            ) from exc
    return dest
