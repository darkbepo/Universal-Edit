"""Export a drawing to PDF / PNG / JPG / … through the vector converter (desktop only; the
website exports through its worker). No Qt."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from umc.converters.vector import VectorConverter, VectorOptions
from umc.core import options as opts
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob
from umc.vectoredit import doc as vdoc
from umc.vectoredit.doc import VectorDoc

# file extension → the converter's target id, in the order Export offers them
FORMATS: dict[str, str] = {
    "pdf": "vec:pdf",
    "png": "vec:png",
    "jpg": "vec:jpg",
    "webp": "vec:webp",
    "tiff": "vec:tiff",
    "eps": "eps",
}
NAMES = {"pdf": "PDF", "png": "PNG", "jpg": "JPEG", "webp": "WebP", "tiff": "TIFF", "eps": "EPS"}


def export_file(
    doc: VectorDoc,
    dest: Path,
    fmt: str,
    values: dict | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Write ``doc`` as ``fmt`` to ``dest`` (replacing it: the user chose the name). ``dest`` is
    left as it was if anything fails."""
    if fmt not in FORMATS:
        raise ConversionError(f"Drawings can't be exported as .{fmt}.")
    dest = Path(dest)
    with tempfile.TemporaryDirectory(prefix=".umc-vector-", dir=dest.parent) as tmp:
        src = Path(tmp) / f"{dest.stem or 'drawing'}.svg"
        src.write_text(vdoc.write(doc), encoding="utf-8")
        files = [detect(src)]
        converter = VectorConverter()
        target = next((t for t in converter.targets(files) if t.id == FORMATS[fmt]), None)
        if target is None:
            raise ConversionError(f"Drawings can't be exported as {NAMES[fmt]}.")
        job = ConversionJob(
            files=files,
            target=target,
            options=opts.build(VectorOptions, {**opts.defaults(VectorOptions), **(values or {})}),
            output_dir=Path(tmp) / "out",
        )
        if cancel_event is not None:
            job.cancel_event = cancel_event
        [out] = converter.run(job)
        try:
            os.replace(out, dest)
        except OSError as exc:
            raise ConversionError(
                f"Couldn't write “{dest.name}”. Is it open in another program?", details=repr(exc)
            ) from exc
    return dest
