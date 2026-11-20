# Website video editor — step 3: the page shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The website's video editor page, reachable only behind the `?video-editor` / `?e2e` gate: Convert | Edit for video sets and `.umcvideo`, the start tile (gated), a media list, a canvas timeline (select, trim, move, reorder, split, delete, add rows, zoom, snapping), the inspector, a still preview that follows the playhead while scrubbing, undo / redo, and saving / opening a project with a locate window. No playback, thumbnails, waveforms (step 4), no export (step 5), no phone sheets (step 6) — but nothing breaks at 375 px.

**Architecture:** A page in `web/src/ui/videoeditor/` imported on demand from `ui/app.ts` (like `ensureAudio`). Everything worth testing is DOM-free and covered by vitest: option schemas and new-project glue (`web/src/videoedit/schemas.ts`, `start.ts`), timeline geometry / hit testing / snapping / drag results (`ui/videoeditor/timelinemath.ts`, a port of `gui/videoedit/timeline.py` without painting), the shortcut table, the locate matching glue, and `draw.ts` (executes `compose.ts` frames and `textlayout.ts` boxes on any Canvas 2D context, tested with a recording fake). The page files (`page.ts`, `timeline.ts`, `media.ts`, `inspector.ts`, `preview.ts`, `locate.ts`) are thin DOM shells over those and are covered by Playwright (`web/e2e/videoeditor.spec.ts`). Sources are opened on the page with Mediabunny (`sources.ts`); files the browser can't read or decode go through a new hidden worker converter `video-edit-source` (FFmpeg / ImageMagick near-lossless copy), guarded by `limits.sourceProblem`.

**Tech Stack:** TypeScript (strict), Vite, Canvas 2D, Mediabunny 1.65 (`Input`, `BlobSource`, `ALL_FORMATS`, `CanvasSink`, `canDecode()`), vitest, Playwright (Chromium / Edge); Python 3.11 only to add one section to the shared fixture.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` ("Architecture", "Preview and playback" → Scrub / still, "Text", "Limits", "Calls made after the builder check" 1–4, "Differences from the desktop", "Phones", "Start screen and switch", "Order of work" step 3). Desktop references: `src/umc/gui/videoedit/editor_page.py` (layout, `_apply`, `_push`, `_inspector_changed` / `_flush_pending`, `_split_target`, `delete_selected`, `step_frames`, shortcuts), `timeline.py` (all constants and maths), `inspector.py` (`target_of`, `change_label`, titles), `media_list.py`, `preview_view.py` (`format_time`, `frame_number`, `SCREEN_BG`), `dialogs.py` (`LocateDialog`), `src/umc/videoedit/media.py` (`new_project`), `project.py` (the `opt()` fields).

## Global Constraints

- Work only in the worktree `G:\Claude\Universal-Media-Converter\.claude\worktrees\web-video-editor` (branch `web-video-editor`), with its `.venv` and `web/node_modules`.
- **Gate:** the editor is reachable only when the URL has `?e2e` or `?video-editor` (spec "Calls" 3). Normal visitors see today's Convert | Edit switch and today's "Coming soon" video tile. `EDITORS` keeps `available: false` for `"video"` and `src/umc/core/editors.py` keeps `"video"` in `WEB_LATER` (step 6 changes both).
- No new dependencies. The page bundle stays small: everything under `ui/videoeditor/` and `videoedit/` is reached only through a dynamic `import()` from `ui/app.ts`; Mediabunny is imported dynamically inside `sources.ts`.
- **Anti-goal:** a plain MP4 / MOV / MKV / WebM / MP3 / M4A / WAV / OGG / FLAC / PNG / JPG / WebP / GIF / BMP / AVIF project never loads FFmpeg.wasm. FFmpeg runs only for files that fail `limits.needsCopy(ext, decodes) === false`.
- Limits come from `videoedit/limits.ts` only (`LIMITS.pictureSide` 16000, `LIMITS.copyBytes` 1 GB, `sourceProblem`, `needsCopy`); e2e lowers them with `setLimitsForTests` through the hook. `MAX_LENGTH` (4 h) / `MAX_ITEMS` (500) are enforced by `edits.ts` (`EditError` / `ProjectTooLarge`) — their messages are shown inline, never swallowed (except a refused drag, which is cancelled silently like the desktop).
- Timeline numbers exactly as `timeline.py`: `HEADER_WIDTH 80`, `RULER_H 24`, `TEXT_H 28`, `MAIN_H 64`, `MUSIC_H 40`, `WAVE_H 16`, `EDGE 6`, `HANDLE 12`, `HANDLE_H 20`, `SNAP_PX 8`, `DRAG_PX 3`, `MIN_PPS 5`, `MAX_PPS 800`, `DEFAULT_PPS 80`, `ZOOM_STEP 1.25`, `FOLLOW_LEAD 0.05`; ruler steps `0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300, 600` (first with step · pps ≥ 60).
- Shortcuts as the desktop: Space (play — wired in step 4, no-op now), S split, Delete / Backspace, ← / → one frame, Shift+← / → 1 s, Home / End, Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z, Ctrl+S, Ctrl+Shift+S, Ctrl+E (export — step 5, no-op now), + / = zoom in, − zoom out, L loop (step 4). Keys are ignored while focus is in an `input` / `textarea` / `select`, except Ctrl+S and Ctrl+Shift+S.
- Undo labels as the desktop: "Add files", "Add clip", "Add music", "Add text", "Add row", "Split clip", "Trim clip", "Move clip", "Move text", "Move music", "Delete clip" / "Delete text" / "Delete music", "Remove transition", "Change <label lower>" / "Change settings" / "Change transition", "Fix missing files". `History` (`videoedit/history.ts`, 300 steps) is the only undo stack.
- Project files are `projectfile.ts` JSON (`projectToJson` / `projectFromJson`), extension `.umcvideo`. A source added in the browser is written with `path` = the file name; a source matched in the locate window keeps the project's original `path` / `relpath` (spec "Calls" 4).
- Colours: the clip tokens are copied from `gui/theme.py` into `styles.css` in all three blocks (`:root`, OS dark, `[data-theme="dark"]`): `--clip-video`, `--clip-picture`, `--clip-audio`, `--clip-text`, `--playhead` (light `#5b5ef0 #1f9d9d #1f9d61 #d98a1f #d43c45`, dark `#8385ff #2bb0b0 #3ccf8e #e8a040 #ff6b70`). The preview screen background is `SCREEN_BG = "#0b0c0f"` in both themes.
- Top bar: `backLabel(btn("← Back to Convert"))`, Undo, Redo, then `.top-tools` (spacer, "Save project", "Save project as…"), then "Export…" (primary; disabled with title "Export comes soon" in this step). Wide screens look like the desktop bar; on phones `.top-tools` is the second row.
- e2e hook `window.__videoeditor` only when `location.search` contains `e2e`.
- Commits: identity `darkbepo`, imperative subject, no trailers, no mention of AI. Don't push. Don't touch README in this step.

## Review Focus

1. **Hit testing where clips overlap** (a transition makes the next clip start before the previous ends): edges beat bodies, nearest edge wins, a tie goes to the earlier clip's right edge, the transition tab wins in the top `HANDLE_H` px — same answers as `timeline.py`. → Task 2 `hitTest` cases.
2. **The inspector's undo step**: a slider drag or typing is previewed live and becomes **one** history step on commit (native `change`, selection change, undo, save, leaving); an `EditError` mid-drag shows the message and restores the shown values. → Task 6.
3. **Still preview races**: the newest request wins (an older decode finishing late never paints), at most one decode chain runs, ~40 ms debounce while scrubbing, a project change re-renders the same time. → Task 7 `StillQueue`.
4. **Locate**: one multi-file pick fills every source it matches (name + size; name alone only when the project has no size, then the duration must agree within 0.5 s); unmatched sources are removed with a message; original `path` / `relpath` survive a re-save. → Task 8.
5. **The gate**: without `?e2e` / `?video-editor` nothing changes for visitors (Edit stays disabled for videos, the tile says "Coming soon", `.umcvideo` is detected but offers nothing); with `?e2e` alone the tile still says "Coming soon" (hub.spec unchanged). → Task 4.

---

### Task 1: Option schemas and a project from files (model)

**Files:**
- Create: `web/src/videoedit/schemas.ts`, `web/src/videoedit/start.ts`
- Modify: `scripts/make_videoedit_cases.py` (new section `schemas`; keep the other sections byte-identical), `tests/fixtures/videoedit_cases.json` (regenerated), `tests/test_videoedit_cases.py`
- Test: `web/tests/videoedit-schemas.test.ts`

**Interfaces:**
- Consumes: `opt`, `OptionSpec`, `Values` (`core/options.ts`); `TRANSITIONS`, `ANCHORS`, `FITS`, `ROTATIONS`, `find`, `newProject`, types (`project.ts`); `addSources`, `insertMain`, `addAt` (`edits.ts`).
- Produces:
  ```ts
  // schemas.ts
  export type Target = ProjectSettings | Transition | VideoClip | PictureClip | AudioClip | TextItem;
  export type TargetKind = "project" | "transition" | "video" | "picture" | "audio" | "text";
  export const TITLES: Record<TargetKind, string>; // "Project", "Transition", "Video clip", "Picture", "Music", "Text"
  export function schemaFor(kind: TargetKind): OptionSpec[];          // desktop opt() order, labels, ranges, units, show_if
  export function targetOf(p: Project, selectionId: string | null): { kind: TargetKind; target: Target }; // inspector.target_of
  export function valuesOf(kind: TargetKind, target: Target): Values;
  export function changedFields(shown: Values, now: Values): Record<string, unknown>; // inspector.changes
  export function changeLabel(kind: TargetKind, changes: Record<string, unknown>): string; // inspector.change_label
  // start.ts — media.new_project without probing
  export function projectFrom(sources: Source[]): Project; // videos / pictures → main in order; audio → music row 0 end to end
  export function nextSourceId(p: Project): string;          // "s<n>", first free, as add_paths
  ```
- `schemaFor("text")` keeps `font` as a `text` field (the web has no font box; see Task 6) and `text` as kind `"textarea"` (added in Task 6 to `core/options.ts`; this task adds `"textarea"` to the `Kind` union only).
- Python: the `schemas` section = for each of `ProjectSettings, Transition, VideoClip, PictureClip, AudioClip, TextItem`, `option_specs(cls)` as `[{name, label, kind, default, min, max, step, decimals, unit, choices}]` (choices as `[value, label]` pairs; `show_if` left out — it's code).

- [ ] **Step 1: tests.** Python `test_schemas_section`: six classes present, every entry has name / label / kind. vitest `videoedit-schemas.test.ts`:
  - `matches the desktop schemas` — for each class, `schemaFor` names, labels, kinds (`textarea` on both sides), defaults, min / max / step / decimals / unit and choices equal the fixture.
  - `custom size shows only for custom` — `visible(width, {resolution:"custom"})` true, `"1080p"` false.
  - `targetOf` — null → project settings; `"tr:<id>"` → that clip's `transition_in`; unknown id → throws.
  - `changeLabel` — `{volume: 50}` on a video → "Change volume"; two fields → "Change settings"; transition → "Change transition".
  - `changedFields` — only differing fields, unknown keys dropped.
  - `projectFrom` — [video, audio, picture, audio] → main = video, picture; music row 0 = two clips, the second starting at the first's duration (same as `new_project`).
  - `nextSourceId` — `s1, s2` taken → `s3`; a gap (`s1, s3`) → `s2`.
- [ ] **Step 2:** `.venv\Scripts\python -m pytest tests/test_videoedit_cases.py -q` and `npx vitest run tests/videoedit-schemas.test.ts` (in `web/`) → FAIL.
- [ ] **Step 3:** implement; regenerate with `.venv\Scripts\python scripts/make_videoedit_cases.py`; `git diff --stat` shows only the new section changed in the JSON.
- [ ] **Step 4:** both PASS; `ruff check .`, `ruff format --check .`, `npx tsc --noEmit` clean.
- [ ] **Step 5:** commit `Add the video editor's setting schemas to the website`.

### Task 2: Timeline maths and the shortcut table (DOM-free)

**Files:**
- Create: `web/src/ui/videoeditor/timelinemath.ts`, `web/src/ui/videoeditor/shortcuts.ts`
- Test: `web/tests/videoedit-timeline.test.ts`, `web/tests/videoedit-shortcuts.test.ts`

**Interfaces:**
- Consumes: `layout`, `Span`, `Project` (`project.ts`); `trim`, `moveMain`, `moveItem`, `addRow`, `EditError` (`edits.ts`).
- Produces (port of `timeline.py` minus painting; every constant from Global Constraints exported):
  ```ts
  export interface Metrics { header: number; ruler: number; textH: number; mainH: number; musicH: number; edge: number; handle: number; handleH: number }
  export const DESKTOP: Metrics;                                  // step 6 adds a phone set
  export interface Row { kind: "text" | "main" | "music"; row: number; y: number; h: number }
  export interface View { pps: number; scroll: number; width: number; playhead: number; m: Metrics }
  export type Part = "body" | "left" | "right" | "transition";
  export interface Hit { itemId: string; part: Part }
  export function rows(p: Project, m: Metrics): Row[];             // text rows, main, music rows (ruler isn't a row)
  export function totalHeight(rows: Row[]): number;
  export function xFor(v: View, t: number): number;               // Math.round, like x_for
  export function tFor(v: View, x: number): number;
  export function rowAt(rows: Row[], y: number): Row | null;
  export function spanRect(v: View, rows: Row[], s: Span): { x: number; y: number; w: number; h: number };
  export function addRowRect(rows: Row[], kind: "text" | "music", m: Metrics): { x: number; y: number; w: number; h: number };
  export function boundaryX(v: View, p: Project, spans: Map<string, Span>, index: number): number | null;
  export function hitTest(v: View, p: Project, spans: Map<string, Span>, rows: Row[], x: number, y: number): Hit | null;
  export function selectionFor(hit: Hit | null): string | null;    // "tr:<id>" for the transition tab
  export function snap(v: View, spans: Map<string, Span>, t: number, exclude: string, alt: boolean): number;
  export function snapMove(v: View, spans: Map<string, Span>, start: number, dur: number, exclude: string, alt: boolean): number;
  export interface Drag { id: string; part: Part; span: Span; ox: number; oy: number; x: number; y: number; moved: boolean; alt: boolean }
  export function dragMoved(d: Drag): boolean;                     // |dx| + |dy| ≥ DRAG_PX (manhattan)
  export function mainIndex(v: View, p: Project, spans: Map<string, Span>, d: Drag): number;
  export function dragResult(v: View, p: Project, spans: Map<string, Span>, rows: Row[], d: Drag): { project: Project; label: string } | null; // _compute; EditError → null; unchanged project → null
  export function ghost(v: View, p: Project, spans: Map<string, Span>, rows: Row[], d: Drag): { rect: {x:number;y:number;w:number;h:number}; insertX: number | null }; // _paint_ghost's maths
  export function zoom(v: View, factor: number, anchorX?: number): { pps: number; scroll: number };
  export function wheel(v: View, deltaY: number, ctrl: boolean, x: number): { pps: number; scroll: number }; // ctrl = zoom at x, else scroll delta/120·60/pps
  export function follow(v: View, t: number): number;             // new scroll for playback (step 4 uses it)
  export function rulerTicks(v: View): { x: number; label: string }[]; // "m:ss.s" below 1 s steps, else "m:ss"
  export function formatTime(t: number): string;                   // preview_view.format_time: "m:ss.cc"
  export function frameNumber(p: Project, t: number): number;      // floor(max(0,t)·fps + 1e-6)
  // shortcuts.ts
  export type ActionId = "play" | "split" | "delete" | "prev-frame" | "next-frame" | "back-second" | "forward-second" | "home" | "end" | "undo" | "redo" | "save" | "save-as" | "export" | "zoom-in" | "zoom-out" | "loop";
  export const SHORTCUTS: { id: ActionId; keys: string[]; label: string }[];
  export function actionFor(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }, inField: boolean): ActionId | null;
  ```
- Hit-test rule (copy, not invent): in the main row within `handleH` of its top, a boundary within `handle/2` → transition; then edges over every item of the row, `edge = min(EDGE, floor((x1 − x0)/3))`, distance + 0.5 when the pointer is outside the item on that side, nearest wins (strictly smaller), right edge checked before left; else the **last** body containing x.

- [ ] **Step 1: tests** (`videoedit-timeline.test.ts`; projects built with `edits.ts`, ids fixed with `setIdMaker`):
  - `rows` — 2 text rows, main, 1 music row → ys 24, 52, 80, 144; total 184.
  - `x/t round trip` — pps 80, scroll 1: `xFor(1) = 80`, `tFor(160) = 2`.
  - `hitTest` — two touching 2 s clips: x at the shared boundary → first clip's `right`; one px inside the second → second `left` only when nearer; a 0.5 s crossfade: the top 20 px at the boundary centre → `transition`, below it → the edge rule; mid body → `body` of the later clip where bodies overlap; header (x < 80) → null; a 10 px-wide clip has `edge = 3`.
  - `snap` — within 8 px of the playhead / 0 / another clip's edge snaps to the nearest; the dragged item's own edges are ignored; `alt` → unchanged.
  - `snapMove` — the end snaps when it is nearer than the start.
  - `dragResult` — left-edge trim of a main clip by +1 s → "Trim clip" and the in point moved; body drag of the first of three main clips past the third's centre → "Move clip", order b, c, a; text body drag to text row 1 → "Move text", row 1; a drag that changes nothing → null; a trim below `MIN_DURATION` → null (refused).
  - `ghost` — main body drag → `insertX` at the landing boundary; music drag shows the snapped start.
  - `zoom` — anchored zoom keeps `tFor(anchor)`; clamps to 5…800.
  - `rulerTicks` — pps 80 → 1 s steps "0:00", "0:01"…; pps 600 → 0.1 s steps "0:00.1".
  - `formatTime(65.4) = "1:05.40"`; `frameNumber` at 25 fps of 0.9999999 → 24 (the 1e-6 rule), 1.0 → 25.
  - `follow` — playhead past the view → scroll = t − span · 0.05; inside → unchanged.
- [ ] **Step 2:** `videoedit-shortcuts.test.ts`: every `ActionId` has at least one key; no key combination maps to two actions; `actionFor({key:"s"}, false) = "split"`, `({key:"s", ctrlKey:true}) = "save"`, `({key:"Z", ctrlKey:true, shiftKey:true}) = "redo"`, `({key:"="}) = "zoom-in"`; in a field only `save` / `save-as` come through.
- [ ] **Step 3:** run both → FAIL.
- [ ] **Step 4:** implement (line-by-line from `timeline.py`; no DOM types in either file).
- [ ] **Step 5:** PASS, `npx tsc --noEmit` clean; commit `Add the website video timeline's maths and shortcuts`.

### Task 3: `draw.ts` — a Frame and its text on any Canvas 2D context

**Files:**
- Create: `web/src/ui/videoeditor/draw.ts`, `web/src/ui/videoeditor/fonts.ts`
- Test: `web/tests/videoedit-draw.test.ts`

**Interfaces:**
- Consumes: `Frame`, `Placed`, `Layer`, `layerRect`, `gradeRgb`, `Grade` (`compose.ts`); `TextBox`, `FALLBACK_FONT` (`textlayout.ts`).
- Produces:
  ```ts
  export type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
  export interface Picture { image: CanvasImageSource; width: number; height: number } // a decoded source frame / bitmap
  export interface Surfaces { make(w: number, h: number): { canvas: CanvasImageSource; ctx: Ctx2D } } // OffscreenCanvas on page and in the worker; a fake in vitest
  export function drawFrame(ctx: Ctx2D, f: Frame, pictureOf: (l: Layer) => Picture | null, s: Surfaces): void;
  export function drawText(ctx: Ctx2D, boxes: TextBox[], scale: number): void; // scale = drawn height / output height (outline, padding)
  export function gradePixels(data: Uint8ClampedArray, g: Grade): void;  // in place, = gradeRgb per pixel
  export function liftPixels(data: Uint8ClampedArray, lift: number): void; // every RGB channel − lift, clamped at 0
  export function textAlign(column: 0 | 1 | 2): CanvasTextAlign;        // left / center / right
  export function lineTop(row: 0 | 1 | 2, y: number, lines: number, lineH: number): number; // top of the first line
  export const LINE_HEIGHT = 1.164; // DejaVu Sans ascent + descent per em (libass's line step)
  // fonts.ts
  export function fontAvailable(family: string, measure: (font: string) => number): boolean; // width differs from both generic fallbacks
  export function loadBundledFonts(): Promise<void>; // FontFace for DejaVu Sans regular / Bold / Oblique / BoldOblique from dejavu-fonts-ttf ?url, once
  export function hasFont(family: string): boolean;  // page: fontAvailable with a canvas measureText; cached per family
  ```
- `drawFrame` order (the contract tests check):
  1. Fill the whole frame with `f.background` (mode "over") or `#000` (mode "add").
  2. Per placed layer, first to last: on a scratch surface of frame size — fill background; if the layer has a picture: draw the cropped (`layer.crop` fractions of the picture), rotated (`rotate`, clockwise) and flipped (`flipH` after rotation, then `flipV`) picture into a second scratch at the destination scale; when `layer.grade` is set run `gradePixels` on that scratch (the grade applies to the picture only, not the letterbox bars — `_geometry` runs `eq` before `_fit`); draw it with `layerRect(layer, picture.width, picture.height, f.width, f.height)`; then fill the background at `globalAlpha = layer.fade`. A missing picture (source not found) leaves the background.
  3. Composite the scratch onto `ctx`: `globalAlpha = weight`, `globalCompositeOperation = "lighter"` in mode "add", else `"source-over"`; clip to `clipX` columns when set; translate by `offsetX`.
  4. If `f.lift > 0`: `getImageData` → `liftPixels` → `putImageData`.
  Text is drawn after (by the caller, `drawText`): per box `font = [italic] [bold] <fontPx·scale>px "<family>", "DejaVu Sans"`, `textAlign(column)`, `textBaseline = "top"`, lines at `lineTop(...) + i · round(fontPx·scale·LINE_HEIGHT)`; box (when set) = one rectangle per line padded by `padding · scale`, `box.colour` at `opacity · alpha`; else `strokeText` with `outlineColour`, `lineWidth = 2 · outlineWidth · scale` (stroke is centred), `lineJoin = "round"`, then `fillText` with `colour`; everything at `globalAlpha = alpha`.
- **Ruling (spec "Text", font check):** `document.fonts.check()` returns true for fonts that aren't installed in Chrome, so `hasFont` measures a test string in `"<family>", monospace` and `"<family>", serif` against plain `monospace` / `serif`; a family is present when either width differs. `FALLBACK_FONT` is always present once `loadBundledFonts` resolved.

- [ ] **Step 1: tests** (`videoedit-draw.test.ts`, node; a fake context that records `fillRect`, `drawImage`, `globalAlpha`, `globalCompositeOperation`, `save/restore`, `clip`, `rect`, `translate`, `rotate`, `scale`, `getImageData`/`putImageData`, `fillText`/`strokeText`, `font`, and a fake `Surfaces`):
  - `one layer` — background fill first; one scratch; drawImage with exactly the `layerRect` destination; final composite at alpha 1, source-over.
  - `crossfade` — two composites in clip order, the second at weight `b`.
  - `fadeblack` — black fill, both composites with `"lighter"`, then one get/putImageData pass.
  - `wipe` / `slide` — the second composite is clipped to `clipX`; slides translate by `offsetX` for both.
  - `fade` — a background fill at `globalAlpha = layer.fade` after the picture.
  - `grade only on the picture` — getImageData is called on the picture scratch (picture-sized), not the frame scratch.
  - `rotate 90` — the picture scratch is `h × w`.
  - `gradePixels` equals `gradeRgb` on 50 random pixels; alpha untouched. `liftPixels(…, 18.6)` on 10 → 0, on 200 → 181.
  - `drawText` — a bottom-centre two-line box: `textAlign "center"`, two `fillText` calls, the second line one line step lower, the last line's bottom at y; outline → `strokeText` before `fillText` with `lineWidth = 2 · outline · scale`; box → `fillRect` per line, no stroke; alpha 0.5 → `globalAlpha 0.5`.
  - `fontAvailable` — a measure that returns the same width for everything → false; a different width for the family → true.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** PASS, tsc clean; commit `Draw video project frames and text on a canvas`.

### Task 4: Gate, detection, switch, tile and an empty page

**Files:**
- Modify: `web/src/core/types.ts` (`Family` gains `"project"`), `web/src/core/detect.ts` (`PROJECT_EXTS = new Set(["umcvideo"])`, family "project" before the code fallback), `web/src/core/editors.ts` (gates), `web/src/ui/app.ts` (`editChoices`, `ensureVideo`, `enterEdit`, `openEditor`, `showConverter`, `enterProperties`), `web/src/ui/hub.ts` (optional per-tile override), `web/src/ui/icons.ts` / queue icon for "project" if the queue switches on family, `web/src/styles.css` (clip tokens ×3, `.video-editor` layout)
- Create: `web/src/ui/videoeditor/page.ts` (shell: top bar, empty areas, `show` / `hide` / `maybeLeave` / `openEmpty` / `open(files)` / `lastError`), `web/e2e/videoeditor.spec.ts`
- Test: `web/tests/core.test.ts` (detect + gate cases), `web/tests/theme.test.ts` (clip tokens exist), e2e

**Interfaces:**
- Produces:
  ```ts
  // core/editors.ts
  export function videoEditorGate(search: string): boolean; // /[?&](e2e|video-editor)(=|&|$)/
  export function videoTileGate(search: string): boolean;   // only video-editor (keeps hub.spec's ?e2e "Coming soon")
  // ui/videoeditor/page.ts
  export interface VideoEditorOptions { client: ConversionClient; onBack: () => void }
  export class VideoEditorPage { readonly el: HTMLElement; show(): void; hide(): void; maybeLeave(): boolean; openEmpty(): void; open(files: InputFile[]): Promise<boolean>; lastError(): string }
  export function mountVideoEditor(host: HTMLElement, opts: VideoEditorOptions): VideoEditorPage;
  // editorHub(onChoose, enabled?: (id: string) => boolean)
  ```
- `editChoices()` (only when `videoEditorGate(location.search)`): one file of family "project" → `["video"]`; else at least one video and every file video / image / audio → `["video"]`. The all-images / all-audio rules stay first. Title: "Open the files in the video editor". The disabled title text stays as it is for visitors (doceditor.spec checks it).
- `.umcvideo` with the gate off: detected as "project", no targets, Edit disabled (no family "project" converter exists, so `targetsFor` is empty — check the queue shows the usual "nothing to convert" note).
- Page layout (`.editor.video-editor`): top bar (Global Constraints), body grid `media (240 px) | preview (1fr) | inspector (300 px)`, timeline below (scrolls vertically, its own horizontal scroll via `View.scroll`), `.editor-status` with a message line. Below 900 px: preview, timeline, media, inspector stacked (step 6 turns media / inspector into sheets).
- **Ruling (tile):** the spec says the tile stays unavailable until step 6. `EDITORS` is unchanged; `editorHub` gets an `enabled` override and `app.ts` passes `(id) => id === "video" && videoTileGate(location.search)`, so the tile opens an empty project only with `?video-editor` (needed to test the empty-project path now); with `?e2e` alone it still says "Coming soon".

- [ ] **Step 1: tests.** vitest: `detect("x.umcvideo")` → family "project"; `videoEditorGate("?e2e")`, `("?a=1&video-editor")` true, `("?video-editors")`, `("")` false; `videoTileGate("?e2e")` false. theme.test: `every clip colour of theme.py is a web token` (keys `clip-*`, `playhead` present in light and dark blocks). e2e `videoeditor.spec.ts`:
  - `Edit opens a video + picture + music set` — `./?e2e`, add `clip.mp4`, `img0.png`, `tone.wav` → Edit enabled with the video title → `.video-editor` visible → "← Back to Convert" returns to the formats.
  - `without the gate nothing changes` — `./`, same files → Edit disabled; `.umcvideo` file → Edit disabled.
  - `the tile opens an empty project with ?video-editor only` — `./?e2e&video-editor` → `.editor-tile[data-editor=video]` enabled → editor with an empty timeline; `./?e2e` → "Coming soon".
  - `375 px` — viewport 375 × 812: no horizontal page scroll (`scrollWidth <= innerWidth`), "← Back" visible, Save project in the second row.
- [ ] **Step 2:** run `npx vitest run tests/core.test.ts tests/theme.test.ts` and `npx playwright test e2e/videoeditor.spec.ts -c playwright.edge.local.config.ts` → FAIL.
- [ ] **Step 3:** implement (`ensureVideo` as `ensureAudio`; `showConverter` / `enterProperties` hide / ask the video page like the others).
- [ ] **Step 4:** PASS; also `npx playwright test e2e/hub.spec.ts e2e/doceditor.spec.ts e2e/convert.spec.ts` (unchanged behaviour), tsc clean.
- [ ] **Step 5:** commit `Open the website video editor behind its test gate`.

### Task 5: Sources, the copy route and the media list

**Files:**
- Create: `web/src/ui/videoeditor/sources.ts`, `web/src/ui/videoeditor/media.ts`, `web/src/converters/videoedit.ts`
- Modify: `web/src/core/registry.ts` (register the hidden converter), `web/src/ui/videoeditor/page.ts` (open / add files), `web/e2e/global-setup.ts` (fixtures `red.mp4` 1 s 320×180 solid `#d02020` + silent audio, `blue.png` 64×36 `#2040d0`, `clip.avi` 1 s mpeg4 + mp3), `web/src/styles.css`
- Test: `web/tests/videoedit-sources.test.ts` (pure parts), e2e

**Interfaces:**
- Consumes: `needsCopy`, `sourceProblem`, `LIMITS` (`limits.ts`); `Source`; `projectFrom`, `nextSourceId` (Task 1); `addSources` (`edits.ts`); `Picture` (Task 3).
- Produces:
  ```ts
  // sources.ts
  export interface Opened { source: Source; file: File; copy: boolean; note: string | null } // note: "Converted for this browser — the desktop app reads it directly."
  export function sourceFromProbe(id: string, name: string, size: number, kind: Source["kind"], probe: { duration: number; width: number; height: number; fps: number; hasAudio: boolean }): Source; // pure; fps rounded to 3 decimals
  export function kindOf(ext: string): Source["kind"] | null; // limits' DIRECT_* sets + the copy-only types (avi, flv, wmv, heic, tiff, raw, psd, …)
  export class SourceBank {
    constructor(client: ConversionClient);
    open(file: File, id: string, onProgress: (f: number, msg: string) => void): Promise<Opened>; // throws ConversionError with the limits message / "Couldn't open “x”."
    has(id: string): boolean;
    picture(id: string, time: number, height: number): Promise<Picture | null>; // video: CanvasSink frame ≤ time at that height (sink per source + height); image: ImageBitmap cached once
    forget(id: string): void;  // relink / remove
    dispose(): void;           // leaving the page
  }
  // converters/videoedit.ts
  export const SOURCE_TARGET: Target; // { id: "source", ..., converterId: "video-edit-source" }
  export const videoEditSourceConverter: Converter; // option kind: "video" → MP4 H.264 CRF 12 + AAC 320k (thread limits from engines/threads.ts); "audio" → FLAC; "image" → PNG via ImageMagick
  // media.ts
  export class MediaList { readonly el: HTMLElement; constructor(on: { add(id: string): void; files(): void; text(): void }); set(sources: readonly Source[], notes: ReadonlyMap<string, string>): void }
  ```
- Open order (spec "Calls" 1): `kindOf` → for video / audio: Mediabunny `Input(BlobSource)`, primary tracks, `await track.canDecode()` for each present track; image: `createImageBitmap(file, { imageOrientation: "from-image" })` succeeds → decodes. `copy = needsCopy(ext, decodes)`. `sourceProblem({name, size, copy, width, height})` checked **before** any copy runs (and again for pictures after decoding, with their size). A copy runs `client.run(SOURCE_TARGET, …)` and the copy's `File` replaces the original for decoding; `Source.path` stays the original name, `Source.size` the original size (so the project matches the real file later).
- Video size in `Source` = display size (`displayWidth` / `displayHeight`, rotation applied), fps = `computePacketStats().averagePacketRate`, duration = `computeDuration()`; `CanvasSink` with `{ height, fit: "contain" }` and default rotation, so pictures arrive upright like the desktop's FFmpeg autorotate.
- Page: `open(files)` → `.umcvideo` handled in Task 8; else probe all (ids `s1…`), `projectFrom`, new `History`; failures listed in the status line ("Couldn't open …" / limits text), nothing usable → `lastError()`. A drop / "+ Add files" on an open project → `addSources` = "Add files" (one step); a `.umcvideo` among them → "Open “x” on its own to edit that project."
- Media rows: kind icon, name, detail (`"Picture"` | `formatTime(duration)` | `"Music · " + formatTime`), the note in muted text, `draggable` with `dataTransfer` type `application/x-umc-source`; double-click, Enter and a small "Add" button add at the playhead (touch has no drag-and-drop). Buttons "+ Add files" (file picker, `accept` = video/*, image/*, audio/*, .umcvideo) and "+ Text".

- [ ] **Step 1: tests.** vitest: `sourceFromProbe` rounds fps to 3 decimals and keeps size; `kindOf("MOV") = "video"`, `("heic") = "image"`, `("txt") = null`. e2e:
  - `media list shows each source` — clip.mp4, img0.png, tone.wav → three rows with "0:02.00", "Picture", "Music · 0:…"; timeline (hook) has 2 main items and 1 music item; FFmpeg was **not** loaded (`performance.getEntriesByType("resource")` has no `ffmpeg-core` / `.wasm` from @ffmpeg).
  - `an AVI goes through the copy` — add clip.avi → row note "Converted for this browser…", source duration ≈ 1 s.
  - `limits are shown` — hook `setLimits({ copyBytes: 1000 })`, add clip.avi → status "too big for the browser … use the desktop app", no row added.
  - `add at the playhead` — double-click the picture row → "Add clip" undo label, main count +1.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement (hook: `window.__videoeditor = { project(), history: {canUndo, undoLabel, dirty}, setLimits, playhead, selected, select(id), seek(t), still(): Promise<void>, pixel(x, y) }` — grow it in later tasks; `setLimits` calls `setLimitsForTests`).
- [ ] **Step 4:** PASS, tsc clean, `npm test` green; commit `Open video editor sources in the browser, copying only what it can't read`.

### Task 6: Timeline view, inspector and editing

**Files:**
- Create: `web/src/ui/videoeditor/timeline.ts`, `web/src/ui/videoeditor/inspector.ts`
- Modify: `web/src/core/options.ts` (`textarea` kind already in the union since Task 1), `web/src/ui/settings.ts` (render `textarea`; make the `text` kind's `set` work — today it's a no-op, which `setValues` needs), `web/src/ui/videoeditor/page.ts`, `web/src/styles.css`
- Test: `web/tests/settings-form.test.ts` (jsdom; new), e2e

**Interfaces:**
- Consumes: Task 2 (everything), Task 1 (`schemaFor`, `targetOf`, `valuesOf`, `changedFields`, `changeLabel`, `TITLES`), `edits.ts` (`addAt`, `addText`, `split`, `remove`, `update`, `applyChanges`, `addRow`, `NO_TRANSITION` from project.ts), `History`.
- Produces:
  ```ts
  export class TimelineView {
    readonly el: HTMLElement; // a <canvas class="ve-timeline" tabindex=0 aria-label="Timeline">
    constructor(on: { edit(p: Project, label: string): void; seek(t: number): void; select(id: string | null): void; drop(sourceId: string, t: number, kind: Row["kind"], row: number): void });
    setProject(p: Project): void; setPlayhead(t: number): void; setSelected(id: string | null): void;
    zoomIn(): void; zoomOut(): void; draw(): void;
    view(): View; // tests
  }
  export class Inspector {
    readonly el: HTMLElement;
    constructor(on: { change(changes: Record<string, unknown>): void; commit(): void });
    show(p: Project | null, selectionId: string | null): void; // same item → setValues (keeps focus), else render
    readonly selectionId: string | null;
  }
  ```
- Timeline: pointer events with pointer capture (mouse and touch alike); press on an add-row "+" → `addRow` "Add row"; ruler press / drag → seek; press on nothing → deselect + seek; on an item → select and start a `Drag`; move → `dragMoved`, ghost; up → `dragResult` → `edit`. Alt = no snapping. Wheel → `wheel()`. `dragover` / `drop` of `application/x-umc-source` → `drop(id, max(0, tFor(x)), row kind, row)` (page: music row only for audio, else main — `_source_dropped`). Painted per `timeline.py`: alternating row fills (`--surface` / `--surface-alt`), header labels ("Text", "Text 2", "Video", "Music"), rounded clips in `--clip-*` with the label (text items: their text, one line; others: the source name), text colour by fill lightness, selection outline `--text` 2 px, transition tabs (`--accent` when set or selected, else `--surface` + `--border`), ghost at 55 % (`--accent-soft`, dashed), insert line `--accent` 3 px, playhead `--playhead` 2 px. Canvas sized in device pixels from a ResizeObserver; height = `totalHeight(rows)`.
- Inspector: title (`TITLES` upper-cased), `SettingsForm` with `schemaFor(kind)`, id prefix `ve`. Every form `onChange` → `change(changedFields(shown, form.values()))`; a `change` event on the form element (bubbles from inputs: slider release, number / text commit, colour picked, checkbox, select) → `commit()`. For a text item a muted line under the Font field says "Not on this device — using DejaVu Sans." when `!hasFont(font)` (spec "Text").
- Page wiring (port of `editor_page.py`): `pending: {project, label} | null`; `change` → `applyChanges(history.project, selectionId, changes)` → pending + re-render (EditError → message, `inspector.show(base, sel)`, pending null); `flushPending()` pushes it — called by commit, selection change, undo / redo, save, any `apply`, leaving. `apply(fn, label)` = flush, run, catch `ConversionError` → inline message, push. Split target / message "Put the playhead inside a clip to split it.", delete rules and labels, seek clamps to `[0, length(shown)]`, step frames with `frameNumber`. Undo / Redo buttons disabled + titles "Undo <label> (Ctrl+Z)". Keyboard via `actionFor` on `document` while the page is shown (Task 2 rules).
- **Ruling (commit point):** the web `SettingsForm` has no `committed` signal; the native `change` event is the commit, which matches the desktop's "edit finished" for every field kind. Text typed in the Text box is previewed live and committed on blur / Ctrl+Enter is *not* added (Enter inserts a line, as on the desktop).

- [ ] **Step 1: tests.** `settings-form.test.ts` (jsdom): a `textarea` field renders a `<textarea>`, `values()` reads it, `setValues` writes it; `setValues` on a `text` field changes the input (regression for the no-op). e2e:
  - `split, trim, undo, redo` — clip.mp4 + img0.png; seek 1 s (hook) → press S → 3 main items, label "Split clip"; drag the picture's right edge +40 px (mouse) → its duration grows by 0.5 s (pps 80), "Trim clip"; Ctrl+Z twice → back to 2 items; Ctrl+Y → 3.
  - `reorder by dragging` — drag the first main clip past the second's centre → order swapped, "Move clip".
  - `inspector is one undo step` — select the picture (click its body) → title "PICTURE"; drag the Brightness slider across several values → `history` gains exactly one "Change brightness" step after release.
  - `transition tab` — click the boundary tab → title "TRANSITION"; choose "Crossfade" → main spans overlap by 0.5 s; Delete → "Remove transition".
  - `text` — "+ Text" → a text item at the playhead on text row 1, inspector shows the Text area; typing changes the timeline label.
  - `refused edits are shown` — hook builds a 500-item project, "+ Text" → the `MAX_ITEMS` message in the status line.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** PASS (also `npx vitest run` whole suite — `SettingsForm` is shared with the converter page), tsc clean.
- [ ] **Step 5:** commit `Edit video projects on the website timeline`.

### Task 7: Still preview with scrubbing

**Files:**
- Create: `web/src/ui/videoeditor/preview.ts`, `web/src/ui/videoeditor/stillqueue.ts` (DOM-free)
- Modify: `web/src/ui/videoeditor/page.ts`, `web/src/styles.css`
- Test: `web/tests/videoedit-stillqueue.test.ts`, e2e

**Interfaces:**
- Consumes: `frameAt`, `outputSize` (`compose.ts` / `project.ts`), `textAt` (`textlayout.ts`), `drawFrame`, `drawText`, `hasFont`, `loadBundledFonts` (Task 3), `SourceBank.picture` (Task 5), `formatTime`, `frameNumber`.
- Produces:
  ```ts
  // stillqueue.ts — newest wins, one at a time, debounced while requests come fast
  export const DEBOUNCE_MS = 40;
  export class StillQueue<J> {
    constructor(run: (job: J, isCurrent: () => boolean) => Promise<void>, timer?: { set(fn: () => void, ms: number): unknown; clear(h: unknown): void; now(): number });
    request(job: J): void; // no request within DEBOUNCE_MS before → start now; else (re)arm the timer
    idle(): Promise<void>; // tests / hook: resolves when nothing is pending or running
  }
  // preview.ts
  export function previewSize(box: { width: number; height: number }, dpr: number, out: [number, number]): [number, number]; // fit the output aspect in the box, device pixels, height ≤ 720, both ≥ 2, even
  export class Preview {
    readonly el: HTMLElement; // .ve-preview: <canvas> on SCREEN_BG + transport row (⏮ ▶ ⏭ disabled-play, time "m:ss.cc / m:ss.cc", Loop)
    constructor(bank: SourceBank, on: { step(n: number): void });
    show(p: Project, t: number): void; // StillQueue.request({p, t, size})
    setTime(t: number, total: number): void;
    idle(): Promise<void>;
  }
  ```
- One still = `frameAt(p, t, size)` → `Promise.all` of `bank.picture(layer.sourceId, layer.time, size[1])` (pictures for "picture" layers at `time` 0) → if `!isCurrent()` stop → `drawFrame` onto an offscreen surface → `drawText(textAt(p, t, size, hasFont), size[1] / outputSize(p)[1])` → copy to the visible canvas (so a half-drawn frame never shows). Fonts: `loadBundledFonts()` awaited before the first still that has text.
- The canvas is the frame (`previewSize`), centred in the `SCREEN_BG` box with CSS; the box is 16:9 at full width on phones.
- Re-render on: seek, every shown-project change (history or pending), selection-independent; ResizeObserver on the box.
- **Ruling (play button):** the transport row is built now (so the layout is final) but ▶ and Loop are disabled with title "Playback comes soon"; Space / L do nothing until step 4.

- [ ] **Step 1: tests.** `videoedit-stillqueue.test.ts` (fake timer + manual promises): a single request runs at once; three requests 10 ms apart → the first runs, then only the third (one more run, after 40 ms); a run whose job was superseded sees `isCurrent() === false`; two never run at the same time; `idle()` resolves after the last. `previewSize({width: 800, height: 600}, 2, [1920,1080])` → `[1280, 720]` (cap); `({375,211}, 3, [1080,1920])` → height ≤ 633 and aspect kept within 1 px.
- [ ] **Step 2:** e2e:
  - `the preview shows the frame under the playhead` — red.mp4 then blue.png (main), seek 0.5 → `pixel(centre)` ≈ (208,32,32) ± 12; seek 1.5 → ≈ (32,64,208) ± 12.
  - `scrubbing` — drag on the ruler from 0 to 1.5 s with 10 steps → after `still()` the pixel is blue and the playhead label reads "0:01.50 / 0:04.00" (red 1 s + picture 3 s).
  - `text shows` — "+ Text" at 0.5 s, `still()` → the bottom-centre area differs from the frame without text.
  - `grade` — picture Brightness +100 → its centre pixel brighter than before.
  - `past the end` — End key → background colour.
- [ ] **Step 3:** run → FAIL; implement; run → PASS; tsc clean.
- [ ] **Step 4:** commit `Show the frame under the playhead in the website video editor`.

### Task 8: Save and open projects, with the locate window

**Files:**
- Create: `web/src/ui/videoeditor/locate.ts` (window), `web/src/videoedit/locate.ts` (DOM-free glue)
- Modify: `web/src/ui/videoeditor/page.ts`, `web/src/styles.css`
- Test: `web/tests/videoedit-locate.test.ts`, e2e

**Interfaces:**
- Consumes: `projectToJson`, `projectFromJson` (`projectfile.ts`), `matchSources`, `PickedFile` (`match.ts`), `relink`, `removeSource` (`edits.ts`), `History.markSaved` / `dirty`, `SourceBank.open`.
- Produces:
  ```ts
  // videoedit/locate.ts
  export type Choice = { file: number } | null;                      // index into the picked files; null = remove
  export function applyPicks(sources: readonly Source[], files: PickedFile[], choices: Map<string, Choice>): Map<string, Choice>; // matchSources fills only sources still null
  export const DURATION_SLACK = 0.5;
  export function keepsDuration(original: Source, probed: Source): boolean; // original.size > 0, or |Δduration| ≤ 0.5 (0 duration = unknown → true)
  export function relinked(original: Source, probed: Source): Source; // probed facts + original id, path, relpath
  export function defaultProjectName(p: Project): string;            // "<first source stem>.umcvideo", else "video.umcvideo"
  // ui/videoeditor/locate.ts
  export function locateWindow(missing: readonly Source[]): Promise<Map<string, File | null> | null>; // null = Cancel
  ```
- Opening (`open([one .umcvideo])`, also from Edit and a drop): `projectFromJson(await file.text())` (ConversionError message inline); every source is missing on the web, so the window always opens when there are sources: intro text as the desktop's, a row per source ("“name”", status "Will be removed" / "Use “x”", "Find file…" (single picker), "Remove from project"), a "Pick several files…" button (multiple picker → `applyPicks`), buttons "Open project" / "Cancel" (Cancel → "Opening the project was cancelled.", page stays as before). On OK: open each chosen file with the bank; a probe failure or `!keepsDuration` → remove with "Couldn't use “x” for “y”." Then like `_open_project_file`: `History(project)` with the file's project, then `push(fixed, "Fix missing files")` when anything changed; messages joined in the status line.
- Saving: "Save project" = the remembered `FileSystemFileHandle` if any, else as "Save project as…"; "Save project as…" = `showSaveFilePicker({ suggestedName: defaultProjectName(p), types: [{ description: "Video project", accept: { "application/json": [".umcvideo"] } }] })` when available (handle remembered, Ctrl+S writes there again), else a download of that name. Both flush pending first, write `projectToJson(history.project)`, then `markSaved()`; the status says "Saved “name”.". A cancelled picker changes nothing.
- `maybeLeave()`: flush; dirty → `confirm("Discard unsaved changes to this video project?")` (the audio editor's pattern); also asked before `open()` replaces a dirty project.
- **Ruling (name-only matches):** `match.ts` accepts a unique name match when the project has no size; the spec's "fallback to name + duration" is applied after probing via `keepsDuration`, so a same-named but different file is refused instead of silently relinked.

- [ ] **Step 1: tests.** `videoedit-locate.test.ts`: `applyPicks` fills two of three sources from one pick and leaves the third null; a source already chosen by hand isn't overwritten; `keepsDuration` (size known → true; 10 s vs 10.3 s → true; vs 12 s → false); `relinked` keeps id / path / relpath and takes duration / width / size from the probe; `defaultProjectName` for "C:\\v\\holiday.mp4" → "holiday.umcvideo", none → "video.umcvideo".
- [ ] **Step 2:** e2e:
  - `save, reopen, locate, same timeline` — clip.mp4 + img0.png + tone.wav, split once, add a text; Save project as… (download path in Chromium headless: no picker → download) → read the JSON: format "umcvideo", version 1, source paths are the bare names with sizes; reload `./?e2e`, add the `.umcvideo` → Edit → locate window lists 3 files → "Pick several files…" with the three fixtures → all "Use “…”" → Open project → hook's project equals the saved one (ids, spans, text) and the undo label is "Fix missing files"; the preview shows a frame (pixel not background).
  - `remove a missing file` — reopen, pick only clip.mp4 → img0 and tone rows "Will be removed" → their clips are gone, message names them.
  - `cancel` — Cancel → message "Opening the project was cancelled.", converter page unchanged.
  - `unsaved changes are asked about` — edit, Back → `confirm` dialog handled (dismiss keeps the page, accept leaves).
  - `a desktop-saved project opens` — a hand-written fixture in `global-setup.ts` (`desktop.umcvideo`, desktop field order with absolute `C:\…` paths and `relpath`) opens and matches by name + size; re-saving keeps those `path` / `relpath` values.
- [ ] **Step 3:** run → FAIL; implement; run → PASS.
- [ ] **Step 4:** full gate: `npx tsc --noEmit`, `npm test`, `npx playwright test -c playwright.edge.local.config.ts` (all specs — the switch touches the convert page), `ruff check . && ruff format --check .`, `.venv\Scripts\python -m pytest tests/test_videoedit_cases.py tests/test_editor_hub.py -q`.
- [ ] **Step 5:** commit `Save and open video projects on the website`.

### Task 9: CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (roadmap 10.16, after the "Website: step 2 maths ✅" line)

- [ ] **Step 1:** add one line: `Website: step 3 page ✅ — behind ?video-editor / ?e2e (core/editors.ts videoEditorGate; the tile only with ?video-editor): ui/videoeditor/ (page, timeline + DOM-free timelinemath, media, inspector on SettingsForm, preview + stillqueue, draw.ts, sources.ts = Mediabunny + canDecode, else hidden converter "video-edit-source"), .umcvideo = family "project", locate window (videoedit/locate.ts + match.ts); e2e/videoeditor.spec.ts, hook window.__videoeditor.`
- [ ] **Step 2:** commit `Note the website video editor's page in the roadmap`.
