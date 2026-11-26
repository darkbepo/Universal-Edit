"""Write tests/fixtures/print3d_cases.json: what the desktop's print tools
(``umc.editor3d.arrange`` / ``meshcheck`` / ``filament``) make of small models. The website's
ports check the same cases. Regenerate only on purpose:
    python scripts/make_print3d_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from umc.editor3d import arrange, filament, meshcheck

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "print3d_cases.json"


def box(x0, y0, z0, x1, y1, z1) -> list[list[list[float]]]:
    """12 outward-facing triangles (corner lists), always in the same order."""
    c = [[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = []
    for a, b, cc, d in quads:
        tris += [[c[a], c[b], c[cc]], [c[a], c[cc], c[d]]]
    return tris


def soup(tris) -> list[float]:
    return [float(x) for t in tris for p in t for x in p]


def indexed(flat: list[float]):
    v = np.array(flat, np.float64).reshape(-1, 3)
    return v, np.arange(len(v)).reshape(-1, 3)


def r6(x: float) -> float:
    return round(float(x), 6)


def main() -> None:
    cube = box(-5, -5, 0, 5, 5, 10)
    tiny = box(20, 0, 0, 20.5, 0.5, 0.5)
    flipped = [list(t) for t in cube]
    flipped[3] = [flipped[3][0], flipped[3][2], flipped[3][1]]
    models = {
        "cube": cube,
        "hole (one side open)": cube[:10],
        "corner missing": cube[1:3] + cube[4:],
        "one triangle flipped": flipped,
        "inside out": [[t[0], t[2], t[1]] for t in cube],
        "repeats, empty, tiny": cube + [cube[0]] + [[[0, 0, 0], [1, 1, 1], [2, 2, 2]]] + tiny,
        "overhang": cube + box(0, -5, 10, 15, 5, 20),
        "flat square": [[[0, 0, 0], [4, 0, 0], [4, 4, 0]], [[0, 0, 0], [4, 4, 0], [0, 4, 0]]],
        "fin": cube[:2] + [[[-5, -5, 0], [5, 5, 0], [0, 0, 8]]] + cube[2:],
        "two cubes": cube + box(10, -5, 0, 20, 5, 10),
        "touching cubes": cube + box(5, -5, 0, 15, 5, 10),
        "stacked cubes": cube + box(-5, -5, 10, 15, 5, 20),
        "touching, one inside out": cube + [[t[0], t[2], t[1]] for t in box(5, 5, 0, 15, 15, 10)],
        "big open shell and a tiny box": box(-100, -100, 0, 100, 100, 2)[2:] + tiny,
    }
    cases: dict = {"models": [], "arrange": [], "settle": [], "fits": [], "filament": []}
    for name, tris in models.items():
        flat = soup(tris)
        v, f = indexed(flat)
        wv, wf = meshcheck.weld(v, f)
        r = meshcheck.check(v, f)
        x = meshcheck.repair(v, f)
        after = meshcheck.check(x.vertices, x.faces)
        cases["models"].append(
            {
                "name": name,
                "soup": flat,
                "welded": {"vertices": len(wv), "faces": wf.reshape(-1).tolist()},
                "check": {
                    k: getattr(r, k)
                    for k in (
                        "triangles",
                        "zero_area",
                        "duplicates",
                        "open_edges",
                        "shared_edges",
                        "flipped_edges",
                        "parts",
                        "inside_out",
                        "tiny_parts",
                        "holes",
                        "overhang_faces",
                    )
                }
                | {
                    "volume": r6(r.volume),
                    "area": r6(r.area),
                    "overhang_area": r6(r.overhang_area),
                },
                "notes": meshcheck.notes(r),
                "repair": {
                    k: getattr(x, k)
                    for k in (
                        "zero_area",
                        "duplicates",
                        "turned",
                        "holes_filled",
                        "holes_left",
                        "tiny_removed",
                    )
                }
                | {
                    "triangles": len(x.faces),
                    "vertices": len(x.vertices),
                    "note": meshcheck.repaired_note(x, after),
                    "after_volume": r6(after.volume),
                    "after_closed": after.closed,
                    "after_problems": after.problems,
                },
            }
        )

    for name, m in [
        ("rotate x 90", arrange.rotate("x", 90)),
        ("rotate y -90 about a point", arrange.rotate("y", -90, (1, 2, 3))),
        ("rotate z 30", arrange.rotate("z", 30)),
        ("rotate z 450", arrange.rotate("z", 450)),
        ("scale", arrange.scale(2, 0.5, 3, (1, 1, 1))),
        ("mirror y", arrange.mirror("y", (0, 4, 0))),
        ("lay flat +x", arrange.lay_flat((1, 0, 0), (0, 0, 5))),
        ("lay flat up", arrange.lay_flat((0, 0, 1))),
        ("lay flat down", arrange.lay_flat((0, 0, -1))),
        ("lay flat slanted", arrange.lay_flat((0.3, -0.4, 0.5))),
        ("multiply", arrange.multiply(arrange.translate(1, 2, 3), arrange.rotate("x", 90))),
    ]:
        cases["arrange"].append(
            {
                "name": name,
                "matrix": [[r6(x) for x in row] for row in m],
                "det": r6(arrange.determinant3(m)),
                "normal": [r6(x) for x in arrange.apply_normal(m, (0.0, 0.6, 0.8))],
            }
        )

    flat = soup(cube)
    v, f = indexed(flat)
    for name, m in [
        ("tip over", arrange.rotate("x", 90, arrange.centre_of(v))),
        ("lay on side", arrange.lay_flat((1, 0, 0), arrange.centre_of(v))),
        ("grow", arrange.scale(2, 2, 2, arrange.centre_of(v))),
        ("mirror", arrange.mirror("x", arrange.centre_of(v))),
    ]:
        s = arrange.settle(v, m)
        nv, nf = arrange.apply(v, f, s)
        lo, hi = arrange.bounds(nv)
        cases["settle"].append(
            {
                "name": name,
                "soup": flat,
                "matrix": [[r6(x) for x in row] for row in m],
                "settled": [[r6(x) for x in row] for row in s],
                "lo": [r6(x) for x in lo],
                "hi": [r6(x) for x in hi],
                "faces": meshcheck.weld(nv, nf)[1].reshape(-1).tolist(),
                "volume": r6(meshcheck.check(nv, nf).volume),
            }
        )
    cases["centred"] = [[r6(x) for x in row] for row in arrange.centred(v + [30, -12, 4])]
    cases["centred_soup"] = soup(box(25, -17, 4, 35, -7, 14))
    for lo, hi, bed in [
        ((-50, -50, 0), (50, 50, 100), (220, 220, 250)),
        ((-150, -5, 0), (150, 5, 260), (256, 256, 256)),
        ((-110, -110.25, 0), (110, 110.25, 250), (220, 220, 250)),
        ((80, -10, 0), (140, 10, 30), (220, 220, 250)),
        ((-30, -125, 0), (30, -85, 10), (220, 220, 250)),
    ]:
        size = [b - a for a, b in zip(lo, hi, strict=True)]
        cases["fits"].append(
            {
                "lo": lo,
                "hi": hi,
                "bed": bed,
                "over": [r6(x) for x in arrange.fits(size, bed)],
                "note": arrange.fit_note(lo, hi, bed),
            }
        )
    for vol, area, mat, infill in [
        (1000.0, 600.0, "PLA", 0.15),
        (125000.0, 15000.0, "PETG", 0.2),
        (8000.0, 2400.0, "TPU", 1.0),
        (50.0, 300.0, "ABS", 0.15),
    ]:
        e = filament.estimate(vol, area, mat, infill)
        cases["filament"].append(
            {
                "volume": vol,
                "area": area,
                "material": mat,
                "infill": infill,
                "estimate": {k: r6(x) for k, x in e.items()},
                "rows": filament.rows(vol, area, mat, infill),
            }
        )
    cases["beds"] = [list(b) for b in arrange.BEDS]
    OUT.write_text(
        json.dumps(cases, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
