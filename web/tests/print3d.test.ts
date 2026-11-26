// 3D print tools (mirrors tests/test_print3d.py): the shared cases made by the desktop's
// scripts/make_print3d_cases.py.
import { describe, expect, it } from "vitest";
import data from "../../tests/fixtures/print3d_cases.json";
import { applySoup, BEDS, bounds, centred, determinant3, fitNote, fits, layFlat, mirror, multiply, rotate, scale, settle, translate, applyNormal, type Matrix } from "../src/editor3d/arrange";
import type { Vec3 } from "../src/editor3d/camera";
import { estimate, rows } from "../src/editor3d/filament";
import { check, hasProblems, isClosed, notes, repair, repairedNote, soup, weld } from "../src/editor3d/meshcheck";

const close = (a: number, b: number, tol = 1e-6) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(b)));
const closeAll = (a: number[], b: number[], tol = 1e-6) => {
  expect(a.length).toBe(b.length);
  a.forEach((v, i) => close(v, b[i], tol));
};

describe("check & repair: shared cases", () => {
  for (const c of data.models) {
    it(c.name, () => {
      const pos = Float32Array.from(c.soup);
      const m = weld(pos);
      expect(m.vertices.length / 3).toBe(c.welded.vertices);
      expect(m.faces.flat()).toEqual(c.welded.faces);
      const r = check(m);
      for (const k of ["triangles", "zero_area", "duplicates", "open_edges", "shared_edges", "flipped_edges", "parts", "inside_out", "tiny_parts", "holes"] as const)
        expect(r[k], k).toBe(c.check[k]);
      expect(r.overhang_faces).toEqual(c.check.overhang_faces);
      close(r.volume, c.check.volume);
      close(r.area, c.check.area);
      close(r.overhang_area, c.check.overhang_area);
      expect(notes(r)).toEqual(c.notes);
      const x = repair(m);
      for (const k of ["zero_area", "duplicates", "turned", "holes_filled", "holes_left", "tiny_removed"] as const) expect(x[k], k).toBe(c.repair[k]);
      expect(x.faces.length).toBe(c.repair.triangles);
      expect(x.vertices.length / 3).toBe(c.repair.vertices);
      const after = check(x);
      expect(repairedNote(x, after)).toBe(c.repair.note);
      close(after.volume, c.repair.after_volume);
      expect(isClosed(after)).toBe(c.repair.after_closed);
      expect(hasProblems(after)).toBe(c.repair.after_problems);
      // and back to a soup: the same triangles
      expect(soup(x).length).toBe(x.faces.length * 9);
    });
  }
});

describe("arrange: shared cases", () => {
  const build: Record<string, () => Matrix> = {
    "rotate x 90": () => rotate("x", 90),
    "rotate y -90 about a point": () => rotate("y", -90, [1, 2, 3]),
    "rotate z 30": () => rotate("z", 30),
    "rotate z 450": () => rotate("z", 450),
    scale: () => scale(2, 0.5, 3, [1, 1, 1]),
    "mirror y": () => mirror("y", [0, 4, 0]),
    "lay flat +x": () => layFlat([1, 0, 0], [0, 0, 5]),
    "lay flat up": () => layFlat([0, 0, 1]),
    "lay flat down": () => layFlat([0, 0, -1]),
    "lay flat slanted": () => layFlat([0.3, -0.4, 0.5]),
    multiply: () => multiply(translate(1, 2, 3), rotate("x", 90)),
  };
  it("matrices", () => {
    for (const c of data.arrange) {
      const m = build[c.name]();
      closeAll(m.flat(), c.matrix.flat());
      close(determinant3(m), c.det);
      closeAll(applyNormal(m, [0, 0.6, 0.8]), c.normal);
    }
  });

  it("settle keeps the model standing in place", () => {
    const pos = Float32Array.from(data.settle[0].soup);
    const [lo, hi] = bounds(pos);
    const centre: Vec3 = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    const ms: Record<string, Matrix> = {
      "tip over": rotate("x", 90, centre),
      "lay on side": layFlat([1, 0, 0], centre),
      grow: scale(2, 2, 2, centre),
      mirror: mirror("x", centre),
    };
    for (const c of data.settle) {
      const s = settle(pos, ms[c.name]);
      closeAll(s.flat(), c.settled.flat());
      const out = applySoup(pos, s);
      const [nlo, nhi] = bounds(out);
      closeAll(nlo, c.lo, 1e-5);
      closeAll(nhi, c.hi, 1e-5);
      const w = weld(out);
      expect(w.faces.flat()).toEqual(c.faces);
      close(check(w).volume, c.volume, 1e-5);
    }
  });

  it("centred, fits, beds", () => {
    closeAll(centred(Float32Array.from(data.centred_soup)).flat(), data.centred.flat());
    for (const c of data.fits) {
      const size = c.hi.map((h, i) => h - c.lo[i]) as Vec3;
      closeAll(fits(size, c.bed as Vec3), c.over);
      expect(fitNote(c.lo as Vec3, c.hi as Vec3, c.bed as Vec3)).toBe(c.note);
    }
    expect(BEDS).toEqual(data.beds);
  });
});

describe("filament: shared cases", () => {
  it("estimates and rows", () => {
    for (const c of data.filament) {
      const e = estimate(c.volume, c.area, c.material, c.infill);
      close(e.grams, c.estimate.grams);
      close(e.metres, c.estimate.metres);
      close(e.solid_grams, c.estimate.solid_grams);
      expect(rows(c.volume, c.area, c.material, c.infill)).toEqual(c.rows);
    }
  });
});
