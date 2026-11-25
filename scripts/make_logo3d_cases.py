"""Write tests/fixtures/logo3d_cases.json: what the desktop's 3D logo model
(``umc.editor3d.logo`` / ``pick``) makes of small pictures, shapes and faces. The website's
ports (web/src/editor3d/logo.ts / pick.ts) check the same cases. Regenerate only on purpose:
    python scripts/make_logo3d_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from umc.editor3d import logo, pick
from umc.editor3d.camera import OrbitCamera

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "logo3d_cases.json"

MASKS = {
    "square": ["......", ".####.", ".####.", ".####.", "......"],
    "ring": ["#######", "#.....#", "#.###.#", "#.#.#.#", "#.###.#", "#.....#", "#######"],
    "diagonal": ["##..", "##..", "..##", "..##"],
    "stairs": [
        "#.......",
        "##......",
        "###.....",
        "####....",
        "#####...",
        "######..",
        "#######.",
        "########",
    ],
    "letters": [
        "..........................",
        ".####...#####...######....",
        ".#..#...#...#.......#.....",
        ".####...#####......#......",
        ".#..#...#...#.....#.......",
        ".#..#...#####....######...",
        "..........................",
    ],
    "speck": ["##########", "##########", "##########", "..........", "........#.", ".........."],
    "empty": ["....", "...."],
}


def grid(rows: list[str]) -> np.ndarray:
    return np.array([[c == "#" for c in r] for r in rows], np.uint8)


def r6(v: float) -> float:
    return round(float(v), 6)


def ring6(r):
    return [[r6(x), r6(y)] for x, y in r]


def mesh_facts(m: logo.Mesh) -> dict:
    lo, hi = m.vertices.min(axis=0), m.vertices.max(axis=0)
    return {
        "triangles": len(m.faces),
        "volume": r6(m.volume),
        "min": [r6(v) for v in lo],
        "max": [r6(v) for v in hi],
    }


def picture(w: int, h: int, fill) -> bytes:
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes(fill(x, y))
    return bytes(out)


def main() -> None:
    cases: dict = {
        "mask": [],
        "trace": [],
        "shape": [],
        "badge": [],
        "frame": [],
        "ray": [],
        "face": [],
    }

    pics = {
        "alpha": (3, 2, lambda x, y: (10, 20, 30, [0, 127, 128, 255, 200, 1][y * 3 + x])),
        "grey": (4, 1, lambda x, y: (x * 80, x * 80, x * 80, 255 if x != 3 else 100)),
        "colour": (3, 1, lambda x, y: [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)][x]),
    }
    for w, h, fill in pics.values():
        rgba = picture(w, h, fill)
        for mode, threshold, invert in (
            ("alpha", 128, False),
            ("dark", 128, False),
            ("light", 100, False),
            ("dark", 128, True),
        ):
            m = logo.mask(rgba, w, h, mode, threshold, invert)
            cases["mask"].append(
                {
                    "rgba": list(rgba),
                    "w": w,
                    "h": h,
                    "mode": mode,
                    "threshold": threshold,
                    "invert": invert,
                    "mask": m.reshape(-1).tolist(),
                    "default": logo.default_mode(rgba, w, h),
                }
            )

    for name, rows in MASKS.items():
        for detail in logo.DETAIL:
            rings = logo.trace(grid(rows), detail)
            cases["trace"].append(
                {"name": name, "rows": rows, "detail": detail, "rings": [ring6(r) for r in rings]}
            )

    for name, rows in MASKS.items():
        for width in (40.0, 2.0):
            entry = {"name": name, "rows": rows, "width": width}
            try:
                s = logo.shape(logo.trace(grid(rows)), width)
            except logo.LogoError as e:
                entry["error"] = str(e)
            else:
                entry.update(
                    {
                        "polygons": [
                            {"outer": ring6(p.outer), "holes": [ring6(h) for h in p.holes]}
                            for p in s.polygons
                        ],
                        "width_mm": r6(s.width),
                        "height_mm": r6(s.height),
                        "dropped": s.dropped,
                        "area": r6(s.area),
                    }
                )
            cases["shape"].append(entry)

    for name in ("ring", "letters", "square"):
        s = logo.shape(logo.trace(grid(MASKS[name])), 30.0)
        for plate, hole in (
            ("none", False),
            ("rectangle", False),
            ("rounded", False),
            ("rounded", True),
            ("rectangle", True),
        ):
            parts = logo.badge(s, 2.5, plate, 3.0, 1.5, hole)
            cases["badge"].append(
                {
                    "name": name,
                    "rows": MASKS[name],
                    "plate": plate,
                    "hole": hole,
                    "parts": [mesh_facts(p) for p in parts],
                }
            )

    for normal in ([0, 0, 1], [0, 0, -1], [0, -1, 0], [1, 0, 0], [1, 1, 1], [0.3, -0.2, 0.9]):
        for rot in (0.0, 30.0, -90.0):
            m = logo.placement([1.0, 2.0, 3.0], normal, rot, logo.SINK_MM)
            cases["frame"].append(
                {
                    "normal": normal,
                    "rotation": rot,
                    "matrix": [[r6(v) for v in row] for row in m.tolist()],
                }
            )

    for yaw, pitch, dist, x, y in (
        (-45, 35.264, 100, 200, 150),
        (0, 0, 50, 0, 0),
        (-90, 90, 80, 399, 299),
        (30, -20, 10, 120, 40),
    ):
        cam = OrbitCamera(target=np.array([1.0, 2.0, 3.0]), distance=dist, yaw=yaw, pitch=pitch)
        o, d = pick.ray(cam, x, y, 400, 300)
        cases["ray"].append(
            {
                "yaw": yaw,
                "pitch": pitch,
                "distance": dist,
                "target": [1, 2, 3],
                "x": x,
                "y": y,
                "w": 400,
                "h": 300,
                "origin": [r6(v) for v in o],
                "direction": [r6(v) for v in d],
            }
        )

    # a box 40 × 40 × 10 on the bed with a ramp on the +X side; STL-style repeated corners
    v = [
        [-20, -20, 0],
        [20, -20, 0],
        [20, 20, 0],
        [-20, 20, 0],
        [-20, -20, 10],
        [20, -20, 10],
        [20, 20, 10],
        [-20, 20, 10],
        [30, -20, 0],
        [30, 20, 0],
    ]
    f = [
        [0, 2, 1],
        [0, 3, 2],  # bottom
        [4, 5, 6],
        [4, 6, 7],  # top
        [0, 1, 5],
        [0, 5, 4],  # front
        [3, 7, 6],
        [3, 6, 2],  # back
        [0, 4, 7],
        [0, 7, 3],  # left
        [1, 8, 5],
        [8, 9, 6],
        [8, 6, 5],
        [9, 2, 6],  # ramp (two triangles + sides)
        [1, 2, 9],
        [1, 9, 8],  # under the ramp
    ]
    soup_v = np.array([v[i] for tri in f for i in tri], float)
    soup_f = np.arange(len(soup_v)).reshape(-1, 3)
    for origin, direction, width in (
        ([0, 0, 50], [0, 0, -1], 20.0),
        ([0, -50, 5], [0, 1, 0], 8.0),
        ([60, 0, 20], [-1, 0, -0.5], 6.0),
        ([0, 0, 50], [0, 0, -1], 50.0),
        ([100, 100, 100], [0, 0, 1], 5.0),
    ):
        d = np.asarray(direction, float)
        d /= np.linalg.norm(d)
        found = pick.hit(soup_v, soup_f, origin, d)
        entry = {
            "vertices": soup_v.tolist(),
            "origin": origin,
            "direction": direction,
            "width": width,
        }
        if found is None:
            entry["hit"] = None
        else:
            tri, point = found
            face = pick.flat_face(soup_v, soup_f, tri)
            s = logo.shape(logo.trace(grid(MASKS["ring"])), width)
            fp = logo.footprint(s, point, face.normal, 15.0)
            entry.update(
                {
                    "hit": tri,
                    "point": [r6(x) for x in point],
                    "triangles": sorted(face.triangles),
                    "normal": [r6(x) for x in face.normal],
                    "area": r6(face.area(soup_v, soup_f)),
                    "overhang": pick.overhang(fp, soup_v, soup_f, face),
                }
            )
        cases["face"].append(entry)

    OUT.write_text(json.dumps(cases, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
