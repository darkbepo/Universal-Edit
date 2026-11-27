# 3D logos on curved surfaces (both apps) — design

Date: 2026-11-27. Follows `2026-11-25-3d-logo-design.md` (roadmap 10.8, "Logos").

## Why

The owner put a Google logo on a skull (STL, ~890 000 triangles) on the live website. The logo
came out as a flat slab on the tangent plane at the click point: it floats off the skull on one
side and sinks into it on the other. Today `placement` / `placed_logo` push the traced shape
straight out along the clicked triangle's normal with a 0.2 mm `sink`, and `pick.flat_face`
assumes the face is flat. The owner's words: the logo should "merge into the skull", there
should be a "choice to keep it flat or warp it to wrap around the surface", and it should
"become part of the model".

## What the user gets

In the logo panel ("On the model" only), a new row **Fit to surface**:

- **Flat** (default, close to today): the top stays one flat plane, but the **bottom follows
  the surface**: every bottom point goes down along −normal until it meets the model, so the
  logo sits flush, with no gap under one side and nothing poking out through the far side.
  This suits embossed signs on gently curved parts.
- **Wrap**: works like a decal. The whole logo is laid onto the surface and raised
  `thickness` along the surface's own direction at each point, so it bends with the skull.

**Sink into surface** stays (both modes go `sink` below the surface so slicers join them).

On a curved model:

- **The direction comes from the area, not one triangle.** The logo's "out" direction is the
  area-weighted average normal of the model triangles under the logo (within its radius of
  the click point), not the one triangle that was clicked. One noisy triangle on a skull
  would otherwise tilt the logo.
- **Dragging follows the surface.** Each move casts the pointer ray at the model (`pick.hit`)
  and takes the new point and direction from there. It no longer slides over the first
  face's plane. A flat face behaves exactly as it does today.
- **Plain-word warnings** replace "hangs over the edge of the flat face":
  - "N points of the logo miss the model": a projection ray found no surface. Wrap refuses
    to build when it misses; Flat builds and warns.
  - "The surface turns away under the logo; try Flat or a smaller logo" (Wrap only): the
    surface under part of the logo faces away from the logo's direction by more than 75°.
    That would fold the logo.

At **Save as…**, a new tick box: **Merge into one solid** (on by default when the scene has
face logos). It runs a real boolean union, so the STL / 3MF holds one watertight body. If
the union fails (the model isn't closed, or the engine errors), the file is saved as today
(everything in one object, overlapping) and the save note says so in plain words.

Optional, if the owner wants it, since it's cheap once Wrap exists: **Style: Raised /
Engraved**. Engraved cuts the logo `thickness` deep into the model (a boolean difference).
It always needs the boolean step, so it only works on closed models (a plain-word note
otherwise). The preview shows the cut part in the accent colour.

## Model (line-by-line ports: `logo.py` ↔ `logo.ts`, `pick.py` ↔ `pick.ts`, `scene.py` ↔ `scene.ts`)

1. **Surface sampler** (`pick.surface` / `surface`, new). Every projection ray of one logo
   runs parallel, along −n (n = the logo's direction). That turns ray casting into finding
   which triangle a point lands in, in 2D, in the logo's plane:
   - Keep only model triangles whose projection overlaps the logo's footprint, plus a margin.
     One vectorised pass over all triangles (numpy / a plain Float32 loop), so no BVH or
     rtree / embree dependency is needed. Large models pay this once per placement, not once
     per ray.
   - Put those triangles into a uniform 2D grid with about 4 triangles per cell.
   - `sample(xy)` returns, for each point, the hit height along n, the triangle's normal and
     a hit flag. Among the hits on one line it takes the front-facing hit (normal · n > 0)
     nearest to the click plane. That way an eye socket or the back of the skull never wins.
   - Same rules and tolerances in both apps. Shared cases compare heights to 1e-9.
   - Speed target: placing on the 890k-triangle skull in under 150 ms on the desktop and
     under 300 ms in the browser. During a drag the preview is built in Flat-without-fit
     form (today's code, instant); the fitted logo follows 120 ms after the pointer stops
     and on release.
2. **Cap refinement** (`logo.refine(points, tris, max_edge)` / `refine`, new). This splits
   every edge longer than `max_edge` at its midpoint and shares the new point between the two
   triangles on that edge (red–green: a triangle with 1 / 2 / 3 split edges becomes
   2 / 3 / 4). It repeats until no edge is too long. The caps stay conforming, so the mesh
   stays closed. Outline edges split too: walls are rebuilt from the refined cap's boundary
   edges (edges used by one triangle), one quad per edge. `max_edge` = clamp(logo width / 60,
   0.3 mm, 2 mm), so a 40 mm logo gets about 0.67 mm.
3. **`fitted_logo(shape, thickness, point, normal, rotation, sink, fit, surface)`** (new;
   `placed_logo` stays for badges and the drag preview):
   - **Flat**: bottom z_i = h_i − sink (h_i = the surface height under each refined bottom
     point), top = max(h) + thickness. Walls run from bottom to top.
   - **Wrap**: bottom b_i = the surface point − n_i·sink. Top t_i = the surface point +
     n_i·thickness, where n_i = the normal at that point of the logo's own projected bottom
     surface (area-weighted over its refined triangles, so it is smooth even on a faceted
     model). Walls run between the two.
   - Returns the mesh plus `missed` (points with no hit) and `folded` (bottom triangles
     facing away by more than 75°).
4. **Scene**: `LogoSettings.fit = "flat" | "wrap"` (default "flat"); `Logo.overhang` now counts
   the missed points (the name is kept), and `Logo.folded` is new. `overlap` is still area × sink, so the info
   volume stays right. `arranged` scales/turns fitted logos as it does today. A mirror makes
   them again: the logo is rebuilt from `point` / `normal` against the moved model.
   Undo / History and saved-file formats are unchanged.
5. **Merge** (`scene.export(…, merge=True)` / the worker side on the web):
   - Desktop: `manifold3d` (Apache-2.0, ~1 MB wheel for Windows / Linux, pinned; goes into
     the "model" add-on's `site/` like onnxruntime for the image editor). The model and each
     logo become one `Manifold` each, joined by a union (a difference for Engraved). Runs
     on the save thread with progress.
   - Web: `manifold-3d` (npm, Apache-2.0, wasm ~2.8 MB unpacked), loaded lazily inside the
     job worker's `model3d` converter only when merging, behind a new option
     `merge_solid`.
   - Apache-2.0 is compatible with AGPL-3.0-or-later (CLAUDE.md Licensing).
   - If the input isn't manifold or the result is empty or not closed, the export falls
     back to today's single-object save and returns a note.

## Tests

- **Shared cases** (`tests/fixtures/logo3d_cases.json`, regenerated on purpose with
  `python scripts/make_logo3d_cases.py`): new groups "refine" (triangle / edge counts,
  closedness, longest edge ≤ max), "surface" (a UV sphere r = 30 mm and a box: heights at
  fixed points) and "fit" (Flat / Wrap on the sphere: volume, closedness, missed / folded
  counts). Checked by `tests/test_editor3d*.py` and `web/tests/logo3d.test.ts`.
- **Done check** (both apps): a 30 mm logo on the r = 30 mm sphere in both modes. No bottom
  vertex is more than 0.05 mm (+ sink) off the sphere's surface. The mesh is closed (edges
  counted by hand, not by trimesh's `is_watertight`). Wrap's top is `thickness` ± 0.05 mm
  from the sphere.
- **Merge**: sphere + logo → union → one body, closed, volume > sphere's. An open model falls
  back to the plain save with a note. Desktop in `tests/test_gui_logo3d.py` / scene tests;
  web vitest on the worker function (manifold-3d runs in node) + `web/e2e/logo3d.spec.ts`
  (choose Wrap, place on a sphere STL built in the test, save STL with Merge, re-read it).
- **Speed**: a 1M-triangle sphere placement under 1 s in CI on both apps (loose, against
  flakes).
- Self-test step "3D logo" also places a Wrap logo and merges it (proves manifold3d loads
  in the build).

## Steps (one at a time, ship gate each)

1. Model in both apps: `surface`, `refine`, `fitted_logo`, settings, shared cases.
2. Desktop page: Fit row, drag along the surface, warnings, Merge tick box, add-on site
   package, self-test.
3. Website page: the same, plus `manifold-3d` in the worker, e2e.
4. (If the owner says yes) Engraved style in both apps.

## Non-goals

- Bending text along a path, or logos larger than the surface they wrap (Wrap refuses).
- Smoothing the model's own triangles.
- Keeping colours.
- Repairing a non-closed model so Merge works (the Check panel's Repair already exists; the
  note points to it).
