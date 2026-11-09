"""Vector graphics: SVG / EPS / PS / AI ↔ each other, → PDF / PNG / JPG / WEBP / TIFF;
PDF pages → SVG; and raster images → SVG by tracing (vtracer, MIT).

Everything goes through PDF: SVG and modern AI files are read by MuPDF (tools/pdf.py), EPS / PS
(and pre-2000 PostScript-based AI) by Ghostscript. PDF → SVG is MuPDF, PDF → EPS / PS Ghostscript.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.imaging import flatten, open_image
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ghostscript, pdf

VECTOR_OUT = {"svg": "SVG", "eps": "EPS", "ps": "PS"}
RASTER_OUT = {"png": "PNG", "jpg": "JPG", "webp": "WEBP", "tiff": "TIFF"}
PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP", "tiff": "TIFF"}
RASTER_TARGETS = tuple(f"vec:{e}" for e in RASTER_OUT)


@dataclass
class VectorOptions:
    width_px: int = opt(
        0,
        "Image width",
        kind="int",
        min=0,
        max=20000,
        unit="px",
        targets=RASTER_TARGETS,
        help="0 = the drawing's own size (SVG) or 150 DPI (EPS / PS / AI). Height follows.",
    )
    transparent: bool = opt(
        True, "Transparent background", targets=("vec:png", "vec:webp", "vec:tiff")
    )
    background: str = opt(
        "#ffffff",
        "Background colour",
        kind="color",
        targets=RASTER_TARGETS,
        show_if=lambda v: not v.get("transparent", True),
    )
    quality: int = opt(
        90, "Quality", kind="slider", min=10, max=100, unit="%", targets=("vec:jpg", "vec:webp")
    )
    text_as_paths: bool = opt(
        True,
        "Turn text into outlines",
        targets=("svg", "pages:svg"),
        help="Looks identical everywhere. Off keeps text selectable but needs the fonts installed.",
    )


def _to_pdf(f: DetectedFile, tmp: Path, job: ConversionJob) -> Path:
    dest = tmp / f"{f.path.stem}.pdf"
    if f.ext in ("svg", "ai") and pdf.vector_to_pdf(f.path, f.ext, dest):
        return dest
    # EPS, PS, and old PostScript-based .ai files
    return ghostscript.to_pdf(f.path, dest, eps=f.ext != "ps", cancel_event=job.cancel_event)


def _page_names(stem: str, count: int, ext: str) -> list[str]:
    return (
        [stem] if count == 1 else [f"{stem}_{n:0{len(str(count))}d}" for n in range(1, count + 1)]
    )


def _write_raster(
    job: ConversionJob, src_pdf: Path, f: DetectedFile, fmt: str, o: VectorOptions, default_dpi: int
) -> list[Path]:
    alpha = o.transparent and fmt != "jpg"
    outputs = []
    count = pdf.pdf_page_count(src_pdf)
    for i, stem in enumerate(_page_names(f.path.stem, count, fmt)):
        job.check_cancelled()
        img = pdf.render_page(src_pdf, i, o.width_px, default_dpi, alpha)
        if not alpha:
            img = flatten(img, o.background if not o.transparent else "#ffffff")
        dest = unique_path(job.output_dir_for(f.path), stem, fmt)
        kw = {"quality": o.quality} if fmt in ("jpg", "webp") else {}
        if fmt == "tiff":
            kw = {"compression": "tiff_deflate"}
        img.save(dest, PIL_FORMAT[fmt], **kw)
        outputs.append(dest)
    return outputs


def _write_svgs(job: ConversionJob, src_pdf: Path, f: DetectedFile, o: VectorOptions) -> list[Path]:
    outputs = []
    count = pdf.pdf_page_count(src_pdf)
    for i, stem in enumerate(_page_names(f.path.stem, count, "svg")):
        job.check_cancelled()
        dest = unique_path(job.output_dir_for(f.path), stem, "svg")
        dest.write_text(pdf.page_to_svg(src_pdf, i, o.text_as_paths), encoding="utf-8")
        outputs.append(dest)
    return outputs


class VectorConverter(Converter):
    family = MediaFamily.VECTOR
    options_cls = VectorOptions

    def targets(self, files):
        single = files[0].ext if len(files) == 1 else ""
        out = [self.target(e, label, e, "Vector") for e, label in VECTOR_OUT.items() if e != single]
        out.append(
            self.target("vec:pdf", "PDF", "pdf", "Document", "Keeps everything sharp at any zoom")
        )
        out += [self.target(f"vec:{e}", label, e, "Image") for e, label in RASTER_OUT.items()]
        return out

    def run(self, job: ConversionJob) -> list[Path]:
        o: VectorOptions = job.options
        tid = job.target.id
        outputs: list[Path] = []
        with tempfile.TemporaryDirectory(prefix="umc-vector-") as tmp:
            for i, f in enumerate(job.files):
                job.check_cancelled()
                job.report(i / len(job.files), f"Converting {f.name}")
                src_pdf = _to_pdf(f, Path(tmp), job)
                if tid == "vec:pdf":
                    dest = unique_path(job.output_dir_for(f.path), f.path.stem, "pdf")
                    dest.write_bytes(src_pdf.read_bytes())
                    outputs.append(dest)
                elif tid == "svg":
                    outputs += _write_svgs(job, src_pdf, f, o)
                elif tid in ("eps", "ps"):
                    dest = unique_path(job.output_dir_for(f.path), f.path.stem, tid)
                    ghostscript.from_pdf(src_pdf, dest, tid, cancel_event=job.cancel_event)
                    outputs.append(dest)
                else:
                    outputs += _write_raster(
                        job,
                        src_pdf,
                        f,
                        job.target.ext,
                        o,
                        default_dpi=72 if f.ext == "svg" else 150,
                    )
        job.report(1.0, "Done")
        return outputs


class PdfToSvgConverter(Converter):
    """PDF pages → SVG (one file per page)."""

    family = MediaFamily.DOCUMENT
    options_cls = VectorOptions

    def targets(self, files):
        if all(f.ext == "pdf" for f in files):
            return [
                self.target(
                    "pages:svg", "SVG", "svg", "Vector", "Each page as a scalable vector image"
                )
            ]
        return []

    def run(self, job: ConversionJob) -> list[Path]:
        outputs = []
        for i, f in enumerate(job.files):
            job.report(i / len(job.files), f"Converting {f.name}")
            outputs += _write_svgs(job, f.path, f, job.options)
        job.report(1.0, "Done")
        return outputs


# --------------------------------------------------------------------------- tracing


@dataclass
class TraceOptions:
    colour_mode: str = opt(
        "color", "Colours", choices=[("color", "Full colour"), ("binary", "Black & white")]
    )
    colour_precision: int = opt(
        6,
        "Colour detail",
        kind="slider",
        min=1,
        max=8,
        show_if=lambda v: v["colour_mode"] == "color",
        help="Fewer = flatter, poster-like result and a smaller file.",
    )
    gradient_step: int = opt(
        16,
        "Gradient step",
        kind="slider",
        min=1,
        max=128,
        show_if=lambda v: v["colour_mode"] == "color",
        help="Bigger = fewer colour layers.",
    )
    speckle: int = opt(4, "Ignore specks smaller than", kind="slider", min=0, max=128, unit="px")
    curves: str = opt(
        "spline",
        "Curves",
        choices=[
            ("spline", "Smooth curves"),
            ("polygon", "Straight lines"),
            ("none", "Pixel-exact"),
        ],
    )
    corner_threshold: int = opt(
        60,
        "Corner sharpness",
        kind="slider",
        min=0,
        max=180,
        unit="°",
        show_if=lambda v: v["curves"] == "spline",
        help="Angles sharper than this stay corners.",
    )
    max_size: int = opt(
        2000,
        "Shrink big images to",
        kind="int",
        min=100,
        max=10000,
        unit="px",
        help="Tracing time grows fast with size; the SVG scales back up perfectly.",
    )


class TraceConverter(Converter):
    """Raster image → SVG paths."""

    family = MediaFamily.IMAGE
    options_cls = TraceOptions

    def targets(self, files):
        return [
            self.target(
                "trace:svg", "SVG (trace)", "svg", "Vector", "Turn the picture into scalable shapes"
            )
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        import vtracer

        o: TraceOptions = job.options
        outputs = []
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / len(job.files), f"Tracing {f.name}")
            img = open_image(f.path)
            img = img.convert("RGBA")
            scale = 1.0
            if max(img.size) > o.max_size:
                scale = max(img.size) / o.max_size
                img.thumbnail((o.max_size, o.max_size))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            try:
                svg = vtracer.convert_raw_image_to_svg(
                    buf.getvalue(),
                    img_format="png",
                    colormode=o.colour_mode,
                    hierarchical="stacked",
                    mode=o.curves,
                    filter_speckle=o.speckle,
                    color_precision=o.colour_precision,
                    layer_difference=o.gradient_step,
                    corner_threshold=o.corner_threshold,
                )
            except Exception as exc:  # vtracer raises plain exceptions from Rust
                raise ConversionError(f"Couldn't trace “{f.name}”.", details=repr(exc)) from exc
            if scale != 1.0:  # show it at the original size
                w, h = round(img.width * scale), round(img.height * scale)
                svg = svg.replace(
                    f'width="{img.width}" height="{img.height}"',
                    f'width="{w}" height="{h}" viewBox="0 0 {img.width} {img.height}"',
                    1,
                )
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, "svg")
            dest.write_text(svg, encoding="utf-8")
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
