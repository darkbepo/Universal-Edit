# Image Editor (desktop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A layered image editor (between Paint and Photoshop) reached through a Convert | Edit switch in the desktop app.

**Architecture:** A Qt-free editor model in `src/umc/editor/` (layers as Pillow RGBA images, numpy for pixel work, a patch-based undo stack, tools as small classes that return undoable commands). A Qt view in `src/umc/gui/editor/` draws a cached composite and forwards mouse events in image coordinates. Saving to normal formats goes through the existing `ImageConverter`; layers are kept in OpenRaster `.ora`.

**Tech Stack:** Python 3.11+, PySide6-Essentials, Pillow, numpy (already installed through trimesh/rawpy, so add it to `pyproject.toml` explicitly), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-10-image-editor-design.md`

## Global Constraints

- Only `src/umc/gui/` imports Qt. `src/umc/editor/` must import without PySide6.
- The converter screen behaves exactly as before while Convert is selected.
- No new dependency except declaring `numpy>=1.26` (BSD, already bundled).
- Canvas limit: refuse > 16 000 px per side or > 250 megapixels, with the message `"This image is too large to edit (max 16 000 px per side)."`
- Undo memory cap: 500 MB by default; the oldest steps are dropped first.
- Temporary folders: `tempfile.TemporaryDirectory(prefix="umc-edit-")`.
- Never overwrite silently: Save as relies on the system dialog's replace prompt; Save reuses a path the user already confirmed.
- User-facing errors in plain language (`ConversionError` messages); details go to the log.
- Commits: on `main`, author `darkbepo`, imperative subject, **no co-author or session trailers**. Don't stage the owner's unrelated uncommitted files — `git add` only the paths each task lists.
- Lint: `ruff check . && ruff format --check .` must pass before every commit.

## Review Focus

1. **Layers smaller than or offset from the canvas** (several images of different sizes). Tools must work in layer coordinates (`x - layer.x`) and clip to the layer bounds — test in Task 4 (`test_brush_on_offset_layer`).
2. **Images that aren't RGBA** (P, L, CMYK, 16-bit TIFF, RAW). Everything must become RGBA on load — test in Task 3 (`test_open_files_converts_modes`).
3. **Undo after structural changes** (crop/resize/rotate, then undo a brush stroke made before them). Pixel patches must stay valid — test in Task 2 (`test_undo_order_mixed_commands`).
4. **Saving to a format without transparency** (JPG/BMP) must flatten onto the image converter's background colour, not black — test in Task 7 (`test_save_jpg_has_white_background`).
5. **Fill on a fully transparent layer and fill with tolerance 0** must not run away or hang on large images — test in Task 4 (`test_fill_transparent_layer_fast`).

---

## File structure

```
src/umc/editor/__init__.py
src/umc/editor/geometry.py     # Rect
src/umc/editor/layer.py        # Layer
src/umc/editor/document.py     # Document, composite, limits
src/umc/editor/history.py      # Command, PatchCommand, LayerListCommand, DocumentStateCommand, History
src/umc/editor/ora.py          # read_ora / write_ora
src/umc/editor/io.py           # open_files
src/umc/editor/ops.py          # crop, resize, canvas_size, rotate/flip, brightness/contrast
src/umc/editor/fonts.py        # find_font
src/umc/editor/save.py         # save_document
src/umc/editor/tools/__init__.py   # TOOLS registry
src/umc/editor/tools/base.py       # Tool, ToolContext
src/umc/editor/tools/paint.py      # BrushTool, EraserTool
src/umc/editor/tools/fill.py       # FillTool, PickerTool
src/umc/editor/tools/shapes.py     # LineTool, RectTool, EllipseTool
src/umc/editor/tools/select.py     # SelectTool, CropTool
src/umc/editor/tools/text.py       # render_text, add_text_layer
src/umc/gui/editor/__init__.py
src/umc/gui/editor/canvas.py       # EditorCanvas
src/umc/gui/editor/layers_panel.py # LayersPanel
src/umc/gui/editor/side_panel.py   # ToolBar, ColorSwatches, ToolSettings
src/umc/gui/editor/dialogs.py      # ResizeDialog, CanvasSizeDialog, BrightnessContrastDialog, SaveOptionsDialog
src/umc/gui/editor/editor_page.py  # EditorPage
Modify: src/umc/gui/main_window.py, src/umc/gui/style.qss, src/umc/core/detect.py,
        src/umc/converters/imaging.py, src/umc/selftest.py, pyproject.toml, README.md, CLAUDE.md
Tests:  tests/test_editor_model.py, tests/test_editor_tools.py, tests/test_editor_ops.py,
        tests/test_editor_ora.py, tests/test_editor_save.py, tests/test_gui_editor.py
```

---

### Task 1: Layers, document and composite

**Files:**
- Create: `src/umc/editor/__init__.py` (empty), `geometry.py`, `layer.py`, `document.py`
- Modify: `pyproject.toml` (add `"numpy>=1.26"` to `dependencies`)
- Test: `tests/test_editor_model.py`

**Interfaces:**
- Produces:
  - `Rect(x0: int, y0: int, x1: int, y1: int)` frozen dataclass (x1/y1 exclusive); `.width`, `.height`, `.empty -> bool`, `.union(other) -> Rect`, `.intersect(other) -> Rect` (empty result → `Rect(0,0,0,0)`), `.offset(dx, dy) -> Rect`, `Rect.from_points(x0, y0, x1, y1)` (normalises order), `.box -> tuple[int,int,int,int]` (for Pillow).
  - `Layer(name: str, image: Image.Image, x: int = 0, y: int = 0, opacity: float = 1.0, visible: bool = True)`; `__post_init__` converts the image to RGBA; `.bounds -> Rect` (canvas coordinates); `.copy() -> Layer` (deep copy of the image).
  - `Document(width: int, height: int, layers: list[Layer], active: int = 0, selection: Rect | None = None)`; `check_size(width, height)` module function raises `ConversionError` with the Global Constraints message; `Document.new(width, height) -> Document` (one transparent layer "Background"); `.active_layer -> Layer`; `.bounds -> Rect`; `.composite(rect: Rect | None = None) -> Image.Image` (RGBA, the whole canvas or just `rect`); `.dirty: bool` (unsaved changes flag, starts False).

- [ ] **Step 1: Write failing tests**

```python
def test_composite_opacity_and_hidden():
    red = Layer("r", Image.new("RGBA", (4, 4), (255, 0, 0, 255)))
    blue = Layer("b", Image.new("RGBA", (4, 4), (0, 0, 255, 255)), opacity=0.5)
    hidden = Layer("h", Image.new("RGBA", (4, 4), (0, 255, 0, 255)), visible=False)
    doc = Document(4, 4, [red, blue, hidden])          # index 0 = bottom
    px = doc.composite().getpixel((0, 0))
    assert px[3] == 255 and abs(px[0] - 128) <= 1 and abs(px[2] - 127) <= 1 and px[1] == 0

def test_composite_offset_layer_and_rect():
    small = Layer("s", Image.new("RGBA", (2, 2), (0, 0, 0, 255)), x=2, y=2)
    doc = Document(4, 4, [small])
    img = doc.composite()
    assert img.getpixel((1, 1))[3] == 0 and img.getpixel((3, 3)) == (0, 0, 0, 255)
    assert doc.composite(Rect(2, 2, 4, 4)).size == (2, 2)

def test_layer_converts_to_rgba():
    assert Layer("p", Image.new("P", (2, 2))).image.mode == "RGBA"

def test_size_limit():
    with pytest.raises(ConversionError, match="too large"):
        check_size(16_001, 10)
    with pytest.raises(ConversionError):
        check_size(16_000, 16_000)   # 256 MP > 250 MP

def test_rect_ops():
    assert Rect.from_points(5, 5, 1, 2) == Rect(1, 2, 5, 5)
    assert Rect(0, 0, 4, 4).intersect(Rect(6, 6, 8, 8)).empty
    assert Rect(0, 0, 2, 2).union(Rect(1, 1, 3, 3)) == Rect(0, 0, 3, 3)
```

- [ ] **Step 2:** `pytest tests/test_editor_model.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** — composite with `Image.alpha_composite` onto a transparent canvas; for opacity < 1 multiply the layer's alpha band (`ImageChops.multiply` or numpy) before compositing; only touch layers whose bounds intersect `rect`.
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5: Commit** `git add src/umc/editor tests/test_editor_model.py pyproject.toml` → `Add editor document model with layers`.

---

### Task 2: Undo history

**Files:**
- Create: `src/umc/editor/history.py`
- Test: `tests/test_editor_model.py` (append)

**Interfaces:**
- Consumes: `Document`, `Layer`, `Rect` (Task 1).
- Produces:
  - `class Command(ABC)`: `label: str`; `undo(doc) -> None`; `redo(doc) -> None`; `nbytes -> int`.
  - `PatchCommand(label, layer_index: int, rect: Rect, before: Image.Image, after: Image.Image)`; `rect` is in **layer** coordinates. Classmethod `PatchCommand.capture(label, doc, layer_index, rect, before_full: Image.Image) -> PatchCommand | None` crops `before` from a pre-stroke snapshot and `after` from the current layer (returns None for an empty rect).
  - `LayerListCommand(label, before: list[Layer], after: list[Layer], active_before: int, active_after: int)` — add / delete / duplicate / reorder / merge / rename / opacity / visibility.
  - `DocumentStateCommand(label, before: tuple[int, int, list[Layer]], after: tuple[int, int, list[Layer]])` — (width, height, layers) for crop / resize / canvas size / rotate / flip.
  - `History(doc: Document, limit_bytes: int = 500 * 1024**2)`: `push(cmd)` (the change is already applied; clears redo; sets `doc.dirty = True`; drops oldest while over the limit), `undo() -> Rect | None`, `redo() -> Rect | None` (both return what to repaint; None = everything), `can_undo`, `can_redo`, `undo_label`, `redo_label`, `mark_saved()` (remembers the position; `doc.dirty` becomes False there and True after any further change, also after undoing past it).
  - Helper `snapshot_layers(doc) -> list[Layer]` (shallow list of `Layer.copy()`s).

- [ ] **Step 1: Write failing tests** — `test_patch_undo_redo` (paint a pixel, capture, undo restores, redo re-applies), `test_redo_cleared_on_push`, `test_memory_limit_drops_oldest` (limit 100 bytes, push two 10×10 patches → only one undo left), `test_dirty_tracks_saved_position` (push → dirty; mark_saved → clean; push + undo → clean; undo once more → dirty). (`test_undo_order_mixed_commands` lives in Task 6, because it needs `ops.crop`: paint (8,8), crop 10×10 → 5×5, undo crop, undo brush → pixel (8,8) transparent and size 10×10; redo twice → 5×5 again.)
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.** `nbytes` = sum of `w*h*4` of stored images.
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add undo history for the editor`.

---

### Task 3: OpenRaster files and opening images

**Files:**
- Create: `src/umc/editor/ora.py`, `src/umc/editor/io.py`
- Modify: `src/umc/core/detect.py` (add `"ora"` to `IMAGE_EXTS`), `src/umc/converters/imaging.py` (`open_image`: `.ora` → `read_ora(path).composite()`)
- Test: `tests/test_editor_ora.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `write_ora(doc: Document, path: Path) -> None`, `read_ora(path: Path) -> Document`, `open_files(paths: list[Path]) -> tuple[Document, list[str]]` (document + plain-language messages for files that couldn't be opened; raises `ConversionError("None of the files could be opened as an image.")` when none open).

Format details (from the spec): ZIP; first entry `mimetype`, stored uncompressed, content `image/openraster`; `stack.xml` `<image w h><stack>` with `<layer name src x y opacity visibility="visible|hidden">` **top layer first**; `data/layerN.png`; `mergedimage.png`; `Thumbnails/thumbnail.png` ≤ 256 px. `read_ora` ignores nested `<stack>` groups by flattening their layers in order, and treats missing attributes as defaults.

`open_files`: each path via `imaging.open_image` (`.ora` → its layers are appended), `ImageOps.exif_transpose`, `.convert("RGBA")`; first file at the bottom; layer name = file stem; canvas = max width × max height; `check_size` first; active = top layer.

- [ ] **Step 1: Write failing tests** — `test_ora_round_trip` (2 layers with offset, opacity 0.5, one hidden → read back equal attributes and pixels), `test_ora_mimetype_first_and_stored` (`zipfile.ZipFile(...).infolist()[0]` is `mimetype` with `compress_type == ZIP_STORED`), `test_ora_opens_in_converter` (`convert([ora], "png", out)` from conftest works and the PNG equals the composite), `test_open_files_layers_and_size` (100×50 + 30×80 → canvas 100×80, names = stems, order preserved), `test_open_files_bad_file_reported` (a `.png` with junk bytes + one good file → document with 1 layer and one message naming the bad file), and:

```python
def test_open_files_converts_modes(tmp_path):
    paths = []
    for mode in ("P", "L", "CMYK", "I;16"):
        p = tmp_path / f"{mode.replace(';', '')}.tif"
        Image.new(mode, (8, 8)).save(p)
        paths.append(p)
    doc, errors = open_files(paths)
    assert not errors and all(l.image.mode == "RGBA" for l in doc.layers)
```
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement** (ElementTree for `stack.xml`).
- [ ] **Step 4:** PASS; also run `pytest tests/test_images.py tests/test_detect*.py -q` to check `.ora` didn't break detection.
- [ ] **Step 5: Commit** `Read and write OpenRaster files with layers`.

---

### Task 4: Tool framework, brush, eraser, fill, colour picker

**Files:**
- Create: `src/umc/editor/tools/__init__.py`, `base.py`, `paint.py`, `fill.py`
- Test: `tests/test_editor_tools.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `ToolContext(primary: tuple[int,int,int,int], secondary: tuple[int,int,int,int], options: Any, shift: bool = False, button: str = "left")` dataclass; `ctx.color` = primary for left, secondary for right.
  - `class Tool(ABC)`: class attrs `id: str`, `label: str`, `shortcut: str`, `options_cls: type | None`; methods `press(doc, x: float, y: float, ctx) -> Rect | None`, `move(doc, x, y, ctx) -> Rect | None`, `release(doc, x, y, ctx) -> Command | None`, `preview(doc) -> tuple[Image.Image, int, int] | None` (overlay image at canvas position, default None), `cancel(doc) -> Rect | None`. Coordinates are canvas pixels (floats); returned Rects are canvas coordinates to repaint.
  - Helper `clip_rect(doc, layer) -> Rect` in layer coordinates: layer bounds ∩ selection (if any), offset by `-layer.x, -layer.y`.
  - `BrushOptions(size: int = opt(12, "Size", kind="slider", min=1, max=500, unit="px"), hardness: int = opt(80, "Hardness", kind="slider", min=0, max=100, unit="%"), opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%"))`; `EraserOptions` same fields.
  - `BrushTool` (`id="brush"`, B), `EraserTool` (`id="eraser"`, E): snapshot the layer on press, stamp round dabs every `max(1, size/4)` px along the segment, accumulate the dirty rect, `release` → `PatchCommand.capture(...)`.
  - `FillOptions(tolerance: int = opt(32, "Tolerance", kind="slider", min=0, max=255), sample_all: bool = opt(False, "Sample all layers"))`; `FillTool` (`id="fill"`, G) fills on press, returns command on release.
  - `PickerOptions(sample_all: bool = opt(True, "Sample all layers"))`; `PickerTool` (`id="picker"`, I) sets `picked: tuple | None` and `picked_button`; the GUI reads it after `press` and updates the swatch. Release returns None.
  - `TOOLS: list[type[Tool]]` in `tools/__init__.py`, in tool-bar order: brush, eraser, fill, picker, line, rect, ellipse, text, select, crop (later tasks append).

Algorithms the signatures don't fix:
- **Dab:** alpha mask = `clip((r - d) / max(1, r * (1 - hardness/100)), 0, 1)` where `d` is the distance to the centre, times `opacity/100`; brush blends `color` over the layer (source-over on that region using numpy float32); eraser does `alpha *= 1 - mask`. Within one stroke the coverage must not stack beyond the opacity: keep a per-stroke `coverage` array (max of masks), and compute pixels from the snapshot + coverage, not incrementally.
- **Fill:** compare against the seed pixel with max channel difference ≤ tolerance (RGBA), sample from the layer or the composite; flood fill with `scipy`-free connected labelling: use a BFS on a boolean numpy mask in row spans (scanline fill) — must finish a 4000×4000 fully transparent layer in < 2 s.

- [ ] **Step 1: Write failing tests:**

```python
def ctx(opts, color=(255, 0, 0, 255)):
    return ToolContext(primary=color, secondary=(255, 255, 255, 255), options=opts)

def run_stroke(tool, doc, pts, c):
    tool.press(doc, *pts[0], c)
    for p in pts[1:]:
        tool.move(doc, *p, c)
    return tool.release(doc, *pts[-1], c)

def test_brush_paints_and_undoes():
    doc = Document.new(50, 50); h = History(doc)
    cmd = run_stroke(BrushTool(), doc, [(10, 10), (40, 10)], ctx(BrushOptions(size=6)))
    assert doc.active_layer.image.getpixel((25, 10)) == (255, 0, 0, 255)
    h.push(cmd); h.undo()
    assert doc.active_layer.image.getpixel((25, 10))[3] == 0

def test_brush_opacity_does_not_stack_within_stroke():
    doc = Document.new(50, 50)
    run_stroke(BrushTool(), doc, [(10, 10), (10, 10.5), (10, 11)], ctx(BrushOptions(size=10, opacity=50, hardness=100)))
    assert 120 <= doc.active_layer.image.getpixel((10, 10))[3] <= 135

def test_brush_respects_selection():
    doc = Document.new(50, 50); doc.selection = Rect(0, 0, 20, 50)
    run_stroke(BrushTool(), doc, [(5, 10), (45, 10)], ctx(BrushOptions(size=4)))
    assert doc.active_layer.image.getpixel((30, 10))[3] == 0

def test_brush_on_offset_layer():
    doc = Document(50, 50, [Layer("s", Image.new("RGBA", (20, 20)), x=30, y=30)])
    run_stroke(BrushTool(), doc, [(35, 35), (35, 35)], ctx(BrushOptions(size=4)))
    assert doc.layers[0].image.getpixel((5, 5)) == (255, 0, 0, 255)
    run_stroke(BrushTool(), doc, [(5, 5), (5, 5)], ctx(BrushOptions(size=4)))   # outside: no crash

def test_eraser_clears():   # opaque layer, erase line → alpha 0 on the line
def test_fill_tolerance(): # left half (10,10,10), right half (40,40,40); tolerance 5 fills left only, 40 fills both
def test_fill_sample_all_layers():  # boundary drawn on layer 0, fill on empty layer 1 with sample_all stops at it
def test_picker_sets_color():  # PickerTool.press on a blue pixel → picked == (0, 0, 255, 255)

def test_fill_transparent_layer_fast():
    doc = Document.new(4000, 4000)
    t = time.perf_counter()
    tool = FillTool(); c = ctx(FillOptions(tolerance=0))
    tool.press(doc, 5, 5, c); tool.release(doc, 5, 5, c)
    assert time.perf_counter() - t < 2.0
    assert doc.active_layer.image.getpixel((3999, 3999)) == (255, 0, 0, 255)
```
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add brush, eraser, fill and colour picker tools`.

---

### Task 5: Shapes, text, rectangle selection

**Files:**
- Create: `src/umc/editor/tools/shapes.py`, `select.py`, `text.py`, `src/umc/editor/fonts.py`
- Modify: `src/umc/editor/tools/__init__.py` (register)
- Test: `tests/test_editor_tools.py` (append)

**Interfaces:**
- Consumes: Task 4 (`Tool`, `ToolContext`, `clip_rect`), Task 2 (`PatchCommand`, `LayerListCommand`).
- Produces:
  - `LineOptions(width: int = opt(4, "Width", kind="slider", min=1, max=200, unit="px"), opacity: int = opt(100, "Opacity", kind="slider", min=1, max=100, unit="%"))`.
  - `ShapeOptions(width: int = opt(4, ...), mode: str = opt("outline", "Style", choices=[("outline", "Outline"), ("filled", "Filled"), ("both", "Outline + fill")]), opacity: int = opt(100, ...))`.
  - `LineTool` (`line`, L), `RectTool` (`rect`, U), `EllipseTool` (`ellipse`, U — the tool bar toggles between rect and ellipse on repeated U): drag shows `preview()`; release draws onto the active layer (outline = primary, fill = secondary; Shift snaps lines to 45° and makes squares/circles) and returns a `PatchCommand`. Drawn with Pillow `ImageDraw` on a transparent overlay, then alpha-composited onto the clipped layer region; anti-alias by drawing at 4× and downsampling with LANCZOS.
  - `SelectTool` (`select`, M): drag sets `doc.selection` (clipped to the canvas; a click without drag clears it); returns a `SelectionCommand(before: Rect | None, after: Rect | None)` (add to `history.py`).
  - Functions in `select.py`: `select_all(doc) -> Command`, `deselect(doc) -> Command | None`, `clear_selection(doc) -> Command | None` (makes the selected area of the active layer transparent; None without a selection).
  - `CropTool` (`crop`, C): drag stores `pending: Rect | None` and shows it in `preview()`; the GUI calls `ops.crop(doc, tool.pending)` on Enter (Task 6).
  - `TextOptions(size: int = opt(48, "Size", kind="slider", min=6, max=500, unit="px"), bold: bool = opt(False, "Bold"), italic: bool = opt(False, "Italic"))`; the font family is chosen in the GUI (QFontComboBox) and passed separately.
  - `find_font(family: str, bold: bool, italic: bool) -> Path | None` in `editor/fonts.py`: searches the bundled DejaVu folder (`converters.subtitles.fonts_dir()`), then the OS font folders (`%WINDIR%\Fonts`, `%LOCALAPPDATA%\Microsoft\Windows\Fonts`, `/usr/share/fonts`, `~/.local/share/fonts`, `~/.fonts`, `/Library/Fonts`, `~/Library/Fonts`); matches `ImageFont.truetype(p).getname()` (family, style) case-insensitively; prefers the exact style, falls back to Regular; index cached in a module-level dict built once.
  - `render_text(text: str, family: str, size: int, bold: bool, italic: bool, color: tuple) -> Image.Image` (RGBA, tight to the text with 2 px padding, multi-line; uses `find_font`, falls back to `ImageFont.load_default(size)`); `add_text_layer(doc, text, x, y, family, size, bold, italic, color) -> Command | None` inserts a new layer above the active one named after the first 30 characters of the text, makes it active, returns a `LayerListCommand`; empty/whitespace text → None.

- [ ] **Step 1: Write failing tests** — `test_rect_outline_vs_filled` (centre pixel transparent for outline, secondary colour for filled), `test_line_shift_snaps` (drag (10,10)→(40,12) with shift → pixel (40,10) painted), `test_shape_preview_then_commit_undo`, `test_select_and_clear` (select 0..10, clear → those pixels alpha 0, others untouched, undo restores), `test_click_clears_selection`, `test_text_layer_added` (`add_text_layer` → layer count +1, name `"Hello"`, some pixel has primary colour, undo removes it), `test_find_font_dejavu` (skip if `fonts_dir()` is None; `find_font("DejaVu Sans", False, False).name == "DejaVuSans.ttf"`), `test_render_text_unknown_font_falls_back` (no exception, non-empty image).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add shape, text and selection tools`.

---

### Task 6: Whole-image operations and adjustments

**Files:**
- Create: `src/umc/editor/ops.py`
- Test: `tests/test_editor_ops.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces (each applies the change and returns the `Command` to push; layer-list changes use `LayerListCommand`, size changes `DocumentStateCommand`):
  - `crop(doc, rect: Rect) -> Command` (rect clipped to the canvas; layers keep their content, offsets shift by `-rect.x0, -rect.y0`, then each layer is cropped to the new canvas; selection cleared).
  - `resize_image(doc, width: int, height: int, resample: str = "lanczos") -> Command` (`"lanczos" | "bilinear" | "nearest"`; layers and offsets scale; `check_size` first).
  - `canvas_size(doc, width: int, height: int, anchor: str = "center") -> Command` (anchor in `top-left, top, top-right, left, center, right, bottom-left, bottom, bottom-right`; layers are not resized, only shifted).
  - `rotate(doc, degrees: int, layer_only: bool = False) -> Command` (90 = clockwise; 90/180/270; whole image swaps width/height and moves offsets; layer_only rotates the active layer around its own centre).
  - `flip(doc, horizontal: bool, layer_only: bool = False) -> Command`.
  - `brightness_contrast(image: Image.Image, brightness: int, contrast: int) -> Image.Image` (−100…+100 each; pure preview function; alpha untouched; brightness adds `brightness * 2.55`, contrast scales around 128 by `(100 + contrast) / 100` for contrast ≥ 0 and `1 + contrast/100` below 0; clip 0–255).
  - `apply_brightness_contrast(doc, brightness, contrast) -> Command | None` (active layer, clipped to the selection; None if both 0).
  - Layer list ops (all return `LayerListCommand`): `add_layer(doc) -> Command` (transparent, canvas size, above active, named `"Layer N"`), `duplicate_layer`, `delete_layer` (refused — returns None — on the last layer), `merge_down` (None on the bottom layer; result keeps the lower layer's name, opacity 1), `move_layer(doc, delta: int)`, `rename_layer(doc, index, name)`, `set_opacity(doc, index, opacity: float)`, `set_visible(doc, index, visible: bool)`.

- [ ] **Step 1: Write failing tests** — `test_crop_shifts_offsets`, `test_resize_scales_offsets`, `test_canvas_size_anchor_center` (4×4 → 8×8: layer at (2,2)), `test_rotate_90_whole_image` (10×4 → 4×10 and a marked corner pixel ends up in the right place), `test_rotate_layer_only` (canvas size unchanged), `test_flip_horizontal`, `test_brightness_contrast_values` (`(100,100,100)` with brightness 50 → ~227; contrast −100 → 128; alpha preserved), `test_merge_down_respects_opacity`, `test_delete_last_layer_refused`, `test_move_layer_updates_active`, `test_undo_order_mixed_commands` (described in Task 2), and an undo/redo round-trip for every op.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add crop, resize, rotate, layer and brightness operations`.

---

### Task 7: Saving through the image converter

**Files:**
- Create: `src/umc/editor/save.py`
- Test: `tests/test_editor_save.py`

**Interfaces:**
- Consumes: Task 3 (`write_ora`), `ImageConverter`, `IMAGE_FORMATS`, `ConversionJob`, `core.options.build/defaults`, `core.detect.detect`, `core.registry.Target`.
- Produces:
  - `SAVE_FORMATS -> list[tuple[str, str]]`: `("ora", "OpenRaster (layers)")` first, then `(tid, label)` for every `IMAGE_FORMATS` entry with `can_save(pil)`.
  - `save_options_cls() -> type` = `ImageOptions`.
  - `make_save_job(doc, dest: Path, target_id: str, option_values: dict, tmp: Path) -> ConversionJob` — writes `tmp/<dest.stem>.png` from `doc.composite()`, builds a job with `output_dir=tmp / "out"` using `ImageConverter` and the target for `target_id`.
  - `save_document(doc, dest: Path, target_id: str, option_values: dict | None = None, on_progress=None, cancel_event=None) -> Path` — `.ora` → `write_ora` to a temp file in `dest.parent` then `os.replace`; otherwise run the job inside `TemporaryDirectory(prefix="umc-edit-")`, then `os.replace(output, dest)` (the user already confirmed replacing `dest`). Raises `ConversionError` on failure; `dest` is untouched when it fails. Does **not** touch `doc.dirty` (the GUI calls `History.mark_saved()`).
  - The GUI runs `save_document` through a small `QThread` worker (Task 9), so saving never blocks the window.

- [ ] **Step 1: Write failing tests** — `test_save_png_keeps_alpha`, `test_save_ora_round_trip`, `test_save_quality_option_used` (jpg quality 10 file smaller than quality 95), `test_save_failure_leaves_dest` (existing dest with known bytes; target id `"jpg"` and a monkeypatched `ImageConverter.run` raising `ConversionError` → dest bytes unchanged, error raised), `test_save_formats_list` (`"ora"` first, `"png"` and `"jpg"` present), and:

```python
def test_save_jpg_has_white_background(tmp_path):
    doc = Document.new(10, 10)          # fully transparent
    out = save_document(doc, tmp_path / "x.jpg", "jpg")
    assert Image.open(out).convert("RGB").getpixel((5, 5)) == (255, 255, 255)
```
  (Check `ImageOptions`' background default first; if it isn't white, assert that default colour instead.)
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Save edited images through the image converter`.

---

### Task 8: Canvas widget

**Files:**
- Create: `src/umc/gui/editor/__init__.py`, `src/umc/gui/editor/canvas.py`
- Test: `tests/test_gui_editor.py`

**Interfaces:**
- Consumes: `Document`, `History`, `Tool`, `ToolContext`, `Rect`.
- Produces: `EditorCanvas(QWidget)`:
  - `set_document(doc: Document, history: History)`; `set_tool(tool: Tool)`; `context_provider: Callable[[], ToolContext]` attribute (set by the page; called on each event, with `shift`/`button` filled in by the canvas).
  - Signals: `changed()` (after any push/undo/redo — the page refreshes buttons and layer thumbnails), `color_picked(tuple, str)` (colour, button), `zoom_changed(float)`, `cursor_moved(int, int)`.
  - `zoom: float`; `fit()`, `set_zoom(z: float)` (0.05 – 32), `zoom_in()`, `zoom_out()`; wheel zooms around the cursor; middle-drag or Space+left-drag pans.
  - `refresh(rect: Rect | None = None)` — recomposites only `rect` into the cached `QImage` (`ImageQt` or `QImage(bytes, w, h, Format_RGBA8888)`), then `update()`.
  - `widget_to_image(pos: QPointF) -> tuple[float, float]`.
  - Paints: checkerboard (8 px squares, in widget space), the composite scaled with `Qt.SmoothTransformation` below 100 % and nearest above, the tool's `preview()` overlay, the selection as a dashed animated outline (`QTimer` 150 ms), the crop rectangle, and a circle cursor of the brush size for brush/eraser.
  - Mouse: press/move/release → tool methods; after `release` pushes the returned command to the history and emits `changed`; `Esc` → `tool.cancel(doc)`.

- [ ] **Step 1: Write failing tests** (pytest-qt; add a `qapp`/`qtbot` fixture use like the existing GUI tests):

```python
def test_canvas_stroke_pushes_history(qtbot):
    doc = Document.new(100, 100); h = History(doc)
    c = EditorCanvas(); qtbot.addWidget(c); c.resize(200, 200)
    c.set_document(doc, h); c.set_tool(BrushTool())
    c.context_provider = lambda: ToolContext((255,0,0,255), (255,255,255,255), BrushOptions(size=8))
    c.set_zoom(1.0)
    p = c.image_to_widget(50, 50)
    qtbot.mousePress(c, Qt.LeftButton, pos=p); qtbot.mouseMove(c, p + QPoint(10, 0))
    qtbot.mouseRelease(c, Qt.LeftButton, pos=p + QPoint(10, 0))
    assert h.can_undo and doc.active_layer.image.getpixel((55, 50))[3] > 0
```
  Plus `test_zoom_limits` and `test_widget_image_round_trip` (`image_to_widget(*widget_to_image(p)) ≈ p` at zoom 2.5 after panning). Add `image_to_widget(x, y) -> QPoint` to the produced interface.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `QT_QPA_PLATFORM=offscreen pytest tests/test_gui_editor.py -v` PASS.
- [ ] **Step 5: Commit** `Add the editor canvas widget`.

---

### Task 9: Editor page (tool bar, colours, tool settings, layers, dialogs, saving)

**Files:**
- Create: `src/umc/gui/editor/side_panel.py`, `layers_panel.py`, `dialogs.py`, `editor_page.py`
- Modify: `src/umc/gui/style.qss` (editor styles using existing `@token` colours)
- Test: `tests/test_gui_editor.py` (append)

**Interfaces:**
- Consumes: Tasks 2–8; `SettingsPanel.set_schema(options_cls, target_id, values)` / `.values()` / `changed` for tool settings and the save-options dialog (pass `target_id` = tool id or save format id).
- Produces:
  - `ToolBar(QWidget)`: one checkable `QToolButton` per `TOOLS` entry (text glyph or simple drawn icon, tooltip `"Brush (B)"`), signal `tool_selected(str)`; `select(tool_id)`.
  - `ColorSwatches(QWidget)`: `primary`, `secondary` (RGBA tuples, defaults black / white), `swap()`, signal `colors_changed()`; click opens `QColorDialog` with alpha.
  - `ToolSettings(QWidget)`: shows the active tool's options via `SettingsPanel`, plus a `QFontComboBox` for the text tool (default `"DejaVu Sans"` if available); `options_for(tool) -> Any` (built with `core.options.build`), values remembered per tool for the session.
  - `LayersPanel(QWidget)`: list top layer first with 40 px thumbnails, name (double-click → rename), eye checkbox; opacity slider for the active layer; buttons Add, Duplicate, Delete, Merge down, Up, Down. Signal `command(object)` carrying a `Command` produced by `ops.*` (the page pushes it), and `active_changed(int)`. `refresh(doc)`.
  - Dialogs: `ResizeDialog(doc) -> (w, h, resample) | None`, `CanvasSizeDialog(doc) -> (w, h, anchor) | None`, `BrightnessContrastDialog(page) -> (b, c) | None` (live preview: sets a temporary preview layer image on the canvas through `canvas.set_preview_layer(index, image | None)` — add that method to `EditorCanvas`), `SaveOptionsDialog(target_id) -> dict | None` (only when `ImageOptions` has visible fields for that target).
  - `EditorPage(QWidget)`:
    - `open_paths(paths: list[Path]) -> bool` (via `open_files`; shows the returned messages inline; False + inline error if nothing opened).
    - Signal `back_requested()`. `maybe_leave() -> bool` — if `doc.dirty` asks "Save changes to this image?" (Save / Discard / Cancel); Save runs Save-as synchronously-from-the-user's-view (waits for the save worker) and returns False if cancelled or failed.
    - Top bar: Back to Convert, Undo, Redo (tooltips show `undo_label`), Fit, 100 %, −, +, an "Image" menu button (Resize image…, Canvas size…, Rotate 90° right / left, Rotate 180°, Flip horizontal / vertical, each also "… layer only" via a "Layer" submenu, Brightness / contrast…, Crop to selection), Save, Save as….
    - Status line at the bottom: cursor position, canvas size, zoom %, inline messages (`objectName="Error"` / `"Muted"` like the main window).
    - Shortcuts exactly as in the spec ("Shortcuts" section), plus Enter applies a pending crop.
    - Save as: `QFileDialog.getSaveFileName` with one filter per `SAVE_FORMATS` entry, default name = first layer's source stem + `.png` (`.ora` when the doc has > 1 layer), in the first input's folder; optional `SaveOptionsDialog`; runs `save_document` on a `QThread` (keep worker + thread refs until `QThread.finished`, as the main window does); success → `history.mark_saved()`, status "Saved name.ext"; failure → inline error, still dirty.
    - Save: repeat last Save as (path, format, options) or act as Save as.

- [ ] **Step 1: Write failing tests:**
  - `test_page_opens_one_layer_per_image` (2 PNGs → `len(page.doc.layers) == 2`, layers panel shows 2 rows).
  - `test_shortcut_selects_tool` (key `E` → active tool id `"eraser"`).
  - `test_add_and_delete_layer_buttons_undoable`.
  - `test_save_as_png_and_ora` (monkeypatch `QFileDialog.getSaveFileName` to return the path; wait with `qtbot.waitUntil` for the file; both reopen; `page.doc.dirty` is False afterwards).
  - `test_leave_with_unsaved_changes_asks` (paint, monkeypatch `QMessageBox.question`/the page's prompt helper to return Cancel → `maybe_leave()` is False; Discard → True).
  - `test_brightness_dialog_preview_cancel_restores` (open dialog programmatically, set values, cancel → layer pixels unchanged, no history entry).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS (`QT_QPA_PLATFORM=offscreen`).
- [ ] **Step 5: Commit** `Add the image editor page`.

---

### Task 10: Convert | Edit switch in the main window

**Files:**
- Modify: `src/umc/gui/main_window.py`, `src/umc/gui/style.qss`
- Test: `tests/test_gui_editor.py` (append)

**Interfaces:**
- Consumes: `EditorPage` (Task 9).
- Produces in `MainWindow`: `self.stack: QStackedWidget` (page 0 = the existing converter `root`, page 1 = `EditorPage`, created lazily on first Edit); `self.mode_switch` — two checkable `QPushButton`s "Convert" / "Edit" in an exclusive `QButtonGroup`, `objectName="Segment"`, placed directly above the files card; `edit_available() -> bool` (files non-empty and every `f.family == MediaFamily.IMAGE`; `.ora` is an image ext after Task 3); `enter_edit()`, `leave_edit()`.
  - The switch is hidden unless `edit_available()`; updated in `_refresh`.
  - `enter_edit()`: remembers the window size, `resize(max(w, 1100), max(h, 720))`, `editor.open_paths([f.path for f in self.files])`; on False stays in Convert. `leave_edit()` (Back button or clicking Convert): `editor.maybe_leave()` must be True, then show page 0 and restore the size; the Convert button is re-checked either way to match the visible page.
  - `closeEvent`: in Edit mode, `maybe_leave()` False → `event.ignore()`.
  - Drag-and-drop while in Edit mode: dropped images are added as new layers (`editor.add_paths(paths)`, an undoable `LayerListCommand` — add `add_paths` to `EditorPage`).

- [ ] **Step 1: Write failing tests** — `test_switch_hidden_for_non_images` (a `.txt` file → switch hidden), `test_switch_shown_for_images`, `test_edit_and_back` (click Edit → `stack.currentIndex() == 1` and window ≥ 1100 wide; Back → index 0, previous size), `test_convert_flow_unchanged` (with images added and Convert selected, the format picker still lists `png`/`jpg` and conversion still works — reuse an existing main-window test helper), `test_drop_in_edit_mode_adds_layer`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full `QT_QPA_PLATFORM=offscreen pytest -q` PASS.
- [ ] **Step 5: Commit** `Add the Convert | Edit switch to the main window`.

---

### Task 11: Self-test, docs, final check

**Files:**
- Modify: `src/umc/selftest.py`, `README.md` ("What works where" table), `CLAUDE.md` (roadmap item 10 + architecture tree: `editor/` and `gui/editor/`)
- Check: `packaging/umc.spec` (numpy is already collected; add `hiddenimports` only if the self-test fails)

- [ ] **Step 1:** Add `_editor_check(d: Path) -> list[Path]` to `selftest.py`: build a 2-layer `Document` (one offset, opacity 0.5), brush stroke via `BrushTool`, `write_ora`, `read_ora` back (assert 2 layers), `save_document(... "png")` and `"jpg"`; return the three paths. Add the step `("editor: layers -> ora -> png/jpg", lambda: _editor_check(d))`. Run `python -m umc --self-test report.txt` (or the module function) locally → `RESULT: OK`.
- [ ] **Step 2:** README rows (desktop / website): "Image editor: layers, brush, eraser, fill, shapes, text, selection, crop/resize/rotate, brightness/contrast" ✅ / coming later; "OpenRaster (.ora) open/save with layers" ✅ / —; ".ora → any image format (converter)" ✅ / —.
- [ ] **Step 3:** CLAUDE.md: roadmap item 10 gets `1. ✅ Shell (Convert | Edit)` and `2. ✅ Image editor, first version (…)`, with the "not yet" list from the spec (lasso / magic wand, blend modes, filters, hue/saturation/curves, website).
- [ ] **Step 4:** `ruff check . && ruff format --check .` and `pytest -q` → all pass (skips only for missing external tools).
- [ ] **Step 5: Commit** `Add editor self-test step and document the image editor`, then `git push`, then run the "Build Windows executable" workflow (`gh workflow run build-windows.yml`) and check its self-test passes.
