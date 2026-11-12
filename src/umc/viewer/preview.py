"""Work the viewer does off the UI thread: text, PDF pages, documents turned into a PDF."""

from __future__ import annotations

import threading
from pathlib import Path

from umc.core.detect import DetectedFile
from umc.core.errors import ConversionError, ToolMissingError
from umc.viewer.files import PREVIEW_TARGETS

TEXT_LIMIT = 2_000_000  # characters shown of a text file


def read_text(path: Path, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    """(text, cut off?) — UTF-8 (with or without BOM), UTF-16 with a BOM, else Windows-1252."""
    data = Path(path).read_bytes()[: limit * 4]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text[:limit], len(text) > limit or Path(path).stat().st_size > len(data)


def page_sizes(path: Path) -> list[tuple[float, float]]:
    """Each page's size in points (PyMuPDF: PDF, EPUB, XPS, SVG, …)."""
    from umc.tools import pdf

    return pdf.page_sizes(path)


def render_page(path: Path, index: int, width_px: int):
    """One page as a PIL image ``width_px`` wide."""
    from umc.tools import pdf

    return pdf.render_page(path, index, width_px, 72, False)


def preview_pdf(f: DetectedFile, out_dir: Path, cancel: threading.Event | None = None) -> Path:
    """Convert ``f`` to a PDF in ``out_dir`` with the converter (e.g. Word → PDF)."""
    from umc.core import options as opts
    from umc.core.job import ConversionJob
    from umc.core.registry import targets_for

    target_id = PREVIEW_TARGETS.get(f.family)
    target = next((t for t in targets_for([f]) if t.id == target_id), None)
    if target is None:
        raise ConversionError("This file can't be previewed.")
    cls = target.converter.options_cls
    job = ConversionJob(
        files=[f],
        target=target,
        output_dir=out_dir,
        options=opts.build(cls, opts.defaults(cls)),
    )
    if cancel is not None:
        job.cancel_event = cancel
    try:
        outputs = target.converter.run(job)
    except ToolMissingError as exc:  # Convert offers "Install it for me"; the viewer doesn't
        raise ConversionError(f"{exc} Press Convert to install it with one click.") from exc
    pdfs = [Path(p) for p in outputs if str(p).lower().endswith(".pdf")]
    if not pdfs:
        raise ConversionError("This file can't be previewed.")
    return pdfs[0]
