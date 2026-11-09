"""Image ↔ image, and images → PDF."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from umc.converters.base import Converter
from umc.converters.imaging import can_save, flatten, open_image, parse_color
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt

# target id → (label, Pillow format name)
IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("PNG", "PNG"),
    "jpg": ("JPG", "JPEG"),
    "webp": ("WEBP", "WEBP"),
    "avif": ("AVIF", "AVIF"),
    "heic": ("HEIC", "HEIF"),
    "gif": ("GIF", "GIF"),
    "bmp": ("BMP", "BMP"),
    "tiff": ("TIFF", "TIFF"),
    "ico": ("ICO", "ICO"),
    "tga": ("TGA", "TGA"),
    "qoi": ("QOI", "QOI"),
}

LOSSY = ("jpg", "webp", "avif", "heic")
NO_ALPHA = {"jpg", "bmp"}
ANIMATED = {"gif", "webp", "png", "tiff", "avif"}
METADATA = {"jpg", "webp", "png", "tiff", "avif", "heic"}

RESIZE_CHOICES = [
    ("none", "Original size"),
    ("percent", "Scale by percentage"),
    ("fit", "Fit within width × height"),
    ("exact", "Exact width × height (stretch)"),
]


@dataclass
class ImageOptions:
    quality: int = opt(
        90,
        "Quality",
        kind="slider",
        min=1,
        max=100,
        unit="%",
        targets=LOSSY,
        help="Higher = better looking, bigger file.",
    )
    lossless: bool = opt(False, "Lossless", targets=("webp", "avif"))
    target_size_kb: int = opt(
        0,
        "Target file size",
        min=0,
        max=1_000_000,
        unit="KB",
        targets=LOSSY,
        help="0 = off. Finds the best quality that fits; shrinks the image only if it must.",
    )
    png_compression: int = opt(
        6,
        "Compression level",
        kind="slider",
        min=0,
        max=9,
        targets=("png",),
        help="Higher = smaller file, slower save. Image quality is identical.",
    )
    tiff_compression: str = opt(
        "tiff_lzw",
        "Compression",
        targets=("tiff",),
        choices=[
            ("none", "None"),
            ("tiff_lzw", "LZW (lossless)"),
            ("tiff_deflate", "Deflate (lossless)"),
            ("jpeg", "JPEG (lossy)"),
        ],
    )
    resize: str = opt("none", "Resize", choices=RESIZE_CHOICES)
    scale_percent: int = opt(
        100,
        "Scale",
        kind="slider",
        min=1,
        max=400,
        unit="%",
        show_if=lambda v: v["resize"] == "percent",
    )
    width: int = opt(
        1920,
        "Width",
        min=1,
        max=30000,
        unit="px",
        show_if=lambda v: v["resize"] in ("fit", "exact"),
    )
    height: int = opt(
        1080,
        "Height",
        min=1,
        max=30000,
        unit="px",
        show_if=lambda v: v["resize"] in ("fit", "exact"),
    )
    ico_size: int = opt(
        256,
        "Largest icon size",
        targets=("ico",),
        choices=[(s, f"{s} × {s}") for s in (16, 24, 32, 48, 64, 128, 256)],
    )
    grayscale: bool = opt(False, "Convert to black & white")
    auto_rotate: bool = opt(True, "Auto-rotate (use camera orientation)")
    keep_metadata: bool = opt(True, "Keep metadata (EXIF, colour profile)")
    keep_animation: bool = opt(True, "Keep animation (GIF/WEBP/APNG)", targets=tuple(ANIMATED))
    background: str = opt(
        "#ffffff",
        "Background for transparency",
        kind="color",
        help="Used where the output format can't store transparency.",
    )


def _process_frame(img: Image.Image, o: ImageOptions, target: str) -> Image.Image:
    if o.resize == "percent" and o.scale_percent != 100:
        w = max(1, round(img.width * o.scale_percent / 100))
        h = max(1, round(img.height * o.scale_percent / 100))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    elif o.resize == "fit":
        img = ImageOps.contain(img, (o.width, o.height), Image.Resampling.LANCZOS)
    elif o.resize == "exact":
        img = img.resize((o.width, o.height), Image.Resampling.LANCZOS)

    if o.grayscale:
        img = img.convert("LA" if "A" in img.getbands() else "L")

    if target in NO_ALPHA:
        img = flatten(img, o.background)
        if o.grayscale:
            img = img.convert("L")
    elif img.mode not in ("RGB", "RGBA", "L", "LA", "P", "1", "I;16") or img.mode == "CMYK":
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    if target in ("heic", "avif") and img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    if target == "webp" and img.mode in ("LA", "I;16"):
        img = img.convert("RGBA")
    if target == "ico":
        # Icons are square: centre the image on a transparent square canvas.
        img = img.convert("RGBA")
        img = ImageOps.contain(img, (o.ico_size, o.ico_size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (o.ico_size, o.ico_size), (0, 0, 0, 0))
        canvas.paste(img, ((o.ico_size - img.width) // 2, (o.ico_size - img.height) // 2))
        img = canvas
    return img


def _encode(img: Image.Image, pil_format: str, kw: dict, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=pil_format, **{**kw, "quality": quality, "lossless": False})
    return buf.getvalue()


def _fit_to_size(img: Image.Image, pil_format: str, kw: dict, limit: int) -> bytes:
    """Highest quality (binary search) that fits ``limit`` bytes; downscale only if q=10 can't."""
    kw = {k: v for k, v in kw.items() if k not in ("quality", "lossless")}
    while True:
        lo, hi, best = 10, 95, None
        while lo <= hi:
            q = (lo + hi) // 2
            data = _encode(img, pil_format, kw, q)
            if len(data) <= limit:
                best, lo = data, q + 1
            else:
                hi = q - 1
        if best is not None:
            return best
        if min(img.size) <= 16:
            raise ConversionError(
                "The image can't be made that small. Choose a bigger target size."
            )
        img = img.resize(
            (max(16, round(img.width * 0.8)), max(16, round(img.height * 0.8))),
            Image.Resampling.LANCZOS,
        )


def _save_kwargs(o: ImageOptions, target: str, src: Image.Image) -> dict:
    kw: dict = {}
    if target in LOSSY:
        kw["quality"] = o.quality
    if target == "jpg":
        kw["optimize"] = True
        kw["subsampling"] = 0 if o.quality >= 90 else 2
    if target in ("webp", "avif") and o.lossless:
        kw["lossless"] = True
    if target == "png":
        kw["compress_level"] = o.png_compression
    if target == "tiff" and o.tiff_compression != "none":
        kw["compression"] = o.tiff_compression
    if target == "ico":
        # Embed every standard size up to the chosen one for crisp icons everywhere.
        kw["sizes"] = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= o.ico_size]
    if o.keep_metadata and target in METADATA:
        icc = src.info.get("icc_profile")
        if icc:
            kw["icc_profile"] = icc
    return kw


class ImageConverter(Converter):
    family = MediaFamily.IMAGE
    options_cls = ImageOptions

    def targets(self, files: list[DetectedFile]):
        return [
            self.target(tid, label, tid, "Image")
            for tid, (label, pil) in IMAGE_FORMATS.items()
            if can_save(pil)
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: ImageOptions = job.options
        target = job.target.id
        pil_format = IMAGE_FORMATS[target][1]
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Converting {f.name} ({i + 1}/{n})")
            outputs.append(self._convert_one(f.path, job, o, target, pil_format))
        job.report(1.0, "Done")
        return outputs

    def _convert_one(self, src: Path, job, o: ImageOptions, target: str, pil_format: str) -> Path:
        img = open_image(src)
        exif = None
        if o.keep_metadata and target in METADATA:
            exif_obj = img.getexif()
            if o.auto_rotate and 0x0112 in exif_obj:
                exif_obj[0x0112] = 1  # we apply the rotation, so reset the tag
            exif = exif_obj.tobytes() if len(exif_obj) else None

        animated = (
            o.keep_animation
            and target in ANIMATED
            and getattr(img, "n_frames", 1) > 1
            and src.suffix.lower() in (".gif", ".webp", ".png", ".apng", ".tiff", ".tif", ".avif")
        )
        if animated:
            frames, durations = [], []
            for frame in ImageSequence.Iterator(img):
                durations.append(frame.info.get("duration", img.info.get("duration", 100)))
                fr = frame.convert("RGBA")
                frames.append(_process_frame(fr, o, target))
        else:
            base = ImageOps.exif_transpose(img) if o.auto_rotate else img
            frames = [_process_frame(base, o, target)]
            durations = []

        kw = _save_kwargs(o, target, img)
        if exif:
            kw["exif"] = exif
        if animated:
            kw.update(
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=img.info.get("loop", 0),
            )
            if target == "gif":
                kw["disposal"] = 2
        dest = unique_path(job.output_dir_for(src), src.stem, target)
        try:
            if o.target_size_kb > 0 and not animated and target in LOSSY:
                dest.write_bytes(_fit_to_size(frames[0], pil_format, kw, o.target_size_kb * 1024))
                return dest
            frames[0].save(dest, format=pil_format, **kw)
        except Exception as exc:
            from umc.core.errors import ConversionError

            dest.unlink(missing_ok=True)
            raise ConversionError(
                f"Couldn't save “{src.name}” as {target.upper()}.", details=repr(exc)
            ) from exc
        return dest


# --------------------------------------------------------------------------- images → PDF

PAGE_SIZES_MM = {"a4": (210, 297), "letter": (215.9, 279.4), "a5": (148, 210), "a3": (297, 420)}


@dataclass
class ImagesToPdfOptions:
    combine: bool = opt(True, "Combine all images into one PDF", help="Off = one PDF per image.")
    page_size: str = opt(
        "image",
        "Page size",
        choices=[
            ("image", "Same as image"),
            ("a4", "A4"),
            ("letter", "US Letter"),
            ("a5", "A5"),
            ("a3", "A3"),
        ],
    )
    orientation: str = opt(
        "auto",
        "Orientation",
        choices=[
            ("auto", "Match each image"),
            ("portrait", "Portrait"),
            ("landscape", "Landscape"),
        ],
        show_if=lambda v: v["page_size"] != "image",
    )
    margin_mm: int = opt(
        10,
        "Margin",
        kind="slider",
        min=0,
        max=50,
        unit="mm",
        show_if=lambda v: v["page_size"] != "image",
    )
    dpi: int = opt(150, "Resolution", kind="slider", min=72, max=600, step=1, unit="DPI")
    quality: int = opt(90, "JPEG quality", kind="slider", min=10, max=100, unit="%")
    background: str = opt("#ffffff", "Page / transparency background", kind="color")


class ImagesToPdfConverter(Converter):
    family = MediaFamily.IMAGE
    options_cls = ImagesToPdfOptions

    def targets(self, files):
        return [
            self.target(
                "images:pdf", "PDF", "pdf", "Document", "Put the images into a PDF document"
            )
        ]

    def _page(self, img: Image.Image, o: ImagesToPdfOptions) -> Image.Image:
        img = flatten(ImageOps.exif_transpose(img), o.background)
        if o.page_size == "image":
            return img
        w_mm, h_mm = PAGE_SIZES_MM[o.page_size]
        landscape = (o.orientation == "landscape") or (
            o.orientation == "auto" and img.width > img.height
        )
        if landscape:
            w_mm, h_mm = h_mm, w_mm
        px = lambda mm: round(mm / 25.4 * o.dpi)  # noqa: E731
        page = Image.new("RGB", (px(w_mm), px(h_mm)), parse_color(o.background))
        box = (max(1, page.width - 2 * px(o.margin_mm)), max(1, page.height - 2 * px(o.margin_mm)))
        fitted = ImageOps.contain(img, box, Image.Resampling.LANCZOS)
        page.paste(fitted, ((page.width - fitted.width) // 2, (page.height - fitted.height) // 2))
        return page

    def run(self, job: ConversionJob) -> list[Path]:
        o: ImagesToPdfOptions = job.options
        n = len(job.files)
        pages, outputs = [], []
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n * 0.9, f"Adding {f.name}")
            page = self._page(open_image(f.path), o)
            if o.combine:
                pages.append(page)
            else:
                dest = unique_path(job.output_dir_for(f.path), f.path.stem, "pdf")
                page.save(dest, "PDF", resolution=o.dpi, quality=o.quality)
                outputs.append(dest)
        if o.combine and pages:
            first = job.files[0].path
            stem = first.stem if n == 1 else f"{first.stem} + {n - 1} more"
            dest = unique_path(job.output_dir_for(first), stem, "pdf")
            pages[0].save(
                dest,
                "PDF",
                resolution=o.dpi,
                quality=o.quality,
                save_all=True,
                append_images=pages[1:],
            )
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
