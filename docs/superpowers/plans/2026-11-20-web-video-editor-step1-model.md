# Website video editor — step 1: the model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A DOM-free TypeScript port of the desktop video editor's model (project types and timeline maths, edits, undo, `.umcvideo` read / write, matching picked files to a project's sources), checked against the same cases as the desktop, plus an optional `size` on the desktop's sources.

**Architecture:** `web/src/videoedit/` mirrors `src/umc/videoedit/` line by line, in the style of `web/src/audioedit/`. The data objects use the **same snake_case field names as the `.umcvideo` JSON** (`in_`, `fade_in`, `transition_in`, `has_audio` …) plus a `type` tag on items ("video" / "picture" / "audio" / "text"), so reading and writing a project is near-trivial. Functions are camelCase. Shared expectations come from `tests/fixtures/videoedit_cases.json`, written by a desktop script with deterministic ids and replayed by **both** test suites.

**Tech Stack:** TypeScript (strict), vitest; Python 3.11, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` (steps 1 of "Order of work", "Calls" point 4). The desktop model being ported: `src/umc/videoedit/project.py`, `edits.py`, `history.py`, `projectfile.py`.

## Global Constraints

- Work only in the worktree `G:\Claude\Universal-Media-Converter\.claude\worktrees\web-video-editor` (branch `web-video-editor`), with its own `.venv` (`.venv\Scripts\python`) and `web/node_modules`.
- No DOM, no Mediabunny, no WASM in `web/src/videoedit/` (vitest runs it in node).
- No new dependencies in either app.
- User-facing messages are copied **verbatim** from the desktop (`edits.py`, `projectfile.py`), e.g. `"This project is too big to edit (max 4 hours and 500 items)."`, `"Put the playhead inside the clip to split it."`, `"That spot is taken."`, `"This isn't a valid video project file."`, `"This project was made by a newer version of the app."`.
- Limits copied from the desktop: `MIN_DURATION = 0.1`, `MAX_LENGTH = 4 * 3600`, `MAX_ITEMS = 500`, `PICTURE_SECONDS = 3.0`, `TEXT_SECONDS = 3.0`, `MAX_STEPS = 300`, file `format` "umcvideo", `version` 1.
- `.umcvideo` stays version 1; `size` is optional (missing = 0 = unknown). Older files must keep opening in both apps.
- Commits: authored by the repo's existing identity (`darkbepo`), imperative subject, no trailers, no mention of AI. Don't push — landing on `main` happens after the ship gate and the owner's go-ahead.
- Nothing in this step is visible on the website (no UI yet).

## Review Focus

1. **A project file written by the other app** — the web writes `3` where Python wrote `3.0`, may omit `relpath`, adds `size`; the desktop writes no `size` in old files. Both apps must open both, and unknown keys are ignored, not fatal. → Task 1 step 1 (`test_loads_web_style_file`), Task 2 step 1 (`reads a desktop-written file`).
2. **Damaged or foreign `.umcvideo`** (not JSON, wrong `format`, `version` 2, an item with an unknown `type`, `main` not a list) → the plain "isn't a valid" / "newer version" message, never a raw `TypeError`. → Task 2 step 1 (`rejects damaged files`).
3. **Two picked files with the same name** (`clip.mp4` from two folders, different sizes) — must match by size, and an ambiguous name-only match must stay unmatched rather than guess. → Task 4 step 1 (`matchSources`).
4. **Edits mutating their input** — undo shares snapshots, so one changed array corrupts history silently. → Task 3 step 1 (`edits never change their input`).
5. **Floating-point drift between the apps** (transition caps rounded to 6 places, split points with speed) — results must agree within 1e-9, and ids must come out in the same order. → Task 3 (every fixture case compared with `nearlyEqual`, ids deterministic).

---

### Task 1: Desktop — optional `size`, and the shared cases file

**Files:**
- Modify: `src/umc/videoedit/project.py` (`Source`)
- Modify: `src/umc/videoedit/media.py` (`probe_source`)
- Modify: `src/umc/videoedit/projectfile.py` (`_find_source`: relative paths)
- Create: `scripts/make_videoedit_cases.py`
- Create: `tests/fixtures/videoedit_cases.json` (generated)
- Create: `tests/test_videoedit_cases.py`
- Modify: `tests/test_videoedit_files.py`

**Interfaces:**
- Produces: `Source.size: int = 0` (last field; bytes, 0 = unknown). `probe_source` fills it with `path.stat().st_size`. Saving and loading pick it up through `dataclasses.asdict` / `_build`; only `_find_source` changes (step 1).
- Produces: `tests/fixtures/videoedit_cases.json`:
  ```json
  {
    "layout": [{"name": str, "project": <umcvideo dict>, "spans": {"<id>": [start, end, row_kind, row, index]},
                "length": float, "output_size": [w, h], "output_fps": float}],
    "edits":  [{"name": str, "project": <umcvideo dict>, "op": str, "args": [...],
                "result": <umcvideo dict> | null, "id": str | null, "error": str | null}],
    "file":   {"text": "<what save_project writes for one sample project, sources in the project's folder>",
               "project": <umcvideo dict of the same project>}
  }
  ```
  `<umcvideo dict>` = exactly what `projectfile._to_dict(project, Path("."))` returns. Sources are named after their id (`v1.mp4`, `v2.mp4`, `p1.png`, `m1.mp3`), so `path` / `relpath` are those names.
  `op` uses the desktop function names (`insert_main`, `add_at`, `add_text`, `remove`, `move_main`, `move_item`, `trim`, `split`, `update`, `set_settings`, `add_row`, `relink`, `remove_source`, `apply_changes`); `args` are the arguments after `project` (for `update`, one dict of changes; for `set_settings`, the settings dict; for `relink`, `[source_id, <probed source dict>]`, path taken from the dict). For ops that return `(project, id)`, `id` holds the new id.
- Produces: deterministic ids — while a case runs, `umc.videoedit.edits.new_id` is replaced by a counter returning `"n1"`, `"n2"`, … (reset per case). The TS side does the same (Task 2 `setIdMaker`).

- [ ] **Step 1: Write the failing tests** in `tests/test_videoedit_files.py`:
  - `test_size_round_trips(tmp_path)`: a `Source(..., size=1234)` saved with `save_project` and loaded with `load_project` keeps `size == 1234`.
  - `test_old_file_without_size_loads(tmp_path)`: a file whose source entries have no `size` key loads with `size == 0`.
  - `test_loads_web_style_file(tmp_path)`: write next to empty files `v1.mp4` / `p1.png` a JSON string by hand in the web's style: floats as integers (`"duration": 10`, `"in_": 0`, `"out": 4`), `"size": 5`, no `relpath` key, `"path": "v1.mp4"` (a bare name, not absolute), an unknown key `"web_only": true` on a source and on an item. `load_project` returns no missing sources (bare names resolve against the project's folder), `layout` gives the clip 4.0 s, and nothing raises.
  - `test_probe_source_fills_size(vmedia)`: `probe_source` on a fixture video gives `size == path.stat().st_size`.

  Note for `test_loads_web_style_file`: today `_find_source` tries `Path(entry["path"])` (relative to the *current directory*) before `relpath`. Make a bare relative `path` resolve against the project's folder too — `path = Path(entry["path"]); if not path.is_absolute(): path = folder / path` — and fall back to `entry.get("relpath") or path.name`.

- [ ] **Step 2: Run them to see them fail**

  Run: `.venv\Scripts\python -m pytest tests/test_videoedit_files.py -q -k "size or web_style"`
  Expected: FAIL (`Source` has no `size`; relative path not found).

- [ ] **Step 3: Add `size: int = 0` to `Source`, fill it in `probe_source`, resolve relative paths in `projectfile._find_source`.**

- [ ] **Step 4: Run them to see them pass**

  Run: `.venv\Scripts\python -m pytest tests/test_videoedit_files.py tests/test_videoedit_model.py -q`
  Expected: all PASS.

- [ ] **Step 5: Write `scripts/make_videoedit_cases.py`** (docstring in the style of `scripts/make_photo_cases.py`: what it writes, who reads it, "run on purpose only, after changing project.py / edits.py / projectfile.py"). It builds the cases below, runs each against the desktop code with the id counter, and writes the JSON with `indent=1, ensure_ascii=False` and a trailing newline.

  Sources (shared by every case): `v1` video 10 s 1920×1080 30 fps with sound, `v2` video 4 s 1280×720 25 fps without sound, `p1` image 800×600, `m1` audio 60 s.

  Cases — at least these (names are the `name` field):
  - layout: `back_to_back`, `crossfade_overlap` (1 s fade between v1 and v2), `speed_2x`, `picture_3s`, `text_and_music_extend_length`, `settings_presets` (one per resolution choice incl. custom 1281×721 → even sizes), `fps_first_and_fixed`, `empty_project` (length 0, output 1920×1080 / 30 fps).
  - edits — one case per desktop test in `tests/test_videoedit_model.py` that exercises an edit (split video / with speed / keeps fades and transition on the right halves / outside raises; trim clamps to source / picture and text / start past end / end past start / music start; transition capped / first item has none / dropped when neighbour too short; move_item overlap raises / touching is fine; add_at audio free spot / video nearest boundary; insert_main image 3 s; add_text; remove_source; too many items / too long; update + unknown field / overlap for text and music; add_row, set_settings, relink; split text and music; apply_changes for settings, item, transition and its three errors) — same inputs and expectations as those tests.
  - file: one project with every item kind, a crossfade, and non-default values on every item; `text` = what `save_project` writes into a temp folder that also holds the sources (so `relpath` are bare names), with the absolute temp `path`s replaced by the bare names before storing.

- [ ] **Step 6: Generate the file and check it**

  Run: `.venv\Scripts\python scripts\make_videoedit_cases.py`
  Expected: `tests/fixtures/videoedit_cases.json` written; it contains ≥ 8 layout and ≥ 35 edit cases; at least 8 edit cases have `error` set.

- [ ] **Step 7: Write `tests/test_videoedit_cases.py`**: `test_cases_still_match_the_desktop` loads the JSON, replays every layout and edit case with the same id counter (rebuilding projects from the dicts through the same helpers `projectfile` uses) and asserts the stored result equals what the code produces now (floats with `pytest.approx(abs=1e-9)`). It fails when someone changes the model without regenerating the cases.

- [ ] **Step 8: Run it, plus lint**

  Run: `.venv\Scripts\python -m pytest tests/test_videoedit_cases.py tests/test_videoedit_files.py tests/test_videoedit_model.py -q; .venv\Scripts\ruff check .; .venv\Scripts\ruff format --check .`
  Expected: all PASS, lint clean.

- [ ] **Step 9: Commit**

  ```bash
  git add src/umc/videoedit/project.py src/umc/videoedit/media.py src/umc/videoedit/projectfile.py scripts/make_videoedit_cases.py tests/fixtures/videoedit_cases.json tests/test_videoedit_cases.py tests/test_videoedit_files.py
  git commit -m "Store source sizes in video projects and share the model's test cases"
  ```

---

### Task 2: Web — project types, timeline maths, project file

**Files:**
- Create: `web/src/videoedit/project.ts`
- Create: `web/src/videoedit/projectfile.ts`
- Test: `web/tests/videoedit-project.test.ts`

**Interfaces:**
- Consumes: `tests/fixtures/videoedit_cases.json` (Task 1), `ConversionError` from `web/src/core/types.ts`.
- Produces in `project.ts` (all readonly):
  ```ts
  export const TRANSITIONS: [string, string][]; ANCHORS; FITS; ROTATIONS: [number, string][]  // same lists as project.py
  export interface Transition { kind: string; duration: number }
  export interface Source { id: string; path: string; kind: "video" | "image" | "audio"; duration: number;
    width: number; height: number; fps: number; has_audio: boolean; size: number; relpath?: string | null }
  export interface VideoClip { type: "video"; id; source; in_; out; speed; volume; mute; fade_in; fade_out; fit; rotate;
    flip_h; flip_v; crop_left; crop_top; crop_right; crop_bottom; brightness; contrast; saturation; transition_in: Transition }
  export interface PictureClip { type: "picture"; id; source; duration; fit; rotate; flip_h; flip_v; fade_in; fade_out;
    brightness; contrast; saturation; transition_in: Transition }
  export interface AudioClip { type: "audio"; id; source; start; in_; out; volume; fade_in; fade_out; loop_until }
  export interface TextItem { type: "text"; id; text; start; end; anchor; x_offset; y_offset; font; size; bold; italic;
    colour; outline_colour; outline; box; box_colour; box_opacity; fade_in; fade_out }
  export type MainItem = VideoClip | PictureClip;
  export interface ProjectSettings { resolution: string; width: number; height: number; fps: string; background: string }
  export interface Project { settings: ProjectSettings; sources: Source[]; main: MainItem[]; text_rows: TextItem[][]; music_rows: AudioClip[][] }
  export interface Span { start: number; end: number; row_kind: "main" | "text" | "music"; row: number; index: number }
  export const DEFAULT_SETTINGS: ProjectSettings; export const NO_TRANSITION: Transition;
  export function videoClip(id: string, source: string, in_: number, out: number): VideoClip;   // desktop defaults
  export function pictureClip(id: string, source: string, duration?: number): PictureClip;
  export function audioClip(id: string, source: string, start: number, in_: number, out: number): AudioClip;
  export function textItem(id: string, start: number, end: number): TextItem;
  export function newProject(sources?: Source[]): Project;          // one empty text row, one empty music row
  export function newId(): string;                                  // 8 hex chars (crypto.getRandomValues)
  export function setIdMaker(make: (() => string) | null): void;    // tests: deterministic ids; null = back to random
  export function sourceOf(p: Project, id: string): Source;         // throws Error(id) like KeyError
  export function clipDuration(item: MainItem | AudioClip): number;
  export function layout(p: Project): Map<string, Span>;
  export function length(p: Project): number;
  export function find(p: Project, id: string): { kind: "main" | "text" | "music"; row: number; index: number; item: MainItem | TextItem | AudioClip };
  export function outputSize(p: Project): [number, number];
  export function outputFps(p: Project): number;                    // round(first.fps, 3) like Python
  ```
  Defaults equal `project.py`'s field defaults exactly (e.g. text `size` 8.0, `outline` 2.0, `box_opacity` 50, `font` "DejaVu Sans", `anchor` "bottom").
- Produces in `projectfile.ts`:
  ```ts
  export const FORMAT = "umcvideo", VERSION = 1;
  export function projectFromJson(text: string): Project;   // throws ConversionError with the desktop's messages
  export function projectToJson(p: Project): string;        // JSON.stringify(dict, null, 1) + no trailing newline, like save_project
  ```
  Reading builds every object from the defaults above plus the known keys of the entry (unknown keys dropped, like `_build`); `relpath` from the file is kept on the `Source`. Writing: each source's `path` as stored, `relpath` = `source.relpath ?? basename(path)` (a source added in the browser has `path` = its file name). Any structural problem (not an object, `main` not an array, unknown item `type`, missing `id`) → `ConversionError("This isn't a valid video project file.")`.

- [ ] **Step 1: Write the failing tests** in `web/tests/videoedit-project.test.ts`:
  - `describe("layout cases")`: `it.each(cases.layout)` — `projectFromJson(JSON.stringify(c.project))`, then `layout` spans, `length`, `outputSize`, `outputFps` match the case (numbers via a `nearlyEqual(a, b, 1e-9)` helper defined in the test file).
  - `it("reads a desktop-written file")`: `projectFromJson(cases.file.text)` deep-equals (nearly) `projectFromJson(JSON.stringify(cases.file.project))`, and its sources keep `relpath`.
  - `it("writes what the desktop reads")`: `JSON.parse(projectToJson(p))` for the file case has `format` "umcvideo", `version` 1, every item's `type`, and every source's `path`, `relpath` and `size`.
  - `it("round-trips")`: `projectFromJson(projectToJson(p))` deep-equals `p`.
  - `it("ignores unknown keys and fills missing ones")`: an entry with `"web_only": 1` and without `fade_in` reads with `fade_in` 0 and no `web_only`.
  - `it("rejects damaged files")`: `"{"`, `"[]"`, `{"format":"other"}`, `version` "1", an item `type` "sticker", `main: 5` → `"This isn't a valid video project file."`; `version: 2` → `"This project was made by a newer version of the app."`.

- [ ] **Step 2: Run them to see them fail**

  Run (in `web/`): `npx vitest run tests/videoedit-project.test.ts`
  Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `project.ts` and `projectfile.ts`** as above, porting `project.py` / `projectfile.py` line by line (`_even`, `_first_video`, `_PRESET_SIZES`). JSON import of the fixture: `import cases from "../../tests/fixtures/videoedit_cases.json"` (as `codeedit-*.test.ts` do).

- [ ] **Step 4: Run them to see them pass, plus the type check**

  Run (in `web/`): `npx vitest run tests/videoedit-project.test.ts; npx tsc --noEmit`
  Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

  ```bash
  git add web/src/videoedit/project.ts web/src/videoedit/projectfile.ts web/tests/videoedit-project.test.ts
  git commit -m "Port the video project model and file format to the website"
  ```

---

### Task 3: Web — edits

**Files:**
- Create: `web/src/videoedit/edits.ts`
- Test: `web/tests/videoedit-edits.test.ts`

**Interfaces:**
- Consumes: everything in Task 2's `project.ts` (ids through `newId()` so `setIdMaker` reaches them).
- Produces:
  ```ts
  export const MIN_DURATION = 0.1, MAX_LENGTH = 4 * 3600, MAX_ITEMS = 500, PICTURE_SECONDS = 3.0, TEXT_SECONDS = 3.0;
  export class EditError extends ConversionError {}
  export class ProjectTooLarge extends EditError {}         // message = the desktop's _TOO_LARGE
  export function normalize(p: Project): Project;
  export function addSources(p: Project, sources: Source[]): Project;
  export function insertMain(p: Project, index: number, sourceId: string): [Project, string];
  export function addAt(p: Project, sourceId: string, t: number, row?: number): [Project, string];
  export function addText(p: Project, t: number, row?: number): [Project, string];
  export function remove(p: Project, itemId: string): Project;
  export function moveMain(p: Project, itemId: string, newIndex: number): Project;
  export function moveItem(p: Project, itemId: string, start: number, row: number): Project;
  export function trim(p: Project, itemId: string, edge: "start" | "end", delta: number): Project;
  export function split(p: Project, itemId: string, t: number): Project;
  export function update(p: Project, itemId: string, changes: Record<string, unknown>): Project;
  export function setSettings(p: Project, settings: ProjectSettings): Project;
  export function addRow(p: Project, kind: "text" | "music"): Project;
  export function relink(p: Project, sourceId: string, probed: Source): Project;   // keeps sourceId, takes probed's path/size/…
  export function removeSource(p: Project, sourceId: string): Project;
  export function applyChanges(p: Project, selectionId: string | null, changes: Record<string, unknown>): Project;
  ```
  Same rules, order of id creation and messages as `edits.py` (split creates the left id, then the right). `update` / `applyChanges` treat "unknown" as any key not on the item (excluding `id`, `source`, `type`) — message `Unknown setting: a, b.` with sorted names. `round(x, 6)` = `Math.round(x * 1e6) / 1e6`.

- [ ] **Step 1: Write the failing tests** in `web/tests/videoedit-edits.test.ts`:
  - `it.each(cases.edits)("%s")` (by `name`): `setIdMaker` to a counter giving `"n1"`, `"n2"`, … reset per case; load the case project with `projectFromJson`; call the op (a small table maps the desktop name to the TS function and its `args`; `relink`'s second arg goes through the same source reader as `projectFromJson`); if `c.error` → expect it to throw `EditError` with exactly that message; else the result nearly-equals `projectFromJson(JSON.stringify(c.result))` and the returned id equals `c.id`.
  - `it("edits never change their input")`: for a project with every item kind, run split / trim / update / moveItem / applyChanges on `structuredClone`-compared input: the input still deep-equals its clone afterwards.
  - `afterEach(() => setIdMaker(null))`.

- [ ] **Step 2: Run them to see them fail**

  Run (in `web/`): `npx vitest run tests/videoedit-edits.test.ts`
  Expected: FAIL (module missing).

- [ ] **Step 3: Implement `edits.ts`** — a line-by-line port of `edits.py` (`_clamp_range`, `_normalize_*`, `_free_spot`, `_ensure_free` with `_EPS = 1e-6`, `_replace_item`); return new arrays / objects, never assign into the input.

- [ ] **Step 4: Run them to see them pass, plus the type check**

  Run (in `web/`): `npx vitest run tests/videoedit-edits.test.ts; npx tsc --noEmit`
  Expected: every fixture case PASSES; no type errors.

- [ ] **Step 5: Commit**

  ```bash
  git add web/src/videoedit/edits.ts web/tests/videoedit-edits.test.ts
  git commit -m "Port the video editor's edits to the website"
  ```

---

### Task 4: Web — undo history and matching picked files

**Files:**
- Create: `web/src/videoedit/history.ts`
- Create: `web/src/videoedit/match.ts`
- Test: `web/tests/videoedit-history.test.ts`
- Modify: `CLAUDE.md` (roadmap 10.16: one line "website: step 1 model ✅ — `web/src/videoedit/` ports, shared `tests/fixtures/videoedit_cases.json` (`python scripts/make_videoedit_cases.py`, on purpose only), `Source.size`")
- Modify: `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` (architecture block: `window.ts` is not ported — the canvas compositor works per frame, so there is no render window to cut; add `match.ts`)

**Interfaces:**
- Consumes: `Project`, `Source` (Task 2).
- Produces:
  ```ts
  export const MAX_STEPS = 300;
  export class History {                       // port of history.py
    constructor(project: Project);
    project: Project; version: number;
    push(project: Project, label: string): void;   // no-op when project === this.project
    undo(): void; redo(): void;
    readonly canUndo: boolean; readonly canRedo: boolean;
    readonly undoLabel: string; readonly redoLabel: string;
    readonly dirty: boolean; markSaved(): void;
  }
  // match.ts
  export interface PickedFile { name: string; size: number }
  /** For each source still missing, the index of the picked file that belongs to it. */
  export function matchSources(sources: Source[], files: PickedFile[]): Map<string, number>;
  ```
  `matchSources` rules (spec "Calls" point 4, name+duration fallback is done by the page in step 3 after probing): compare the source's file name (last part of `path`, `/` or `\` separated) to the picked name case-insensitively; with `size > 0` both name and size must match; with `size == 0` the name alone matches only when exactly one picked file has that name; one picked file is used for at most one source; ambiguous → left out.

- [ ] **Step 1: Write the failing tests** in `web/tests/videoedit-history.test.ts`:
  - `History`: the same scenarios as `test_history_undo_redo_dirty` and `test_history_cap` in `tests/test_videoedit_model.py` (push / undo / redo labels and `version`, `dirty` after save and after undo back past the save point, 301 pushes keep 300 undo steps, pushing the same object is a no-op).
  - `matchSources`: `clip.mp4` (size 10) and `clip.mp4` (size 20) from two folders, sources `C:\a\clip.mp4` size 20 and `/b/clip.mp4` size 10 → each gets the right index; `CLIP.MP4` matches `clip.mp4`; a source with size 0 and two picked `clip.mp4` → unmatched; a source with size 0 and one picked → matched; one picked file never serves two sources; a non-matching size → unmatched.

- [ ] **Step 2: Run them to see them fail**

  Run (in `web/`): `npx vitest run tests/videoedit-history.test.ts`
  Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `history.ts` and `match.ts`** as above.

- [ ] **Step 4: Run the whole step's checks**

  Run (in `web/`): `npx vitest run; npx tsc --noEmit`
  Run (worktree root): `.venv\Scripts\python -m pytest tests/test_videoedit_cases.py tests/test_videoedit_files.py tests/test_videoedit_model.py -q; .venv\Scripts\ruff check .; .venv\Scripts\ruff format --check .`
  Expected: all PASS; no type or lint errors.

- [ ] **Step 5: Update `CLAUDE.md` and the spec** as listed under Files.

- [ ] **Step 6: Commit**

  ```bash
  git add web/src/videoedit/history.ts web/src/videoedit/match.ts web/tests/videoedit-history.test.ts CLAUDE.md docs/superpowers/specs/2026-11-14-web-video-editor-design.md
  git commit -m "Add undo and file matching to the website video model"
  ```

---

## After the tasks

Run the ship gate (`/ship`: full `pytest`, `ruff`, `npx tsc --noEmit`, `npm test`; reviewer subagent on the branch diff; fix and re-run; commit identity check), then summarise for the owner and wait for the go-ahead before rebasing on `main` and pushing. `npm run e2e` is not affected by this step (no UI), but run it once if the gate's time allows. Step 2's plan (`compose`, `textlayout`, `mix`, `tempo`, `limits`) is written after this lands.
