"""PDF toolkit: merge, compress, rotate, reorder/delete pages, add/remove password."""

from __future__ import annotations

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

# target id → (label, hint, filename suffix)
TOOLS = {
    "pdf:merge": ("Merge into one PDF", "Combine all files into a single PDF", ""),
    "pdf:compress": ("Compress", "Make the PDF smaller", "_compressed"),
    "pdf:rotate": ("Rotate pages", "Turn some or all pages", "_rotated"),
    "pdf:organize": ("Reorder / delete pages", "Keep only some pages, in any order", "_pages"),
    "pdf:protect": ("Add password", "Encrypt the PDF (AES-256)", "_protected"),
    "pdf:unlock": ("Remove password", "Save an unprotected copy", "_unlocked"),
}


@dataclass
class PdfToolOptions:
    order: str = opt(
        "selected",
        "File order",
        choices=[("selected", "As listed (drag to reorder)"), ("name", "By file name")],
        targets=("pdf:merge",),
    )
    bookmarks: bool = opt(True, "Add a bookmark for each file", targets=("pdf:merge",))
    level: str = opt(
        "recommended",
        "Compression",
        choices=[
            ("lossless", "Lossless (clean up only)"),
            ("recommended", "Recommended (images 150 DPI)"),
            ("strong", "Strong (images 110 DPI)"),
            ("extreme", "Extreme (images 72 DPI, lower quality)"),
        ],
        targets=("pdf:compress",),
    )
    strip_metadata: bool = opt(False, "Remove metadata (author, title…)", targets=("pdf:compress",))
    angle: int = opt(
        90,
        "Rotate by",
        choices=[(90, "90° clockwise"), (180, "180°"), (270, "90° counter-clockwise")],
        targets=("pdf:rotate",),
    )
    rotate_pages: str = opt(
        "all", "Pages", targets=("pdf:rotate",), help="e.g. 1-3, 5, 8- (all = every page)"
    )
    keep: str = opt(
        "all",
        "Pages to keep, in order",
        targets=("pdf:organize",),
        help="e.g. 3, 1-2, 5- puts page 3 first and drops page 4. Leave out pages to delete them.",
    )
    reverse: bool = opt(False, "Reverse the order", targets=("pdf:organize",))
    password: str = opt("", "Password", kind="password", targets=("pdf:protect", "pdf:unlock"))
    owner_password: str = opt(
        "",
        "Owner password (optional)",
        kind="password",
        targets=("pdf:protect",),
        help="Lets you change the permissions below later. Empty = they can never be changed.",
    )
    allow_print: bool = opt(True, "Allow printing", targets=("pdf:protect",))
    allow_copy: bool = opt(True, "Allow copying text", targets=("pdf:protect",))
    allow_edit: bool = opt(False, "Allow editing", targets=("pdf:protect",))


class PdfToolsConverter(Converter):
    family = MediaFamily.DOCUMENT
    options_cls = PdfToolOptions

    def targets(self, files: list[DetectedFile]):
        out = []
        if len(files) > 1 and all("pdf" in reachable(f.ext) for f in files):
            label, hint, _ = TOOLS["pdf:merge"]
            out.append(self.target("pdf:merge", label, "pdf", "PDF tools", hint))
        if all(f.ext == "pdf" for f in files):
            for tid, (label, hint, _) in TOOLS.items():
                if tid != "pdf:merge":
                    out.append(self.target(tid, label, "pdf", "PDF tools", hint))
        return out

    def run(self, job: ConversionJob) -> list[Path]:
        o: PdfToolOptions = job.options
        tool = job.target.id
        if tool == "pdf:merge":
            return [self._merge(job, o)]
        suffix = TOOLS[tool][2]
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"{TOOLS[tool][0]}: {f.name} ({i + 1}/{n})")
            dest = unique_path(job.output_dir_for(f.path), f"{f.path.stem}{suffix}", "pdf")
            try:
                if tool == "pdf:compress":
                    pdf.compress(f.path, dest, o.level, o.strip_metadata)
                elif tool == "pdf:rotate":
                    pdf.rotate(f.path, dest, o.angle, o.rotate_pages)
                elif tool == "pdf:organize":
                    pdf.organize(f.path, dest, o.keep, o.reverse)
                elif tool == "pdf:protect":
                    pdf.protect(f.path, dest, o.password, o.owner_password, o.allow_print,
                                o.allow_copy, o.allow_edit)  # fmt: skip
                elif tool == "pdf:unlock":
                    pdf.unlock(f.path, dest, o.password)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs

    def _merge(self, job: ConversionJob, o: PdfToolOptions) -> Path:
        files = list(job.files)
        if o.order == "name":
            files.sort(key=lambda f: f.name.lower())
        first = files[0].path
        dest = unique_path(
            job.output_dir_for(first), f"{first.stem} + {len(files) - 1} more", "pdf"
        )
        with tempfile.TemporaryDirectory(prefix="umc-merge-") as tmp:
            sources = []
            for i, f in enumerate(files):
                job.check_cancelled()
                job.report(0.9 * i / len(files), f"Preparing {f.name}")
                if f.ext == "pdf":
                    sources.append(f.path)
                else:
                    sources.append(convert_document(f, "pdf", Path(tmp) / f"{i}.pdf", job))
            job.report(0.95, "Merging")
            try:
                pdf.merge(sources, dest, o.bookmarks, [f.path.stem for f in files])
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
        if not dest.exists():
            raise ConversionError("Merging failed.")
        job.report(1.0, "Done")
        return dest
