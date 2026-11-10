"""One layer: an RGBA image placed on the canvas."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from umc.editor.geometry import Rect


@dataclass(eq=False)
class Layer:
    name: str
    image: Image.Image
    x: int = 0
    y: int = 0
    opacity: float = 1.0
    visible: bool = True

    def __post_init__(self) -> None:
        if self.image.mode != "RGBA":
            self.image = to_rgba(self.image)

    @property
    def bounds(self) -> Rect:
        return Rect(self.x, self.y, self.x + self.image.width, self.y + self.image.height)

    def copy(self) -> Layer:
        return Layer(self.name, self.image.copy(), self.x, self.y, self.opacity, self.visible)


def to_rgba(img: Image.Image) -> Image.Image:
    """Any Pillow mode → RGBA (16/32-bit greyscale is scaled down to 8 bit first)."""
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
        import numpy as np

        arr = np.asarray(img.convert("I"), dtype=np.float64)
        top = 65535.0 if img.mode.startswith("I;16") or arr.max() > 255 else 255.0
        img = Image.fromarray((np.clip(arr / top, 0, 1) * 255).astype("uint8"), "L")
    elif img.mode == "F":
        import numpy as np

        arr = np.asarray(img, dtype=np.float64)
        top = 1.0 if arr.max() <= 1.0 else 255.0
        img = Image.fromarray((np.clip(arr / top, 0, 1) * 255).astype("uint8"), "L")
    return img.convert("RGBA")
