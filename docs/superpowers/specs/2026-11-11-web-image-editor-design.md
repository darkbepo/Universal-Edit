# Image editor (website) — design

Date: 2026-11-11 · Roadmap item 10, website part · Status: approved design, awaiting spec review
Desktop counterpart: `docs/superpowers/specs/2026-11-10-image-editor-design.md` (same features,
same behaviour; this document only states what differs on the web).

## Goal

The website gets the same **Convert | Edit** switch and the same layered image editor as the
desktop app, running entirely in the visitor's browser (nothing uploaded).

## Decisions (agreed with the owner)

| Question | Decision |
|---|---|
| Scope | Same tools as the desktop editor: layers (add, duplicate, delete, merge down, reorder, hide, opacity, rename), brush, eraser, fill (tolerance, sample all layers), colour picker, line / rectangle / ellipse, text (new layer), rectangle selection, crop, resize image / canvas, rotate / flip (image or layer), brightness / contrast, undo / redo, save. |
| Architecture | Pixel data as plain RGBA arrays in the page (`Uint8ClampedArray`); the browser's Canvas 2D draws shapes and text into a temporary buffer that is then merged. The conversion worker is only used to open formats the browser can't decode and to save non-`.ora` formats. |
| Code layout | `web/src/editor/` (model, tools, history, `.ora`; no DOM except the Canvas 2D rasteriser behind one small interface) and `web/src/ui/editor/` (the page). |

## User flow (differences from desktop only)

1. The switch appears above the file list under the same rule: every added file is an image
   (family "image", including HEIC / TIFF / RAW / PSD) or a single `.ora`.
2. **Edit** hides the converter sections (`main.app` content and the bottom bar) and shows the
   editor full-window. The URL doesn't change. Leaving with unsaved changes asks with
   `confirm()` ("Discard unsaved changes to this image?"); closing / reloading the tab while
   there are unsaved changes triggers the browser's `beforeunload` prompt.
3. Dropping more images onto the page in Edit mode adds them as layers (undoable).

## Opening images

- PNG, JPG, WEBP, GIF (first frame), AVIF, BMP, ICO: `createImageBitmap(file)` → draw on an
  `OffscreenCanvas` → `getImageData` (browser decoding, fast).
- Everything else of family "image" (HEIC, TIFF, RAW, PSD, TGA, …) or a file the browser fails
  to decode: run the existing `image` converter with target `png` in the conversion worker,
  then decode that PNG as above. A plain-language error names the file if that fails too.
- `.ora`: unzipped with fflate (`unzipSync`), `stack.xml` parsed with `DOMParser`, layer PNGs
  decoded as above. Same rules as desktop: top layer first in the file, nested stacks flattened,
  missing attributes → defaults, layers without PNG skipped.
- Same size limit as desktop: 16 000 px per side and 250 megapixels, same message.

## The page

Same regions as desktop (top bar, left tool bar, canvas, right panel with tool settings /
colours / layers, status line). Built with the same `h()` helper and CSS tokens as
`ui/app.ts` / `styles.css`; dark / light follows `prefers-color-scheme` like the rest of the site.

- Canvas: one visible `<canvas>` sized to the viewport; the composite is kept in an offscreen
  canvas and only the dirty rectangle is re-composited (`putImageData` of the changed area).
  Zoom with the mouse wheel around the cursor (0.05×–32×), pan with middle-drag or Space+drag,
  Fit and 100 % buttons. Checkerboard behind transparency, marching-ants selection, dimmed area
  outside a pending crop, brush-size circle cursor. Pointer events (mouse, pen and touch; a
  two-finger gesture pans/zooms instead of painting).
- Tool settings: rendered with the existing `SettingsForm` (`ui/settings.ts`) from `OptionSpec`s,
  values remembered per tool for the visit. Text tool adds a font picker: a `<select>` of
  common web-safe families plus "DejaVu Sans" (bundled already via `dejavu-fonts-ttf`).
- Colours: two swatches using `<input type="color">` plus an alpha slider; X swaps.
- Layers list: thumbnail, name (double-click to rename inline), eye toggle; opacity slider
  (one undo step per change, committed on release); Add / Duplicate / Delete / Merge down /
  Up / Down buttons.
- Same keyboard shortcuts as desktop (B, E, G, I, L, U, T, M, C, X, [ ], Ctrl+Z / Ctrl+Y /
  Ctrl+Shift+Z, Ctrl+S / Ctrl+Shift+S, Ctrl+A, Ctrl+D, Delete, Ctrl+0, Ctrl+1, Enter applies a
  crop); Ctrl+S calls `preventDefault()` so the browser's own "save page" doesn't open.
  Shortcuts are ignored while typing in an input or the text box, and document shortcuts
  cancel a gesture in progress first (as on desktop).
- Narrow screens (< 900 px): the right panel moves under the canvas, the tool bar becomes a
  horizontal strip; the page must not scroll sideways.

## Tools and model

Behaviour, settings, ranges and defaults are identical to the desktop tables. Implementation
notes:

- Layers: `{ name, width, height, data: Uint8ClampedArray (RGBA), x, y, opacity, visible }`;
  layer objects are replaced, never changed in place, except pixel edits recorded as patches
  (same rule as desktop `history.py`).
- Undo: patch commands store only the changed rectangle (before / after); structural commands
  keep layer lists; selection commands; unique per-command tokens for the saved marker; 500 MB
  cap dropping the oldest steps.
- Brush / eraser: round dabs with hardness falloff, per-stroke coverage so opacity doesn't stack,
  original pixels remembered per 128 px tile (never a whole-layer copy) — ported from desktop.
- Fill: scanline flood fill on the RGBA array with per-channel tolerance; sample one layer or the
  composite; 4000 × 4000 transparent layer must fill in < 2 s.
- Shapes and text: drawn with Canvas 2D (anti-aliased, line caps round) into an
  `OffscreenCanvas` the size of the clipped shape box, read back with `getImageData`, then
  alpha-composited onto the layer, clipped to canvas ∩ layer ∩ selection. Shift snaps lines to
  45° and makes squares / circles.
- Text: an on-canvas `<textarea>` overlay (follows zoom and pan while open); committed as a new
  layer by Ctrl+Enter, Esc, clicking elsewhere, switching tool, saving or leaving.
- Crop, resize (Canvas 2D `drawImage` with `imageSmoothingQuality = "high"` for smooth,
  smoothing off for nearest), canvas size with 9 anchors, rotate / flip (array transforms),
  brightness / contrast (same formula as desktop), merge down (bakes both opacities).

## Saving

- **Save as…** opens a small dialog: file name, format (`.ora` first, then every target of the
  web `image` converter), and that converter's settings (same `SettingsForm`).
- `.ora`: written with fflate (`zipSync`, `mimetype` first and stored uncompressed, `stack.xml`,
  `data/layerN.png`, `mergedimage.png`, `Thumbnails/thumbnail.png` ≤ 256 px). PNGs are encoded
  with `OffscreenCanvas.convertToBlob`.
- Other formats: the composite is encoded to PNG and run through the existing `image`
  converter in the conversion worker (same quality / background / metadata options as the
  converter; transparent areas become the converter's background colour for JPG / BMP).
- Where the File System Access API exists (Chrome / Edge), Save as uses `showSaveFilePicker`
  and **Save** writes to the same file again; elsewhere the result is downloaded and Save
  downloads again with the same name and format.
- A save works on a snapshot (layers copied), so editing can continue; the saved marker uses
  the token of the state that was saved. Errors are shown in plain language in the status line.

## Testing

- `web/tests/editor-*.test.ts` (vitest, node): model, composite, history (incl. saved tokens and
  the memory cap), brush / eraser / fill / picker / selection / crop / ops on small arrays,
  offset and smaller layers, undo after structural changes, the 4000 × 4000 fill time, `.ora`
  zip structure and round trip (PNG encoding injected so node tests don't need a canvas).
- `web/e2e/editor.spec.ts` (Playwright, real Chromium): switch hidden for non-images and shown
  for images; Edit opens one layer per image; drawing a stroke changes pixels and enables Undo;
  add / delete layer; a rectangle and a text layer; crop with Enter; Save as PNG and `.ora`
  produce downloads that decode (and the `.ora` reopens with its layers); HEIC or TIFF input
  opens via the worker; Back with unsaved changes asks.
- `npx tsc --noEmit`, `npm test`, `npm run e2e` all green; `pages.yml` re-runs the browser tests
  against the live site after the push.

## Docs

Same commit as the feature: README "What works where" — editor rows get ✅ in the Website
column (the `.ora` row too); `CLAUDE.md` roadmap 10.2 / web section and architecture tree
mention `web/src/editor/` and `web/src/ui/editor/`.
