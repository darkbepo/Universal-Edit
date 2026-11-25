# 3D viewer: add a logo (both apps) — design

Date: 2026-11-25. Roadmap item 10.8 ("Next: basic editing for 3D printing"), first editing step.

## Why

People who print in 3D often want a logo on a part: a name badge, a keychain, a brand mark on
a box lid. Today that means a CAD program. The 3D viewer already opens every model format we
convert; this lets it turn a picture (PNG with a transparent background, or any picture with a
plain background) into a raised 3D logo, on its own or placed on a model, and save the result
for the slicer.

## Owner's choices (25 Nov 2026)

- **Placement:** both a stand-alone logo (optional base plate) and a logo **raised** on a flat
  face of an existing model. **Engraving** (cutting into the model) is a later step.
- **Colours:** the logo becomes **one silhouette** (one solid shape); colours are not kept.

## Goals

- **Where:** the 3D viewer's top bar gets **"Add logo…"** (with or without a model open) and
  **"Save as…"**. The hub's 3D tile is unchanged (opens the empty viewer; "Add logo…" works there,
  giving a stand-alone logo).
- **Logo window** (a side panel next to the view, like the info panel):
  - Picture: "Choose picture…" (PNG, JPG, WebP, BMP, GIF, SVG — SVG is drawn to pixels first);
    a small preview of the silhouette as it will be extruded.
  - Shape from: *transparency* (default when the picture has an alpha channel) or *dark parts* /
    *light parts* (brightness threshold, slider 1–254, default 128); "Invert".
  - Detail: Smooth / Normal / Fine (simplification tolerance); tiny specks (< 0.5 % of the area,
    or below the printable 0.4 mm) are dropped, the count is shown ("3 tiny bits left out").
  - Size: width in mm (height follows the aspect ratio; a lock to type both), thickness in mm
    (default 2 mm; 0.2 – 50).
  - Stand-alone only — base plate: none / rectangle / rounded rectangle, margin (mm), plate
    thickness (mm), optional hanging hole (keychain) in a corner.
  - On a model: "Click a flat face to place the logo"; then drag on that face to move it,
    rotation (−180…180°, snaps to 15° with Shift), "Sink into surface" (default 0.2 mm, so the
    slicer merges the logo with the part).
  - Buttons: Apply (the logo becomes a part of the model, one undo step), Cancel.
- **Undo / redo** (Ctrl+Z / Ctrl+Y) for adding, moving and removing logos; a "Remove logo" in
  the info panel's parts list.
- **Saving:** "Save as…" writes STL, 3MF, OBJ, PLY or GLB through the model converter's writers
  (mm), default name "<name> with logo.<ext>", 3MF first. Unsaved changes ask before leaving.
- **Warnings in plain words:** logo hangs over the edge of the flat face; walls thinner than
  0.4 mm; picture has no clear shape (all one colour).
- Same model, numbers and shortcuts in both apps; shared test cases in both test suites.

## Non-goals (this step)

- Engraving / boolean cuts (next step: manifold3d on the desktop, manifold-3d wasm on the web,
  both Apache-2.0), logos bent around curved surfaces, several colours as separate parts, text
  typed directly (later: text → outline through the fonts we already ship), moving or editing
  the original model's own geometry.

## Model (`umc/editor3d/logo.py` ↔ `web/src/editor3d/logo.ts`, line-by-line ports, no Qt / DOM)

1. `mask(pixels, w, h, mode, threshold, invert)` → 0/1 grid (picture decoded by each app's own
   engine: Pillow / canvas; scaled so the longer side is at most 1024 px first).
2. `trace(mask)` → closed loops along the pixel edges (own code, so both apps produce identical
   points; a right-turn rule keeps pixels that touch only diagonally apart, and such corners are
   moved 0.05 px apart so no zero-width edge is shared), keeping the corners where the outline
   turns, then Douglas–Peucker with the detail tolerance (a staircase becomes a slope, square
   corners stay square).
3. `polygons(loops)` → outer rings with their holes (nesting by even–odd containment, winding
   fixed: outer counter-clockwise, holes clockwise); specks dropped.
4. `extrude(polygons, width_mm, thickness)` → a closed, watertight mesh: caps triangulated with
   **earcut** (desktop `mapbox_earcut`, web `earcut` — both ISC, the same algorithm), side walls
   as quads per edge. Base plate / hole = more polygons in the same mesh (the logo stands on the
   plate's top; the hole is cut out of the plate polygon before triangulating).
5. `flat_face(model, triangle)` → the connected coplanar triangles around the clicked one
   (normals within 1°, plane distance < 0.01 mm) and their outline; `placement(point, normal,
   rotation, sink)` → the matrix putting the logo onto that plane (its "out" = the face normal;
   on a level face its X axis = world X, otherwise level along the face so the logo stands
   upright), and `overhang(footprint, face)` for the warning.
- Shared cases `tests/fixtures/logo3d_cases.json` (made by `python scripts/make_logo3d_cases.py`,
  on purpose only): small masks → loop points, polygon nesting, triangle counts, volumes,
  placement matrices. Triangle order may differ between the two earcut packages, so cases compare
  areas / volumes / bounds, not indices.
- Desktop dependency: `mapbox_earcut` (ISC, tiny wheel) — checked by a `--self-test` step.
  Web: `earcut` (ISC), three.js does the rest of the drawing.

## Pages

- Desktop `gui/editor3d/viewer_page.py` + a new `logo_panel.py`; the logo preview is drawn by
  the same GL view as a second, highlighted mesh; picking = a ray from the camera through the
  click against the triangles (numpy, `editor3d/pick.py` ↔ `pick.ts`). Heavy steps (tracing,
  triangulating) on the viewer's loading thread, re-run 250 ms after the last setting change.
- Web `ui/editor3d/page.ts` + `logopanel.ts`; tracing and extruding on the page (small numbers;
  a 1024 px mask traces in well under a second), saving through the worker's model writers.
- Test hooks: `.logo-panel`, `#logo-width`, `#logo-thickness`, `window.__viewer3d.logo` with
  `?e2e`; e2e `e2e/logo3d.spec.ts`; desktop GUI tests in `tests/test_logo3d_page.py`.

## Steps (one at a time, ship gate each)

1. ✅ Model in both apps (`logo`, `pick`) with the shared cases.
2. Desktop: logo panel, placing, undo, Save as; README table rows; self-test step.
3. Website: the same page features; e2e.
4. Later: engraving (boolean cut), then text logos.
