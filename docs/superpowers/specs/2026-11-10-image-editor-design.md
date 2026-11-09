# Image editor (desktop) — design

Date: 2026-11-10 · Roadmap item 10, first part · Status: approved design, awaiting spec review

## Goal

When images are added to the app, the user can choose **Convert | Edit**. Convert is the
existing converter, unchanged. Edit opens a layered image editor — between Paint and
Photoshop — in the same window. Desktop app first; the website follows later with the same
model and command design.

## Decisions (agreed with the owner)

| Question | Decision |
|---|---|
| Which app first | Desktop. Website later (README column says "coming later"). |
| Scope of the first version | Layers, brush, eraser, fill, colour picker, line / rectangle / ellipse, text, rectangle selection, crop, resize image / canvas, rotate / flip, brightness / contrast, undo / redo, save. |
| Keeping layers | Yes — save/open OpenRaster `.ora` (open standard, GIMP and Krita read it). |
| Several images added | Each image becomes its own layer in one canvas. |
| Architecture | Qt-free editor model (Pillow + numpy) in `src/umc/editor/`; Qt only in `src/umc/gui/editor/`. |

Not in this version (later steps): lasso / magic-wand selection, blend modes, filters
(blur, sharpen, noise), hue / saturation / curves, website editor.

## User flow

1. The user adds files as today. When **every** added file is an image (family image, incl.
   RAW / PSD / HEIC) or a single `.ora` file, a segmented **Convert | Edit** switch appears
   above the file list. Convert is selected by default; nothing else changes.
2. Clicking **Edit** switches the window (a `QStackedWidget` as the central widget) to the
   editor page and enlarges the window to at least 1100 × 720 (the previous size is restored
   when going back).
   - Each image is opened with `imaging.open_image` (so RAW / PSD / HEIC work), converted to
     RGBA and placed as its own layer, in file-list order, the first file at the bottom.
     Layer name = file stem.
   - Canvas size = the largest width × largest height of the images; smaller layers sit at the
     top-left (0, 0).
   - An `.ora` file opens with its layers, names, opacity, visibility and offsets.
3. **Back to Convert** returns to the converter. With unsaved changes it asks
   "Save changes?" (Save / Discard / Cancel).

## Editor layout

- **Top bar:** Back to Convert · Undo · Redo · zoom (Fit, 100 %, −/+) · Save · Save as…
- **Left:** narrow vertical tool bar with icons and tooltips (tool name + shortcut).
- **Centre:** canvas (a custom `QWidget` painting a cached `QImage` of the
  composite) on a checkerboard; mouse-wheel zoom around the cursor, middle-drag or Space+drag
  pans.
- **Right panel:**
  - Tool settings for the active tool (rendered from the tool's options dataclass with the
    existing `opt()` / settings-panel machinery, so adding a setting = adding a field).
  - Colour: primary / secondary swatches (click = `QColorDialog`, X swaps).
  - Layers list: thumbnail, name (double-click renames), eye toggle, opacity slider for the
    selected layer, buttons Add / Duplicate / Delete / Merge down / Move up / Move down.
- Theme: existing `style.qss` tokens; same accent colour, rounded corners.

### Shortcuts

B brush · E eraser · G fill · I colour picker · L line · U rectangle / ellipse (toggle) ·
T text · M rectangle select · C crop · X swap colours · [ / ] brush size ·
Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) · Ctrl+S / Ctrl+Shift+S · Ctrl+A select all ·
Ctrl+D deselect · Delete clears the selection on the active layer · Ctrl+0 fit · Ctrl+1 100 %.

## Tools and operations

All tools act on the **active layer**, and are clipped to the selection when there is one.

| Tool / operation | Settings | Notes |
|---|---|---|
| Brush | size 1–500 px, hardness 0–100 %, opacity 0–100 % | Round dabs spaced along the stroke; hardness = soft edge falloff. |
| Eraser | size, hardness, opacity | Lowers alpha. |
| Fill bucket | tolerance 0–255, "sample all layers" on/off | Flood fill (4-connected) on numpy arrays. |
| Colour picker | sample all layers on/off | Sets primary colour (right-click: secondary). |
| Line | width, opacity | Shift snaps to 45°. |
| Rectangle / Ellipse | outline width, mode (outline / filled / both) | Shift = square / circle; outline = primary, fill = secondary. |
| Text | font, size, bold / italic, colour = primary | Typed in an on-canvas box; stays editable until committed (Enter+Ctrl, Esc, or clicking elsewhere); committed as pixels on a **new layer** named after the text. |
| Rectangle select | — | Drag to select one rectangle (no adding or subtracting yet); marching-ants outline. |
| Crop | — | Crops the canvas to the selection, or to a dragged rectangle with Enter to apply. |
| Resize image | width / height px or %, keep aspect, resample (Lanczos / bilinear / nearest) | Dialog; all layers. |
| Canvas size | width / height, anchor (9 positions) | Dialog; layer offsets shift. |
| Rotate / flip | 90° left / right, 180°, flip horizontal / vertical; whole image or active layer | Menu in the top bar. |
| Brightness / contrast | −100 … +100 each | Dialog with live preview on the active layer. |

## Model (`src/umc/editor/`, no Qt)

- `document.py` — `Document(width, height, layers: list[Layer], active: int, selection: Rect | None)`;
  `composite(rect=None) -> Image` (normal alpha-over, layer opacity, hidden layers skipped).
- `layer.py` — `Layer(name, image: PIL.Image RGBA, x, y, opacity: float, visible: bool)`.
- `history.py` — undo stack of commands. Pixel commands store only the **changed bounding
  box** before/after (`PatchCommand`); structural commands (add / delete / reorder / merge /
  resize / crop / rotate) store what they need to reverse. Memory cap (default 500 MB) drops
  the oldest steps.
- `tools/` — one module per tool with an options dataclass and pure functions, e.g.
  `brush.stroke(layer, points, options, colour, clip) -> Rect` returning the dirty rectangle.
  The GUI passes mouse points in image coordinates; the tool returns what changed.
- `ops.py` — resize, canvas size, crop, rotate / flip, brightness / contrast.
- `ora.py` — read / write OpenRaster: ZIP with uncompressed `mimetype` first
  (`image/openraster`), `stack.xml` (top layer first, `name`, `x`, `y`, `opacity`,
  `visibility`), `data/*.png`, `mergedimage.png`, `Thumbnails/thumbnail.png` (≤ 256 px).
- `io.py` — `open_files(paths) -> Document` (images → layers, `.ora` → document).

Performance: the canvas keeps a cached composite and recomposites only the dirty rectangle
the tool returned; brush dabs are drawn with numpy on the affected region only.

## Saving

- **Save as…** — file dialog with `.ora` plus every image format the image converter can
  write. For anything other than `.ora`, the composite is written to a temporary PNG inside a
  `tempfile.TemporaryDirectory(prefix="umc-edit-")` and run through the existing
  `ImageConverter` as a normal `ConversionJob` on the worker thread, so quality, metadata and
  transparency-background options keep working; a small options dialog shows that converter's
  settings (the existing settings panel) when the format has any.
- **Save** — repeats the last Save as (same path, format and options). The first Save acts as
  Save as.
- Choosing an existing file in Save as asks to replace it (the system dialog's own prompt);
  the editor never overwrites silently. Saving to the original input file is allowed only
  through that prompt.
- Errors are shown inline in plain language and logged, as in the converter.

## Error handling

- An image that can't be opened: the other images still open; a plain message names the file.
- Very large canvases (> 16 000 px per side or > 250 MP): refused with a clear message.
- A save that fails leaves the document untouched and "unsaved".

## Code layout

```
src/umc/editor/            # Qt-free
  document.py, layer.py, history.py, ops.py, ora.py, io.py
  tools/  brush.py, eraser.py, fill.py, picker.py, shapes.py, text.py, select.py, crop.py
src/umc/gui/editor/        # Qt
  editor_page.py           # page layout, top bar, wiring, save logic
  canvas.py                # zoom / pan, mouse → tool, cached composite, selection outline
  tool_bar.py, layers_panel.py, color_swatch.py, dialogs.py
src/umc/gui/main_window.py # QStackedWidget, Convert | Edit switch
```

Text rendering uses Pillow `ImageFont` with the bundled DejaVu Sans plus system fonts found
by the font dialog (Qt lists them; the model receives a font file path).

## Testing

- `tests/test_editor_*.py` (pure Python): each tool on small arrays (pixels changed, dirty
  rect, selection clipping), undo / redo round-trips for every command, composite with opacity
  and hidden layers, `.ora` write → read round-trip (and the file opens as a valid ZIP with
  `mimetype` first), several images → layers, ops (resize, crop, canvas size, rotate).
- `tests/test_gui_editor.py` (pytest-qt, offscreen): switch appears only for images, Edit opens
  the editor with one layer per image, drawing a stroke changes pixels and enables Undo, add /
  delete layer, Save as PNG and `.ora` produce files that open, Back with unsaved changes asks.
- `src/umc/selftest.py`: a step that builds a two-layer document, writes `.ora` and PNG, and
  reads both back inside the built `.exe`.
- Lint and the full existing test suite must stay green.

## Docs

Same commit as the feature: README "What works where" rows for the editor (desktop ✅,
website "coming later"), and roadmap item 10 lines in `CLAUDE.md`.
