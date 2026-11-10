"""OCR: scanned PDFs and photos → searchable PDF (image + invisible text) or plain text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.imaging import open_image
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import pdf, tessdata

TARGETS = {
    "ocr:pdf": (
        "Searchable PDF",
        "pdf",
        "Recognise the text and keep the scan; text becomes selectable",
    ),
    "ocr:txt": ("Text (OCR)", "txt", "Recognise the text and save it as plain text"),
}
_LANGS = tessdata.LANGUAGES


@dataclass
class OcrOptions:
    language: str = opt(
        "eng",
        "Language",
        choices=_LANGS,
        help="The language of the text. English is built in; others download once (1–8 MB).",
    )
    language2: str = opt(
        "none",
        "Second language (optional)",
        choices=[("none", "None"), *_LANGS],
        help="For documents that mix two languages. Slower.",
    )
    combine: bool = opt(
        True,
        "Combine all images into one file",
        help="When several images are added. Off = one output file per image.",
    )
    page_range: str = opt("all", "Pages (PDF)", help="e.g. 1-3, 5, 8- (all = every page)")
    skip_text_pages: bool = opt(
        True,
        "Keep pages that already have text (PDF)",
        help="Only scanned pages are recognised; pages with real text are copied as they are.",
    )
    dpi: int = opt(
        300,
        "Scan resolution (PDF)",
        choices=[
            (200, "200 DPI (faster)"),
            (300, "300 DPI (recommended)"),
            (400, "400 DPI (small print)"),
        ],
    )


def _language(o: OcrOptions) -> list[str]:
    codes = [o.language]
    if o.language2 != "none" and o.language2 != o.language:
        codes.append(o.language2)
    return codes


class _OcrConverter(Converter):
    options_cls = OcrOptions

    def _accepts(self, files: list[DetectedFile]) -> bool:
        raise NotImplementedError

    def targets(self, files: list[DetectedFile]):
        if not self._accepts(files):
            return []
        return [
            self.target(tid, label, ext, "OCR (recognise text)", hint)
            for tid, (label, ext, hint) in TARGETS.items()
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: OcrOptions = job.options
        codes = _language(o)
        # ensure() reports (fraction, message) like the installers; the first 10 % is the download.
        folder = tessdata.ensure(codes, lambda f, m: job.report(0.1 * f, m), job.cancel_event)
        lang = "+".join(codes)
        ext = TARGETS[job.target.id][1]
        files = list(job.files)
        combine = o.combine and len(files) > 1 and all(f.ext != "pdf" for f in files)
        groups = [files] if combine else [[f] for f in files]
        total = len(files)
        outputs: list[Path] = []
        done = 0
        for group in groups:
            out = pdf.ocr_document()
            try:
                for f in group:
                    job.check_cancelled()
                    base = 0.1 + 0.9 * done / total
                    job.report(base, f"Recognising text in {f.name} ({done + 1}/{total})")
                    if f.ext == "pdf":
                        pages = pdf.parse_page_range(o.page_range, pdf.page_count(f.path))

                        def on_page(n: int, of: int, f=f, base=base) -> None:
                            job.check_cancelled()
                            job.report(base + 0.9 / total * n / of, f"{f.name}: page {n} of {of}")

                        pdf.ocr_add_pdf(
                            out, f.path, pages, lang, folder, o.dpi, o.skip_text_pages, on_page
                        )
                    else:
                        pdf.ocr_add_image(out, open_image(f.path), lang, folder)
                    done += 1
                first = group[0].path
                stem = f"{first.stem} + {len(group) - 1} more" if len(group) > 1 else first.stem
                dest = unique_path(
                    job.output_dir_for(first), stem if ext == "txt" else f"{stem}_ocr", ext
                )
                if ext == "txt":
                    dest.write_text(pdf.ocr_text(out), encoding="utf-8")
                else:
                    pdf.ocr_save(out, dest)
                outputs.append(dest)
            finally:
                out.close()
        job.report(1.0, "Done")
        return outputs


class ImageOcrConverter(_OcrConverter):
    family = MediaFamily.IMAGE

    def _accepts(self, files):
        return True


class PdfOcrConverter(_OcrConverter):
    family = MediaFamily.DOCUMENT

    def _accepts(self, files):
        return all(f.ext == "pdf" for f in files)
