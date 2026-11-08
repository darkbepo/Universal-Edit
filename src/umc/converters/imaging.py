"""Pillow helpers shared by the image-based converters."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PIL import Image, ImageColor, ImageOps

from umc.core.errors import ConversionError


@cache
def _register_plugins() -> None:
    Image.init()  # populate Image.SAVE with every built-in plugin
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # optional dependency
        pass


def open_image(path: Path) -> Image.Image:
    _register_plugins()
    try:
        img = Image.open(path)
        img.load()
        return img
    except Exception as exc:
        raise ConversionError(f"Couldn't open “{path.name}” as an image.") from exc


def open_image_lazy(path: Path) -> Image.Image:
    """Open without decoding pixels (for reading size/metadata)."""
    _register_plugins()
    try:
        return Image.open(path)
    except Exception as exc:
        raise ConversionError(f"Couldn't open “{path.name}” as an image.") from exc


def can_save(pil_format: str) -> bool:
    _register_plugins()
    return pil_format.upper() in Image.SAVE


def parse_color(value: str, default: str = "#ffffff") -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(value or default)[:3]
    except ValueError:
        return ImageColor.getrgb(default)[:3]


def flatten(img: Image.Image, background: str) -> Image.Image:
    """Composite transparency onto a solid background and return an RGB image."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, parse_color(background))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def fit_into(img: Image.Image, size: tuple[int, int], mode: str, background: str) -> Image.Image:
    """Place ``img`` on a ``size`` canvas.

    mode: "letterbox" (fit, keep aspect, pad), "crop" (fill, keep aspect, crop) or "stretch".
    """
    img = flatten(img, background)
    if mode == "stretch":
        return img.resize(size, Image.Resampling.LANCZOS)
    if mode == "crop":
        return ImageOps.fit(img, size, Image.Resampling.LANCZOS)
    contained = ImageOps.contain(img, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, parse_color(background))
    canvas.paste(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
    return canvas
