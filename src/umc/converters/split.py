"""Split a document into several PDFs (every page, N pages each, or custom ranges)."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.document import convert_document, reachable
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import pdf


@dataclass
class SplitOptions:
    mode: str = opt(
        "every",
        "Split",
        choices=[
            ("every", "Every page into its own document"),
            ("chunks", "Every N pages"),
            ("ranges", "Custom page ranges"),
        ],
    )
    pages_per_document: int = opt(
        2,
        "Pages per document",
        kind="slider",
        min=1,
        max=100,
        show_if=lambda v: v["mode"] == "chunks",
        help="The last document gets whatever pages are left.",
    )
    ranges: str = opt(
        "1-3, 4-",
        "Page ranges",
        show_if=lambda v: v["mode"] == "ranges",
        help="One document per range, separated by commas. e.g. 1-3, 4-10, 11- "
        "(“11-” means page 11 to the end).",
    )
    naming: str = opt(
        "pages",
        "File names",
        choices=[
            ("pages", "name_pages_1-3.pdf"),
            ("part", "name_part_01.pdf"),
        ],
    )
    subfolder: bool = opt(True, "Put the documents in their own folder")


def page_groups(o: SplitOptions, count: int) -> list[list[int]]:
    if o.mode == "every":
        return [[p] for p in range(count)]
    if o.mode == "chunks":
        size = max(1, o.pages_per_document)
        return [list(range(s, min(s + size, count))) for s in range(0, count, size)]
    return pdf.parse_page_groups(o.ranges, count)


def group_name(stem: str, index: int, total: int, pages: list[int], naming: str) -> str:
    if naming == "part":
        return f"{stem}_part_{index + 1:0{max(2, len(str(total)))}d}"
    first, last = pages[0] + 1, pages[-1] + 1
    contiguous = pages == list(range(pages[0], pages[-1] + 1))
    if first == last:
        return f"{stem}_page_{first}"
    return f"{stem}_pages_{first}-{last}" if contiguous else f"{stem}_part_{index + 1:02d}"


class DocumentSplitConverter(Converter):
    family = MediaFamily.DOCUMENT
    options_cls = SplitOptions

    def targets(self, files: list[DetectedFile]):
        if not all("pdf" in reachable(f.ext) for f in files):
            return []
        return [
            self.target(
                "split:pdf",
                "Split into PDFs",
                "pdf",
                "Split",
                "Split each document into several PDF files",
            )
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: SplitOptions = job.options
        outputs: list[Path] = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Splitting {f.name} ({i + 1}/{n})")
            with tempfile.TemporaryDirectory(prefix="umc-split-") as tmp:
                src = f.path
                if f.ext != "pdf":
                    src = convert_document(f, "pdf", Path(tmp) / f"{f.path.stem}.pdf", job)
                groups = page_groups(o, pdf.page_count(src))
                if not groups:
                    raise ConversionError(f"“{f.name}” has no pages to split.")
                base = job.output_dir_for(f.path)
                folder = unique_path(base, f"{f.path.stem}_split", "") if o.subfolder else base
                folder.mkdir(parents=True, exist_ok=True)
                written = []
                try:
                    for k, pages in enumerate(groups):
                        job.check_cancelled()
                        name = group_name(f.path.stem, k, len(groups), pages, o.naming)
                        dest = unique_path(folder, name, "pdf")
                        pdf.extract_pages(src, dest, pages)
                        written.append(dest)
                        job.report(
                            (i + (k + 1) / len(groups)) / n,
                            f"Splitting {f.name}: document {k + 1} of {len(groups)}",
                        )
                except BaseException:
                    if o.subfolder:
                        shutil.rmtree(folder, ignore_errors=True)
                    else:
                        for w in written:
                            w.unlink(missing_ok=True)
                    raise
                outputs.append(folder if o.subfolder else written[0].parent)
        job.report(1.0, "Done")
        return outputs
