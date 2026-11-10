# Image Editor (website) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The website gets the desktop editor's Convert | Edit switch and layered image editor, running fully in the browser.

**Architecture:** A DOM-free model in `web/src/editor/` ported from `src/umc/editor/` (RGBA `Uint8ClampedArray` layers, patch-based history with unique tokens, tools returning commands). Shapes and text are rasterised by Canvas 2D behind a small injectable `Rasterizer` so node tests can stub it. The page lives in `web/src/ui/editor/`; the conversion worker is used only to decode unusual formats and to encode non-`.ora` saves.

**Tech Stack:** TypeScript, Vite, vitest (node), Playwright (Chromium), fflate (already a dependency). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-11-web-image-editor-design.md` (desktop reference: `docs/superpowers/specs/2026-11-10-image-editor-design.md` and the code in `src/umc/editor/`).

## Global Constraints

- No new npm dependencies. Everything editor-related is imported dynamically from `ui/app.ts` (`await import("./editor/page")`) so the converter page bundle stays ~100 KB.
- `web/src/editor/` must not touch `document` / `window`; Canvas use only through `Rasterizer` / `PngCodec` interfaces (`editor/raster.ts`).
- Limits: 16 000 px per side, 250 megapixels, message `"This image is too large to edit (max 16 000 px per side)."`; undo cap 500 MB.
- Option names, ranges and defaults copied exactly from the desktop tools (`src/umc/editor/tools/*.py`).
- Layer objects are replaced, never mutated, except pixel edits recorded as patches.
- Commits: in the `image-editor` worktree, author `darkbepo`, imperative subject, no trailers; `git add` only listed paths. Land on main per CLAUDE.md "Start here".
- Verification per task: `npx tsc --noEmit` and `npm test`; Tasks 7–9 also `npm run e2e -- e2e/editor.spec.ts`.

## Review Focus

1. **Pointer input:** pen and touch events, and two-finger pan/zoom must not paint — e2e `touch two-finger does not paint` (Task 8).
2. **Browser shortcuts:** Ctrl+S / Ctrl+A / Delete must not trigger the browser's own actions or fire while typing in inputs — e2e `ctrl+s opens save dialog, typing in name field doesn't switch tools` (Task 8).
3. **Formats the browser can't decode** (HEIC/TIFF/PSD/RAW) must route through the worker — e2e `psd opens via the worker` (Task 7).
4. **Unsaved work on tab close** — `beforeunload` set exactly while dirty — e2e `beforeunload only while dirty` (Task 9).
5. **Large images** — composite repaint by dirty rect only, and no whole-layer copies per stroke — vitest `stroke does not copy whole layer` (Task 3).

---

## File structure

```
web/src/editor/geometry.ts     Rect (x1/y1 exclusive), fromPoints, around, union, intersect, offset, contains
web/src/editor/layer.ts        Layer type, makeLayer(), copyLayer(), crop/paste helpers on RGBA arrays
web/src/editor/document.ts     EditorDoc, newDoc(), checkSize(), composite(doc, rect?, override?)
web/src/editor/history.ts      Command, PatchCommand(+fromRegion), LayerListCommand, DocStateCommand,
                               SelectionCommand, History (tokens, 500 MB cap)
web/src/editor/raster.ts       interfaces Rasterizer, PngCodec; canvasRasterizer / canvasPng (browser)
web/src/editor/tools/base.ts   ToolContext, Tool, clipRect, blendOver
web/src/editor/tools/paint.ts  BrushTool, EraserTool (tiles)
web/src/editor/tools/fill.ts   FillTool, PickerTool, floodMask, similar
web/src/editor/tools/shapes.ts LineTool, RectTool, EllipseTool (via Rasterizer)
web/src/editor/tools/select.ts SelectTool, CropTool, selectAll, deselect, clearSelection
web/src/editor/tools/text.ts   TextTool, addTextLayer (via Rasterizer)
web/src/editor/tools/index.ts  TOOLS in tool-bar order
web/src/editor/ops.ts          crop, resizeImage, canvasSize, rotate, flip, brightnessContrast,
                               applyBrightnessContrast, add/duplicate/delete/merge/move/rename/opacity/visible
web/src/editor/ora.ts          readOra(bytes, png), writeOra(doc, png)
web/src/editor/open.ts         openFiles(files, decode) → { doc, errors }
web/src/ui/editor/canvas.ts    EditorCanvas (zoom/pan, pointer → tool, dirty repaint, overlays)
web/src/ui/editor/panels.ts    ToolBar, Swatches, ToolSettings, LayersPanel
web/src/ui/editor/dialogs.ts   resize / canvas size / brightness / save-as dialogs (<dialog>)
web/src/ui/editor/page.ts      EditorPage: layout, shortcuts, open/save, leave/beforeunload
Modify: web/src/ui/app.ts (switch + mode), web/src/styles.css (editor section)
Tests: web/tests/editor-model.test.ts, editor-tools.test.ts, editor-ops.test.ts, editor-ora.test.ts,
       web/e2e/editor.spec.ts
```

---

### Task 1: Geometry, layers, document, composite, history

**Files:** create `geometry.ts`, `layer.ts`, `document.ts`, `history.ts`; test `tests/editor-model.test.ts`.

**Interfaces (produced):**
- `class Rect { constructor(x0,y0,x1,y1); width; height; empty; static fromPoints(x0,y0,x1,y1); static around(x,y,r); union(o?: Rect|null): Rect; intersect(o): Rect; offset(dx,dy): Rect; contains(x,y): boolean; equals(o): boolean }`, `EMPTY`.
- `interface Layer { readonly name: string; readonly width: number; readonly height: number; readonly data: Uint8ClampedArray; readonly x: number; readonly y: number; readonly opacity: number; readonly visible: boolean }`; `makeLayer(name, width, height, data?, extra?: Partial<Layer>): Layer`; `withProps(layer, patch: Partial<Layer>): Layer` (same `data` reference); `copyLayer(layer)` (new data); `cropData(data, w, rect): Uint8ClampedArray`; `pasteData(dst, dstW, src, rect)`.
- `interface EditorDoc { width; height; layers: Layer[]; active: number; selection: Rect | null; dirty: boolean }`; `newDoc(w,h)` ("Background"); `checkSize(w,h)` throws `EditorError`; `class EditorError extends Error`; `composite(doc, rect?: Rect, override?: Map<number, Uint8ClampedArray>): Uint8ClampedArray` (RGBA of `rect`, normal blending, layer opacity, hidden skipped); `layerBounds(layer): Rect`.
- History (port of `src/umc/editor/history.py` after the review fixes): `Command { label; undo(doc); redo(doc); bytes; repaint(): Rect | null }`; `PatchCommand.fromRegion(label, doc, index, rect, before)`; `LayerListCommand(label, before, after, activeBefore, activeAfter)`; `DocStateCommand(label, before:[w,h,layers], after)`; `SelectionCommand(before, after, label)`; `History(doc, limitBytes = 500*2**20)` with `push`, `undo(): Rect|null`, `redo()`, `canUndo`, `canRedo`, `undoLabel`, `redoLabel`, `position` (token), `markSaved(position?)`.

- [ ] **Step 1: failing tests** — port `tests/test_editor_model.py` one-to-one (composite opacity/hidden, offset layer + rect, size limit, rect ops, new doc, patch undo/redo, redo cleared, memory cap drops oldest, dirty tracks saved position, saved marker is a state not a length, saved marker survives dropped commands, layer list command). Pixel values identical to the Python tests (`abs(px[0]-128) <= 1` etc.).
- [ ] **Step 2:** `npm test -- editor-model` → FAIL (modules missing).
- [ ] **Step 3: implement.** Composite: per-pixel source-over in a loop over the intersecting rows (premultiply on the fly), opacity multiplies source alpha.
- [ ] **Step 4:** PASS; `npx tsc --noEmit` clean.
- [ ] **Step 5: commit** `Add the web editor document model and undo history`.

### Task 2: Raster interfaces and `.ora`

**Files:** create `raster.ts`, `ora.ts`, `open.ts`; test `tests/editor-ora.test.ts`.

**Interfaces:**
- `interface PngCodec { encode(data, w, h): Promise<Uint8Array>; decode(bytes: Uint8Array): Promise<{ data: Uint8ClampedArray; width: number; height: number }> }`; `canvasPng: PngCodec` (OffscreenCanvas `convertToBlob` / `createImageBitmap`).
- `interface Rasterizer { shape(kind: "line"|"rect"|"ellipse", box: Rect, pts: [number,number,number,number], style: {width, mode, stroke: RGBA, fill: RGBA, opacity}): Uint8ClampedArray; text(text, font: {family, size, bold, italic}, color: RGBA): { data, width, height } }`; `canvasRasterizer: Rasterizer` (OffscreenCanvas 2D; round caps/joins).
- `writeOra(doc, png): Promise<Uint8Array>`; `readOra(bytes, png): Promise<EditorDoc>` (stack.xml via a tiny regex/attribute parser — `DOMParser` isn't available in node or workers; attributes: name, src, x, y, opacity, visibility, selected; nested stacks flattened).
- `openFiles(files: {name: string; bytes?: Uint8Array; decode: () => Promise<{data,width,height}>}[], png): Promise<{ doc, errors: string[] }>` — `.ora` by name, one layer per image (name = stem), canvas = max size, active = top, throws `EditorError("None of the files could be opened as an image.")`.

- [ ] **Step 1: failing tests** with a fake `PngCodec` (encodes `[w,h,...data]` as bytes): round trip (names, offsets, opacity 0.5, hidden), `mimetype` first and stored (inspect with fflate `unzipSync` + raw header bytes: method 0 at offset 8), thumbnail ≤ 256, layers order, open-files size/names/active, bad file reported, none open → error.
- [ ] **Step 2–4:** FAIL → implement (fflate `zipSync` with `{ mimetype: [bytes, { level: 0 }] }` first) → PASS.
- [ ] **Step 5: commit** `Read and write OpenRaster files on the website`.

### Task 3: Tools — brush, eraser, fill, picker

**Files:** `tools/base.ts`, `tools/paint.ts`, `tools/fill.ts`, `tools/index.ts`; test `tests/editor-tools.test.ts`.

**Interfaces:** `ToolContext { primary: RGBA; secondary: RGBA; options: Values; shift: boolean; button: "left"|"right"; font: string }` and `color(ctx)`; `abstract class Tool { id; label; shortcut; options: OptionSpec[]; cursor; press(doc,x,y,ctx): Rect|null; move(...): Rect|null; release(...): Command|null; preview(doc): {data,width,height,x,y}|null; cancel(doc): Rect|null }`; `clipRect(doc, layer)`. Option specs (exact desktop values): brush size 12 (1–500 px), hardness 80, opacity 100; eraser size 24; fill tolerance 32 (0–255), sample_all false; picker sample_all true.

- [ ] **Step 1: failing tests** — port every brush/eraser/fill/picker test from `tests/test_editor_tools.py` (incl. offset layer, opacity not stacking, right button, selection clip, tolerance, connected only, sample all, semi-transparent fill, 4000×4000 fill < 2 s, picker) plus `stroke does not copy whole layer`: spy on `Uint8ClampedArray` allocations ≥ layer size via a wrapped `cropData` counter exported for tests (`__stats.largestCrop`) and assert < full size for a 3000×3000 stroke.
- [ ] **Step 2–4:** FAIL → implement (128 px tiles like `paint.py`; scanline flood like `fill.py`) → PASS.
- [ ] **Step 5: commit** `Add web editor brush, eraser, fill and colour picker`.

### Task 4: Tools — shapes, selection, crop, text

**Files:** `tools/shapes.ts`, `tools/select.ts`, `tools/text.ts`, update `tools/index.ts`; tests appended.

**Interfaces:** shape tools take a `Rasterizer` in their constructor (`new RectTool(raster)`); `TOOLS` becomes a factory `makeTools(raster): Tool[]` in desktop order (brush, eraser, fill, picker, line, rect, ellipse, text, select, crop). Options: line width 4 (1–200), opacity 100; shape width 4, mode outline/filled/both, opacity 100; text size 48 (6–500), bold, italic. `addTextLayer(doc, raster, text, x, y, font, color): Command|null`. Box clipped to the canvas ± padding before rasterising (review fix #4).

- [ ] **Step 1: failing tests** with a stub `Rasterizer` that fills the box (rect/ellipse by formula, line by distance) — port the shape/select/text tests (outline vs filled, shift snap, preview then commit + undo, select and clear, click clears, select all/deselect, crop pending, text layer added + undo, blank text → null, preview bounded on a 4000×3000 canvas dragged past the edges).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: commit** `Add web editor shapes, selection, crop and text`.

### Task 5: Operations

**Files:** `ops.ts`; test `tests/editor-ops.test.ts`.

**Interfaces:** same names as `src/umc/editor/ops.py` in camelCase: `crop`, `resizeImage(doc, w, h, resample: "lanczos"|"bilinear"|"nearest", resize?: (data,w,h,nw,nh,method)=>Uint8ClampedArray)` (default resize: bilinear/nearest in JS; the page injects a Canvas `drawImage` high-quality resizer for "lanczos"), `canvasSize(doc,w,h,anchor)`, `ANCHORS`, `rotate(doc, deg, layerOnly)`, `flip(doc, horizontal, layerOnly)`, `brightnessContrast(data, b, c)`, `applyBrightnessContrast`, `addLayer`, `addLayers`, `duplicateLayer`, `deleteLayer`, `mergeDown`, `moveLayer`, `selectLayer`, `renameLayer`, `setOpacity`, `setVisible`.

- [ ] **Step 1: failing tests** — port `tests/test_editor_ops.py` (all positions/values, the parametrised undo/redo round trip for every op, undo order mixed commands).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: commit** `Add web editor crop, resize, rotate, layer and brightness operations`.

### Task 6: Canvas view

**Files:** `ui/editor/canvas.ts`; e2e scaffold `e2e/editor.spec.ts` (first test only).

**Interfaces:** `class EditorCanvas { el: HTMLElement; zoom; setDocument(doc, history); setTool(tool); contextProvider: () => ToolContext; refresh(rect?); setPreviewLayer(index|null, data|null); fit(); setZoom(z, anchor?); zoomIn(); zoomOut(); widgetToImage(px,py); imageToWidget(x,y); cancelGesture(); undo(); redo(); push(cmd); onChanged; onColorPicked(rgba, button); onCursor(x,y); onTextRequested(x,y) }`. Composite cache = `ImageData` on an OffscreenCanvas, dirty-rect `putImageData`; visible canvas redrawn with `drawImage` scaled (`imageSmoothingEnabled = zoom < 1`), DPR-aware. Pointer events with `setPointerCapture`; two active touch pointers → pinch/pan, no painting.

- [ ] **Step 1: failing e2e** `canvas: drawing a stroke paints and undo restores` (a test page hook `window.__editor` exposes `doc` for assertions — only set when `location.search` has `e2e`, used by tests).
- [ ] **Step 2–4:** FAIL → implement (with the page in Task 7 minimal enough to mount) → PASS.
- [ ] **Step 5: commit** `Add the web editor canvas`.

### Task 7: Editor page, panels, dialogs, open/save

**Files:** `ui/editor/panels.ts`, `ui/editor/dialogs.ts`, `ui/editor/page.ts`, `styles.css`; e2e tests appended.

**Interfaces:** `mountEditor(host: HTMLElement, opts: { client: ConversionClient; onBack(): void }): EditorPage`; `EditorPage { open(files: InputFile[]): Promise<boolean>; addFiles(files): Promise<void>; dirty: boolean; maybeLeave(): boolean; destroy() }`. Decoding: browser-native for png/jpg/webp/gif/avif/bmp/ico, else `client.run(imageTarget("png"), [file], defaults)` then decode. Saving: `SaveDialog` (name, format list = `["ora", ...image converter targets]`, `SettingsForm` of the image converter's options); `.ora` via `writeOra(doc, canvasPng)`; others: composite → `canvasPng.encode` → `client.run(target, [pngFile], values)`; write with `showSaveFilePicker` handle when available (kept for Save), else `<a download>`. Snapshot layers before saving; `history.markSaved(token)` on success. Panels and shortcuts exactly as the spec.

- [ ] **Step 1: failing e2e** — opens one layer per image; shortcut E selects eraser and U toggles rect/ellipse; add/delete layer + undo; rectangle drawn; text layer committed; crop with Enter; Save as PNG downloads a PNG of the canvas size; Save as `.ora` downloads a zip with `mimetype` = `image/openraster` and 2 layers; `psd opens via the worker` (fixture `design.psd`); `ctrl+s opens save dialog, typing in name field doesn't switch tools`; `touch two-finger does not paint` (dispatch two pointer streams with `pointerType: "touch"`).
- [ ] **Step 2–4:** FAIL → implement → PASS (`npm run e2e -- e2e/editor.spec.ts`).
- [ ] **Step 5: commit** `Add the web image editor page`.

### Task 8: Convert | Edit switch in the website

**Files:** `ui/app.ts`, `styles.css`; e2e appended.

**Interfaces:** in `app.ts`: segmented `.mode` control above the files card; `editAvailable()` = files non-empty and all `family === "image"` (`.ora` detected as image — add `"ora"` to the web `detect.ts` image extensions); Edit → `await import("./editor/page")`, hide converter UI, `mountEditor`; Back / Convert → `page.maybeLeave()` (`confirm` when dirty) → restore. Drops in Edit mode → `page.addFiles`. `beforeunload` listener active only while `page.dirty`.

- [ ] **Step 1: failing e2e** — switch hidden for `doc.md`, shown for two PNGs; Edit then Back returns to the converter with the format pills intact; drop in edit mode adds a layer; Back with unsaved changes asks (`page.on("dialog")` dismiss → stays, accept → leaves); `beforeunload only while dirty` (evaluate a dispatched `beforeunload` event's `defaultPrevented`).
- [ ] **Step 2–4:** FAIL → implement → PASS; also run the whole `npm run e2e` (converter tests unchanged and green) and `npm test`.
- [ ] **Step 5: commit** `Add the Convert | Edit switch to the website`.

### Task 9: Docs, final checks, land

- [ ] README "What works where": editor row and `.ora` row Website column → ✅ (`.ora` → image formats too).
- [ ] CLAUDE.md: roadmap 10.2 gets a web line; web section lists `web/src/editor/` + `web/src/ui/editor/`; architecture note "Rasterizer/PngCodec injected so node tests need no canvas".
- [ ] `npx tsc --noEmit`, `npm test`, `npm run e2e`, desktop `pytest` (unchanged) all green.
- [ ] Commit `Document the website image editor`; final whole-branch review; rebase main, ff-merge, push (pages.yml then re-runs the browser tests against the live site — check it).
