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


def blocky(seed: int, n: int = 14, k: int = 3) -> list[str]:
    """Random blocks scaled up k× (Python's own seeded generator: the same in every version).
    Holes and outer edges often lie on one line here — the case that once left gaps."""
    import random

    rnd = random.Random(seed)
    bits = [[rnd.random() < 0.55 for _ in range(n)] for _ in range(n)]
    size = n * k
    return ["".join("#" if bits[y // k][x // k] else "." for x in range(size)) for y in range(size)]


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

    # do these rings cross / touch (themselves or each other)? shape() then keeps the raw ones
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    hole = [(2.0, 2.0), (2.0, 8.0), (8.0, 8.0), (8.0, 2.0)]
    meet_sets = {
        "nested": [square, hole],
        "hole touches outline": [square, [(0.0, 5.0), (3.0, 8.0), (3.0, 2.0)]],
        "hole crosses outline": [square, [(-1.0, 5.0), (3.0, 8.0), (3.0, 2.0)]],
        "bow tie": [[(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]],
        "pinched": [[(0.0, 0.0), (10.0, 0.0), (5.0, 5.0), (10.0, 10.0), (0.0, 10.0), (5.0, 5.0)]],
        "two holes touch": [square, [(2.0, 2.0), (2.0, 5.0), (5.0, 5.0), (5.0, 2.0)],
                            [(5.0, 5.0), (5.0, 8.0), (8.0, 8.0), (8.0, 5.0)]],
        "collinear apart": [[(0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (0.0, 1.0)],
                            [(5.0, 0.0), (5.0, 1.0), (9.0, 1.0), (9.0, 0.0)]],
        "single": [square],
    }  # fmt: skip
    cases["meet"] = [{"name": k, "rings": [[list(p) for p in r] for r in v],
                      "meets": logo._rings_meet(v)} for k, v in meet_sets.items()]  # fmt: skip
    raw_hole = [(0.5, 5.0), (3.0, 8.0), (3.0, 2.0)]
    fallback = logo.shape([square, [(0.0, 5.0), (3.0, 8.0), (3.0, 2.0)]], 10.0,
                          raw=[square, raw_hole])  # fmt: skip
    cases["fallback"] = {
        "rings": [[list(p) for p in square], [[0.0, 5.0], [3.0, 8.0], [3.0, 2.0]]],
        "raw": [[list(p) for p in square], [list(p) for p in raw_hole]],
        "holes": [ring6(h) for h in fallback.polygons[0].holes],
    }

    # random pixels that once left gaps: a corner a hair off an edge after scaling (73) and
    # rings earcut overlapped although they don't touch (86)
    import numpy.random as npr

    rng = npr.default_rng(1)
    cases["noise"] = []
    for t in range(87):
        h, w = (int(v) for v in rng.integers(8, 60, 2))
        rows_n = rng.random((h, w)) < rng.uniform(0.3, 0.8)
        if t in (73, 86):
            rows = ["".join("#" if v else "." for v in r) for r in rows_n]
            for detail in ("smooth", "fine"):
                m = logo.logo_mesh(logo.shape_of_mask(grid(rows), detail, 40.0), 2.0)
                cases["noise"].append({"seed": t, "rows": rows, "detail": detail,
                                       "volume": r6(m.volume)})  # fmt: skip

    cases["busy"] = []
    for seed in range(12):
        rows = blocky(seed)
        for detail in logo.DETAIL:
            s = logo.shape(logo.trace(grid(rows), detail), 30.0)
            m = logo.logo_mesh(s, 2.0)
            cases["busy"].append({"seed": seed, "rows": rows, "detail": detail,
                                  "triangles": len(m.faces), "volume": r6(m.volume),
                                  "area": r6(s.area)})  # fmt: skip

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

    curved(cases)
    OUT.write_text(json.dumps(cases, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


def r9(v: float) -> float:
    return round(float(v), 9)


def ball() -> tuple[np.ndarray, np.ndarray]:
    """An icosphere of radius 30 (642 corners), made here so the cases carry its corners."""
    t = (1 + 5**0.5) / 2
    v = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t],
         [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]]  # fmt: skip
    f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4],
         [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8],
         [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]]  # fmt: skip
    pts = [np.array(p, float) / np.linalg.norm(p) for p in v]
    for _ in range(3):
        mids: dict[tuple[int, int], int] = {}

        def mid(a: int, b: int, mids: dict[tuple[int, int], int] = mids) -> int:
            key = (min(a, b), max(a, b))
            if key not in mids:
                m = pts[a] + pts[b]
                mids[key] = len(pts)
                pts.append(m / np.linalg.norm(m))
            return mids[key]

        nf = []
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]
        f = nf
    return np.round(np.array(pts) * 30, 9), np.array(f, np.int64)


def curved(cases: dict) -> None:
    """Logos on curved surfaces: cap refinement, the surface sampler and fitted logos."""
    cases["refine"] = []
    for name in ("square", "ring", "letters"):
        s = logo.prepare(logo.shape(logo.trace(grid(MASKS[name])), 20.0))
        for poly_i, poly in enumerate(s.polygons):
            xy = [p for r in (poly.outer, *poly.holes) for p in r]
            for max_edge in (logo.refine_edge(20.0), 1.5):
                pts, tris = logo.refine(xy, poly.caps, max_edge)
                a2 = sum((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1])
                         - (pts[b][1] - pts[a][1]) * (pts[c][0] - pts[a][0])
                         for a, b, c in tris)  # fmt: skip
                cases["refine"].append(
                    {
                        "name": name,
                        "polygon": poly_i,
                        "max_edge": max_edge,
                        "points": len(pts),
                        "triangles": len(tris),
                        "outline": len(logo.outline_edges(tris)),
                        "area": r6(a2 / 2),
                    }  # fmt: skip
                )
    v, f = ball()
    cases["ball"] = {"vertices": v.tolist(), "faces": f.tolist()}
    cases["surface"] = []
    for direction, rotation, reach in (([0, 0, 1], 0.0, 10.0), ([0.3, 0.2, 1], 15.0, 14.0),
                                       ([1, -1, 0.2], -40.0, 6.0)):  # fmt: skip
        d = np.asarray(direction, float)
        d /= np.linalg.norm(d)
        point = d * 30
        n = pick.area_normal(v, f, point, [0.0, 0.0, 1.0] if d[2] > 0.5 else d, reach)
        x, y, nn = logo.frame(n, rotation)
        surf = pick.surface(v, f, point, np.array([x, y, nn]), reach)
        uv = np.array([[u, w] for u in np.linspace(-reach, reach, 7)
                       for w in np.linspace(-reach, reach, 5)])  # fmt: skip
        h, nrm, hit = surf.sample(uv)
        cases["surface"].append(
            {
                "direction": direction,
                "rotation": rotation,
                "reach": reach,
                "point": [r9(c) for c in point],
                "normal": [r9(c) for c in n],
                "kept": len(surf.tris),
                "size": surf.size,
                "uv": uv.round(9).tolist(),
                "h": [r9(c) for c in h],
                "hit": hit.tolist(),
                "normals": [[r9(c) for c in row] for row in nrm],
            }  # fmt: skip
        )
    cases["fit"] = []
    s = logo.prepare(logo.shape(logo.trace(grid(MASKS["ring"])), 16.0))
    reach = max(float(np.hypot(*p)) for poly in s.polygons for p in poly.outer)
    d = np.array([0.3, 0.2, 1.0]) / np.linalg.norm([0.3, 0.2, 1.0])
    point = d * 30
    n = pick.area_normal(v, f, point, d, reach)
    for fit in logo.FITS:
        for style in logo.STYLES:
            x, y, nn = logo.frame(n, 15.0)
            surf = pick.surface(v, f, point, np.array([x, y, nn]), reach)
            r = logo.fitted_logo(s, 2.0, point, n, 15.0, 0.2, fit, surf, style)
            cases["fit"].append(
                {
                    "fit": fit,
                    "style": style,
                    "point": [r9(c) for c in point],
                    "normal": [r9(c) for c in n],
                    "width": 16.0,
                    "missed": r.missed,
                    "folded": r.folded,
                    **mesh_facts(r.mesh),
                }  # fmt: skip
            )


if __name__ == "__main__":
    main()
