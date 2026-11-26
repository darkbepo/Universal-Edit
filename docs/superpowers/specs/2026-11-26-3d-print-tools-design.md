# 3D viewer: print tools (both apps) — design

Date: 2026-11-26. Roadmap item 10.8, after the logos. Owner: "it's designed for 3D printers
first" — the ideas list's groups 1 and 2, plus the filament estimate.

## Why

Before slicing, people often need a model turned, scaled, laid on a face, checked for holes
or moved onto their printer's bed. Today that means opening a slicer or a mesh repair tool.
These are small, common jobs; the viewer already loads every model format we convert.

## Goals

### Arrange (a side panel, like the logo panel; "Arrange" in the top bar)
- **Move** X / Y (mm, typed or with arrows); the model always stays standing on the bed
  (lowest point Z = 0), like slicers keep it. **Centre** puts it in the middle of the bed.
- **Rotate** about X / Y / Z: ±90° buttons and a typed angle. **Lay flat**: click a face of the
  model; it's turned so that face lies on the bed (`pick.flat_face` finds the face).
- **Scale**: one percentage (proportions kept) or per axis; or type a target size in mm for
  one axis (the others follow while the lock is on). **Mirror** X / Y / Z.
- **Units fix**: "Made in inches" (×25.4), "Made in cm" (×10), and back (÷25.4, ÷10).
- Every change is one undo step (the same `History` as the logos). Logos on the model turn
  and scale with it. Save as… writes the arranged model.

### Printer bed
- Pick a printer (`BEDS`: Prusa MK4 / MINI, Bambu Lab A1 / P1 / X1 and A1 mini, Creality
  Ender-3 / K1, Elegoo Neptune 4, Anycubic Kobra 2) or a custom width × depth × height.
  Remembered (desktop QSettings `viewer3d/bed`, web localStorage `umc-bed`).
- Drawn as an outline box in the view (the grid stays). The info panel says whether the model
  fits, and by how much it's too big ("12.5 mm too wide for the bed").

### Check & repair ("Check" panel)
- **Check** (runs when a model opens or changes, off the UI thread): holes (open edges),
  edges shared by more than two triangles, triangles facing the wrong way (inconsistent or
  inside-out), zero-area and duplicate triangles, separate parts and tiny loose bits, each in
  plain words with what it means for printing. Overhangs: faces steeper than the chosen angle
  (default 45°) that will need supports, shown in red on the model (except faces on the bed),
  with their area.
- **Repair** (one undo step): remove zero-area and duplicate triangles, make every part face
  outwards consistently, close holes (each hole's edge loop is filled flat — earcut on the loop
  projected onto its best-fit plane), remove tiny loose bits (< 1 mm³ and < 1 % of the model).
  Afterwards the check runs again and says what was done.
- What isn't covered: walls too thin to print, self-intersecting surfaces (reported as "can't
  check" non-goals for now).

### Filament estimate (info panel)
- Material (PLA, PETG, ABS, ASA, TPU, Nylon — densities in `filament.DENSITY`) and infill
  (default 15 %): grams and metres of 1.75 mm filament, as a rough estimate (outer walls
  `SHELL_MM` 0.8 mm solid, the inside at the infill), plus "solid" grams. Only for watertight
  models (else no volume).

## Model (Qt / DOM free, line-by-line ports, shared cases)

- `editor3d/arrange.py` ↔ `arrange.ts`: 4×4 matrices (rotate about the model's centre, scale,
  mirror, `lay_flat(normal)` = Rodrigues rotation taking the normal to −Z), `apply` (reverses
  the triangle winding when the matrix mirrors), `settle` (drop to the bed keeping the X / Y
  centre), `BEDS`, `fits(size, bed)`.
- `editor3d/meshcheck.py` ↔ `meshcheck.ts`: `weld` (corners with the same position become one
  vertex, numbered in first-appearance order in both apps), `check` → a report (counts + the
  overhang faces), `repair` → new vertices / faces + what was done. Same steps in the same
  order in both apps; earcut fills the holes (triangle counts match, orders may not).
- `editor3d/filament.py` ↔ `filament.ts`: `estimate(volume, area, material, infill)`.
- `scene.Scene.arranged(matrix)` / `with_base(model)` (repair): the logos' meshes, points and
  normals are transformed too.
- Shared cases `tests/fixtures/print3d_cases.json` (`python scripts/make_print3d_cases.py`, on
  purpose only).

## Steps (one at a time, ship gate each)

1. Model in both apps with the shared cases.
2. Desktop: Arrange + Check panels, bed box, overhang colours, lay-flat clicks, filament rows;
   README rows.
3. Website: the same; e2e.
4. Review, fixes, land on main.
