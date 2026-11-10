"""Filters and one-click looks (no Qt): blur, sharpen, looks. Each is an ``effects.Effect``.

Blur and sharpen work on premultiplied colour, so transparent edges don't get dark fringes.
``web/src/editor/filters.ts`` does the same on the website (its blur is a three-pass box blur,
close to but not pixel-identical with Pillow's Gaussian). The looks are shared exactly: the list
below must match ``web/src/editor/looks.json`` (tested).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageFilter

from umc.editor.adjust import Adjustments


def _premultiplied(image: Image.Image, fn) -> Image.Image:
    return fn(image.convert("RGBA").convert("RGBa")).convert("RGBA")


@dataclass(frozen=True)
class Blur:
    radius: float = 0.0  # px (the Gaussian's standard deviation)

    @property
    def identity(self) -> bool:
        return self.radius <= 0

    def margin(self) -> int:
        return math.ceil(3 * self.radius)

    def __call__(self, image: Image.Image, scale: float = 1.0) -> Image.Image:
        r = self.radius * scale
        if r <= 0:
            return image.copy()
        return _premultiplied(image, lambda im: im.filter(ImageFilter.GaussianBlur(r)))


@dataclass(frozen=True)
class Sharpen:
    amount: int = 0  # %, 0…500
    radius: float = 1.0  # px
    threshold: int = 0  # 0…255: leave differences below this alone (keeps skin and sky smooth)

    @property
    def identity(self) -> bool:
        return self.amount <= 0

    def margin(self) -> int:
        return math.ceil(3 * self.radius) + 1

    def __call__(self, image: Image.Image, scale: float = 1.0) -> Image.Image:
        if self.identity:
            return image.copy()
        f = ImageFilter.UnsharpMask(max(0.1, self.radius * scale), self.amount, self.threshold)
        return _premultiplied(image, lambda im: im.filter(f))


# The same looks as web/src/editor/looks.json: name → (label, steps).
LOOKS: dict[str, tuple[str, tuple[dict, ...]]] = {
    "warm": ("Warm", ({"temperature": 35, "vibrance": 15},)),
    "cool": ("Cool", ({"temperature": -35, "tint": 5},)),
    "vivid": ("Vivid", ({"contrast": 15, "saturation": 25, "vibrance": 30},)),
    "bw": ("Black & white", ({"saturation": -100, "contrast": 10},)),
    "noir": (
        "Noir",
        ({"saturation": -100}, {"contrast": 40, "levels": {"rgb": {"black": 15, "white": 240}}}),
    ),
    "sepia": ("Sepia", ({"saturation": -100}, {"temperature": 60, "tint": 10, "contrast": -5})),
    "faded": (
        "Faded",
        (
            {
                "saturation": -20,
                "contrast": -10,
                "levels": {"rgb": {"out_black": 30, "out_white": 235}},
            },
        ),
    ),
    "vintage": (
        "Vintage",
        (
            {"saturation": -30, "temperature": 30},
            {"levels": {"rgb": {"out_black": 25}}, "curves": {"b": [[0, 30], [255, 220]]}},
        ),
    ),
    "dramatic": (
        "Dramatic",
        (
            {
                "contrast": 30,
                "saturation": -15,
                "curves": {"rgb": [[0, 0], [64, 48], [192, 210], [255, 255]]},
            },
        ),
    ),
    "matte": (
        "Matte",
        ({"saturation": -10, "curves": {"rgb": [[0, 35], [128, 128], [255, 240]]}},),
    ),
    "airy": (
        "Bright & airy",
        ({"exposure": 0.4, "contrast": -10, "saturation": -10, "temperature": -10},),
    ),
}


@dataclass(frozen=True)
class Look:
    name: str = ""  # a key of LOOKS ("" = none)
    strength: float = 1.0  # 0…1: how much of the look, mixed with the original

    @property
    def identity(self) -> bool:
        return self.name not in LOOKS or self.strength <= 0

    def __call__(self, image: Image.Image, scale: float = 1.0) -> Image.Image:
        image = image.convert("RGBA")
        if self.identity:
            return image.copy()
        out = image
        for step in LOOKS[self.name][1]:
            out = Adjustments.from_settings(step)(out)
        if self.strength >= 1:
            return out
        mixed = Image.blend(image, out, self.strength)
        mixed.putalpha(image.getchannel("A"))
        return mixed
