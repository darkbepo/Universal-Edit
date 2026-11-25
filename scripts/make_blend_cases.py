"""Write tests/fixtures/blend_cases.json: a two-layer picture composited in every blend mode
(the desktop's editor.blend). web/tests/editor-blend.test.ts checks the same pixels (within 1).
Regenerate only on purpose:
    python scripts/make_blend_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from umc.editor import blend
from umc.editor.document import Document
from umc.editor.layer import Layer

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "blend_cases.json"

# backdrop / source pixel pairs: opaque, half see-through, empty, extremes
BACK = [
    (200, 100, 50, 255), (10, 128, 250, 255), (90, 90, 90, 128), (0, 0, 0, 0),
    (255, 255, 255, 255), (0, 0, 0, 255), (64, 192, 128, 200), (128, 128, 128, 255),
]  # fmt: skip
SOURCE = [
    (100, 200, 30, 255), (250, 5, 128, 255), (255, 0, 0, 255), (40, 200, 90, 255),
    (0, 0, 0, 255), (255, 255, 255, 255), (60, 30, 220, 90), (128, 128, 128, 0),
]  # fmt: skip


def picture(pixels) -> Image.Image:
    img = Image.new("RGBA", (len(pixels), 1))
    img.putdata(pixels)
    return img


def main() -> None:
    cases = []
    for mode in blend.IDS:
        for opacity in (1.0, 0.6):
            doc = Document(
                len(BACK),
                1,
                [
                    Layer("back", picture(BACK)),
                    Layer("top", picture(SOURCE), opacity=opacity, blend=mode),
                ],
            )
            flat = doc.composite()
            out = [flat.getpixel((x, 0)) for x in range(flat.width)]
            cases.append({"mode": mode, "opacity": opacity, "out": [list(p) for p in out]})
    data = {"back": [list(p) for p in BACK], "source": [list(p) for p in SOURCE], "cases": cases,
            "ora": [[m, blend.ora_op(m)] for m in blend.IDS]}  # fmt: skip
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
