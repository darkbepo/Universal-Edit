"""Document → images (one image per page)."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.document import convert_document, reachable
from umc.converters.imaging import flatten
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import pdf

PAGE_FORMATS = {
    "png": ("PNG", "PNG"),
    "jpg": ("JPG", "JPEG"),
    "webp": ("WEBP", "WEBP"),
    "tiff": ("TIFF", "TIFF"),
    "bmp": ("BMP", "BMP"),
}


@dataclass
class PageImageOptions:
    dpi: int = opt(
        150,
        "Resolution",
        kind="slider",
        min=36,
        max=600,
        unit="DPI",
        help="72 = screen, 150 = good, 300 = print quality.",
    )
    page_range: str = opt("all", "Pages", help="e.g. 1-3, 5, 8- (all = every page)")
    quality: int = opt(
        90, "Quality", kind="slider", min=1, max=100, unit="%", targets=("pages:jpg", "pages:webp")
    )
    transparent: bool = opt(False, "Transparent background", targets=("pages:png", "pages:webp"))
    grayscale: bool = opt(False, "Black & white")
    multipage_tiff: bool = opt(
        False,
        "Single multi-page TIFF",
        targets=("pages:tiff",),
        help="One .tiff file with every page instead of a folder.",
    )


class DocumentToImagesConverter(Converter):
    family = MediaFamily.DOCUMENT
    options_cls = PageImageOptions

    def targets(self, files: list[DetectedFile]):
        if not all("pdf" in reachable(f.ext) for f in files):
            return []
        return [
            self.target(
                f"pages:{ext}",
                label,
                ext,
                "Images (one per page)",
                f"Render each page as a {label} image",
            )
            for ext, (label, _) in PAGE_FORMATS.items()
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: PageImageOptions = job.options
        ext = job.target.ext
        pil_format = PAGE_FORMATS[ext][1]
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Preparing {f.name}")
            with tempfile.TemporaryDirectory(prefix="umc-pages-") as tmp:
                src = f.path
                if f.ext != "pdf":
                    src = convert_document(f, "pdf", Path(tmp) / f"{f.path.stem}.pdf", job)
                pages = pdf.parse_page_range(o.page_range, pdf.page_count(src))
                outputs.append(self._render(f, src, pages, o, ext, pil_format, job, i, n))
        job.report(1.0, "Done")
        return outputs

    def _render(self, f, src, pages, o, ext, pil_format, job, i, n) -> Path:
        alpha = o.transparent and ext in ("png", "webp")
        kw: dict = {"dpi": (o.dpi, o.dpi)}
        if ext in ("jpg", "webp"):
            kw["quality"] = o.quality
        if ext == "tiff":
            kw["compression"] = "tiff_lzw"
        width = max(3, len(str(max(pages) + 1)))
        multi = ext == "tiff" and o.multipage_tiff
        rendered = []
        if multi:
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, "tiff")
        else:
            dest = unique_path(job.output_dir_for(f.path), f"{f.path.stem}_pages", "")
            dest.mkdir(parents=True)
        for k, (page_no, img) in enumerate(pdf.render_pages(src, pages, o.dpi, alpha, o.grayscale)):
            job.check_cancelled()
            job.report((i + (k + 1) / len(pages)) / n, f"Rendering page {page_no + 1} of {f.name}")
            if ext == "jpg" or (ext == "bmp" and img.mode == "RGBA"):
                img = flatten(img, "#ffffff") if img.mode != "L" else img
            if multi:
                rendered.append(img)
            else:
                img.save(
                    dest / f"{f.path.stem}_page{page_no + 1:0{width}d}.{ext}", pil_format, **kw
                )
        if multi and rendered:
            rendered[0].save(dest, "TIFF", save_all=True, append_images=rendered[1:], **kw)
        return dest
