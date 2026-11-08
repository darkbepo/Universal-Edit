"""Generate packaging/icon.ico and icon.png (run once; the outputs are committed)."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "packaging"
S = 1024


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((48, 48, S - 48, S - 48), radius=220, fill=(91, 91, 240, 255))
    white = (255, 255, 255, 255)
    # two arrows: → on top, ← below ("convert")
    d.rounded_rectangle((250, 340, 690, 410), radius=35, fill=white)
    d.polygon([(660, 260), (800, 375), (660, 490)], fill=white)
    d.rounded_rectangle((334, 614, 774, 684), radius=35, fill=white)
    d.polygon([(364, 534), (224, 649), (364, 764)], fill=white)
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


if __name__ == "__main__":
    main()
