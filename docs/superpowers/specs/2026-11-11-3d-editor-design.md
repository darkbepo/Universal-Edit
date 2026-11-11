# 3D editor (both apps) — design

Date: 2026-11-11 · Roadmap item 10.8 · Status: **step 0 (viewer) implemented**; steps 1–5 agreed with the owner (2026-11-11).

## Goal

When 3D files are added, **Convert | Edit** opens them in a 3D editor. The editor is meant for
the small fixes people make **before 3D printing**, not for modelling: get the size and
orientation right, fix a broken mesh, place the parts on the bed, cut a model that is too big,
then save it as STL / 3MF / OBJ / PLY / GLB. Everything lands in both apps, one step at a time.

## Step 0 — viewer (done)

- Edit is enabled when every file is a 3D model (STL, OBJ, 3MF, PLY, GLB, OFF, STEP, IGES; no OFF
  on the web). Several files: a drop-down picks which one is shown.
- Rotate (drag), pan (right-drag / Shift+drag), zoom (wheel), fit (double-click / Fit), preset
  views 3D / Front / Top / Side, Shaded / Edges / Wireframe, print-bed grid (red = X, green = Y).
- Info panel: width / depth / height in mm, triangles, parts, watertight, volume.
- Desktop: `editor3d/` (Qt-free model + camera) and `gui/editor3d/` (QOpenGLWidget, no new
  dependency). Web: `src/editor3d/` (ports) and `src/ui/editor3d/page.ts` (three.js), models
  read in the worker by the hidden `model3d-view` converter.

## Proposed steps (each: both apps, tests, ship gate, then stop)

### 1. Transform + save (the core of "adjust before printing")
- Side panel "Transform": **Move** X/Y/Z (mm), **Rotate** X/Y/Z (°, plus ±90° buttons),
  **Scale** in % or to an exact size in mm (lock proportions on by default), **Mirror** X/Y/Z.
- **Lay flat**: click a face and the model turns so that face lies on the bed.
- **Units fix**: one-click "Model was made in inches (×25.4)" / "in cm (×10)" / "in m (×1000)".
- Always kept on the bed (lowest point at Z = 0), shown centred.
- Undo / redo (Ctrl+Z / Ctrl+Y). Transforms are stored as one 4×4 matrix per part, so undo is
  cheap; the vertices are only changed ("baked") when saving.
- **Save** (back to the same format where we can write it) and **Save as…** STL / 3MF / OBJ /
  PLY / GLB through the existing converter writers (`save_model` / `writeParts`), so every
  format option keeps working. STEP / IGES open fine but are saved as a mesh format.
- "Unsaved changes?" on leaving, like the other editors.

### 2. Check & repair for printing
- Show problems in the view: open edges (holes) in red, flipped triangles tinted.
- **Repair** button: merge duplicate points, remove broken / duplicate triangles, fix normals,
  fill small holes — the same steps as the converter's "Repair mesh", with a before / after
  summary ("filled 3 holes, removed 12 broken triangles").
- **Remove small loose pieces** (below a size you choose).

### 3. Parts and the print bed
- Parts list (from multi-part 3MF / GLB / STEP, or **Split into parts** = separate bodies):
  select (click in the view or the list), show / hide, rename, delete, duplicate, merge.
- **Printer bed** preset (e.g. 220 × 220, 250 × 210, 256 × 256 mm, custom) drawn as an outline;
  a warning when the model doesn't fit; **Arrange** places all parts on the bed with a gap.
- Transform (step 1) works on the selected parts.

### 4. Cut
- **Cut** with a flat plane: at a height (Z), or along X / Y, previewed live; keep the top,
  the bottom or both (as two parts), with the cut face closed so both halves stay printable.
- Optional: add simple alignment pins / holes to the cut faces (later).

### 5. Simplify and measure
- **Reduce triangles** (target % or count) for huge scans that slicers choke on.
- **Measure**: click two points for the distance in mm; a section view (slice at a height).

## Architecture (step 1 onward)

- Model: `editor3d/document.py` / `web/src/editor3d/document.ts` — `ModelDoc` (parts: name,
  vertices, faces, matrix, visible), `History` of commands (matrix commands store before/after
  matrices; geometry commands store the changed arrays), dirty flag. Line-by-line ports with the
  same test cases in both suites, like the image editor.
- Picking: desktop = numpy ray–triangle test (Möller–Trumbore) against the shown triangles;
  web = three.js `Raycaster`.
- Saving: desktop bakes the matrices into a trimesh Scene and calls `save_model`; the web page
  bakes into a GLB (`writeGlb`, exported from `converters/model3d.ts`) and runs the normal model
  converter in the worker with "Centre on the print bed" and "Repair" off.
- Self-test step per new library; README "What works where" rows and a roadmap line per step.

## Decisions (owner, 2026-11-11)

| Question | Decision |
|---|---|
| Order of the steps | As listed: transform → repair → parts / bed → cut → simplify / measure. |
| Printer bed | The owner doesn't know their printer: default to a generic 220 × 220 × 250 mm bed, with presets for common printers and a custom size; the choice is remembered. |
| Extra libraries | Approved for steps 4–5: desktop `manifold3d` (Apache-2.0), `fast-simplification` (MIT); web `manifold-3d` (Apache-2.0), `meshoptimizer` (MIT). |
| Moving / rotating / scaling | **Drag handles in the view from the start** (move arrows + bed-plane drag, rotation rings, scale boxes), with the number fields in the side panel as well for exact values. |
