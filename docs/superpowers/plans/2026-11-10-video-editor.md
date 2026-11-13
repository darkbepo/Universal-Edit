# Video Editor (desktop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**2026-11-13:** preview sound now uses Qt Multimedia's QAudioSink (bundled with the app since the viewer); `sounddevice` was removed. Mentions of `sounddevice` / "no QtMultimedia" below are the original plan.

**Goal:** A timeline video editor (cut & join, pictures + music, text, transitions & effects, preview with sound, export, project files) reached through the Convert | Edit switch when a video is added to the desktop app.

**Architecture:** A Qt-free model in `src/umc/videoedit/`: frozen dataclasses for the project (so undo keeps whole snapshots), pure edit functions, and a renderer that turns a project into one FFmpeg `filter_complex`. Export runs that recipe at full size (in groups for long projects). The preview runs it at 360 p for a short window from the playhead, as raw frames + raw PCM through pipes, with sound played by `sounddevice`. The Qt page in `src/umc/gui/videoedit/` paints the timeline, shows frames and edits the project only through `edits.py`.

**Tech Stack:** Python 3.11+, PySide6-Essentials, FFmpeg (bundled), Pillow, numpy, `sounddevice` (new, MIT), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-10-video-editor-design.md`. This plan covers the **desktop** editor (spec "Order of work" steps 1–5). The website (spec "Phase 2") gets its own spec and plan once this has landed.

## Global Constraints

- Work in the worktree `G:\Claude\Universal-Media-Converter\.claude\worktrees\video-editor` (branch `video-editor`). Before Task 1 run `scripts/setup_worktree.ps1 -Worktree <that folder>` and use only its `.venv`.
- Only `src/umc/gui/` imports Qt. `src/umc/videoedit/` must import without PySide6 and without `sounddevice` installed (import it lazily in `audio_out.py`).
- The converter and the image editor behave exactly as before. Pictures only (or one `.ora`) still opens the image editor.
- New dependency: `sounddevice>=0.5` only (MIT; Windows wheel bundles PortAudio, MIT).
- Limits: refuse a project longer than 4 h or with more than 500 items. Message: `"This project is too big to edit (max 4 hours and 500 items)."`
- Values pinned by the spec: speed 0.25–4; volume 0–200 %; fades 0–5 s; transition 0.1–3 s, at most half the shorter neighbour; picture duration 0.1–60 s (default 3 s); preview height 360 px; still cache 200 frames (LRU); undo cap 300 steps; `GROUP_SIZE = 16`; audio 48 kHz stereo; scrub debounce 40 ms; playback timer about 60 Hz; minimum window 1200 × 760.
- Transition names, in this order: `none`, `fade` (crossfade), `fadeblack`, `wipeleft`, `wiperight`, `slideleft`, `slideright` (FFmpeg `xfade` names).
- Temporary files: one `tempfile.TemporaryDirectory(prefix="umc-vedit-")` per open editor; export groups go into their own `umc-vedit-` temp folder.
- Never overwrite silently. Export writes `<dest stem>.part<ext>` next to `dest`, then `os.replace`; failure or cancel deletes the part file. A project save is atomic (temp file + `os.replace`).
- User-facing errors are plain-language `ConversionError` messages; details go to the log.
- Commits: on branch `video-editor`, author `darkbepo`, imperative subject, **no co-author or session trailers** (CLAUDE.md overrides the default). `git add` only the paths the task lists.
- Before every commit: `ruff check . && ruff format --check .` and the task's tests pass.
- **Landing** (marked "Land" below): `git rebase main` in the worktree, `git -C G:\Claude\Universal-Media-Converter merge --ff-only video-editor`, `git -C G:\Claude\Universal-Media-Converter push origin main`. Run the full `pytest -q` first.

## Review Focus

1. **Phone videos with rotation metadata** (portrait clips stored as landscape plus a 90° flag). FFmpeg auto-rotates when decoding, so "match first video" must use the rotated size. Test in Task 3 (`test_probe_source_uses_display_rotation`).
2. **Mixing clips with and without sound, and pictures.** The silence must be exactly as long as the picture, or the sound drifts out of sync after the first silent clip. Test in Task 5 (`test_render_mixed_audio_stays_in_sync`).
3. **Seeking into the middle of a transition, a fade-in, exactly onto a cut, or past the end of the main row.** The preview frame must match the export frame. Tests in Task 4 (`test_window_*`) and Task 5 (`test_window_render_matches_full_render`).
4. **Text and file names with quotes, braces, backslashes, emoji and non-Latin letters** (ASS escaping, paths in filters). Test in Task 6 (`test_text_escaping_and_unicode_path`).
5. **Editing or scrubbing while frames are still rendering, or while playing.** An old project version must never show up after a newer edit, and playback must stop on any edit. Tests in Task 10 (`test_scrub_newest_request_wins`) and Task 12 (`test_edit_stops_playback`).

---

## File structure

```
src/umc/videoedit/__init__.py
src/umc/videoedit/project.py      # dataclasses, find(), layout(), length(), output_size(), output_fps()
src/umc/videoedit/edits.py        # EditError, ProjectTooLarge, pure edits, normalize()
src/umc/videoedit/history.py      # History (snapshots, dirty, version)
src/umc/videoedit/media.py        # probe_source(), new_project()
src/umc/videoedit/projectfile.py  # save_project(), load_project(), MissingSource
src/umc/videoedit/window.py       # Window, window()
src/umc/videoedit/text_ass.py     # build_ass()
src/umc/videoedit/render.py       # RenderPlan, build_render()
src/umc/videoedit/export.py       # ExportOptions, run_export(), render_groups()
src/umc/videoedit/preview.py      # preview_size(), still(), StillCache, PlaybackStreams, thumbnails(), waveform()
src/umc/videoedit/audio_out.py    # AudioOut, SoundDeviceOut, NullAudioOut, open_output()
src/umc/gui/videoedit/__init__.py
src/umc/gui/videoedit/timeline.py      # TimelineView
src/umc/gui/videoedit/preview_view.py  # PreviewView, FrameService
src/umc/gui/videoedit/media_list.py    # MediaList
src/umc/gui/videoedit/inspector.py     # Inspector
src/umc/gui/videoedit/player.py        # Player
src/umc/gui/videoedit/dialogs.py       # LocateDialog, ExportDialog
src/umc/gui/videoedit/editor_page.py   # VideoEditorPage
Modify: src/umc/tools/ffmpeg.py (rotation in MediaInfo, ffmpeg_bytes, ffmpeg_popen)
Modify: src/umc/core/detect.py (MediaFamily.PROJECT, "umcvideo")
Modify: src/umc/core/options.py + src/umc/gui/widgets/settings_panel.py (kind "textarea")
Modify: src/umc/gui/main_window.py, src/umc/gui/theme.py, src/umc/gui/style.qss
Modify: src/umc/selftest.py, pyproject.toml, README.md, CLAUDE.md
Tests: tests/conftest.py (vmedia fixture, UMC_NO_AUDIO), tests/videoedit_helpers.py,
       tests/test_videoedit_model.py, test_videoedit_files.py, test_videoedit_window.py,
       test_videoedit_render.py, test_videoedit_export.py, test_videoedit_preview.py,
       tests/test_gui_videoedit.py
```

---

### Task 1: Project model and timeline maths

**Files:**
- Create: `src/umc/videoedit/__init__.py`, `src/umc/videoedit/project.py`
- Test: `tests/test_videoedit_model.py`

**Interfaces:**
- Produces (all `@dataclass(frozen=True)`; editable fields use `opt()` so the inspector can render them; `id`, `source`, `start` of main items and similar internal fields have no `opt` metadata):
  - `ProjectSettings`: `resolution: str = opt("first", …)` choices `first`, `720p`, `1080p`, `4k`, `vertical` (1080×1920), `square` (1080×1080), `custom`; `width: int = 1920`, `height: int = 1080` (shown for custom); `fps: str = opt("first", …)` choices `first`, `24`, `25`, `30`, `50`, `60`; `background: str = opt("#000000", kind="color")`.
  - `Source(id: str, path: Path, kind: Literal["video","image","audio"], duration: float, width: int, height: int, fps: float, has_audio: bool)`.
  - `Transition(kind: str = "none", duration: float = 0.5)`.
  - `VideoClip(id, source, in_: float, out: float, speed=1.0, volume=1.0, mute=False, fade_in=0.0, fade_out=0.0, fit="letterbox", rotate=0, flip_h=False, flip_v=False, crop_left=0, crop_top=0, crop_right=0, crop_bottom=0, brightness=0, contrast=0, saturation=0, transition_in: Transition = Transition())`.
  - `PictureClip(id, source, duration=3.0, fit, rotate, flip_h, flip_v, fade_in, fade_out, brightness, contrast, saturation, transition_in)` (same field names and defaults as `VideoClip`).
  - `AudioClip(id, source, start: float, in_: float, out: float, volume=1.0, fade_in=0.0, fade_out=0.0, loop_until: float = 0.0)` (0 = no loop; otherwise the clip repeats until that timeline time).
  - `TextItem(id, text="Text", start=0.0, end=3.0, anchor="bottom", x_offset=0.0, y_offset=0.0, font="DejaVu Sans", size=8.0, bold=False, italic=False, colour="#ffffff", outline_colour="#000000", outline=2.0, box=False, box_colour="#000000", box_opacity=0.5, fade_in=0.0, fade_out=0.0)`. Anchors: `top-left, top, top-right, left, center, right, bottom-left, bottom, bottom-right`. `size` = % of frame height; offsets = % of width / height.
  - `MainItem = VideoClip | PictureClip`.
  - `Project(settings, sources: tuple[Source, ...], main: tuple[MainItem, ...] = (), text_rows: tuple[tuple[TextItem, ...], ...] = ((),), music_rows: tuple[tuple[AudioClip, ...], ...] = ((),))`; `source(id) -> Source`.
  - `new_id() -> str` (`uuid4().hex[:8]`).
  - `clip_duration(item: MainItem | AudioClip) -> float`: `(out - in_) / speed` for video, `duration` for pictures, `out - in_` for audio (or `loop_until - start` when looping).
  - `Span(start: float, end: float, row_kind: Literal["main","text","music"], row: int, index: int)`.
  - `layout(project) -> dict[str, Span]` keyed by item id. Main item *i* starts at `end(i-1) - transition_in(i).duration` (item 0 at 0; the transition of item 0 is ignored).
  - `length(project) -> float` = max of every span end (0 for an empty project).
  - `find(project, item_id) -> tuple[str, int, int, object]` (row_kind, row, index, item); `KeyError` if missing.
  - `output_size(project) -> tuple[int, int]` (even numbers; "first" = first *video* source's size, else 1920×1080) and `output_fps(project) -> float` ("first" = first video's fps rounded to 3 decimals, else 30).

- [ ] **Step 1: Write failing tests:**
  - `test_layout_back_to_back` (two 2 s clips → spans 0–2 and 2–4; `length == 4`).
  - `test_layout_transition_overlaps` (second has `Transition("fade", 0.5)` → second starts at 1.5, `length == 3.5`).
  - `test_layout_speed_and_picture` (video in 1, out 5, speed 2 → 2 s; picture 3 s after it → 2–5).
  - `test_length_includes_text_and_music` (main ends at 2, text ends at 6 → 6).
  - `test_output_size_presets` (`vertical` → (1080, 1920); `custom` 1281×721 → (1280, 720); `first` with a 320×240 video → (320, 240)).
  - `test_items_are_frozen` (`dataclasses.FrozenInstanceError` on assignment) and `test_option_specs_cover_inspector_fields` (`option_specs(VideoClip)` names include `speed`, `volume`, `fit`, `brightness`; exclude `id`, `source`).
- [ ] **Step 2:** `pytest tests/test_videoedit_model.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `project.py` as listed.
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add the video editor project model`.

---

### Task 2: Edits and undo history

**Files:**
- Create: `src/umc/videoedit/edits.py`, `src/umc/videoedit/history.py`
- Test: `tests/test_videoedit_model.py` (append)

**Interfaces:**
- Consumes: Task 1.
- Produces in `edits.py` (every function returns a new `Project` that went through `normalize`, and never changes its input):
  - `class EditError(ConversionError)`, `class ProjectTooLarge(EditError)` (message from Global Constraints).
  - `MIN_DURATION = 0.1`.
  - `normalize(project) -> Project`: item 0's `transition_in` → none; every transition duration clamped to `[0.1, 3]` and to half the shorter neighbour; in/out clamped to `[0, source.duration]` with `out - in_ >= MIN_DURATION * speed`; speed, volume, fades and picture duration clamped to the ranges in Global Constraints; raises `ProjectTooLarge` when `length > 4 * 3600` or items > 500.
  - `add_sources(project, sources: Sequence[Source]) -> Project`.
  - `insert_main(project, index: int, source_id: str) -> tuple[Project, str]` (video → whole source; image → 3 s picture). Returns the new item's id.
  - `add_at(project, source_id, t: float, row: int = 0) -> tuple[Project, str]`: video/image → `insert_main` at the main boundary nearest `t`; audio → music row `row` at start `t`, placed on the first free spot from `t` in that row.
  - `add_text(project, t: float, row: int = 0) -> tuple[Project, str]` (3 s, "Text").
  - `remove(project, item_id)`; `move_main(project, item_id, new_index)`.
  - `move_item(project, item_id, start: float, row: int)` (text / music; `EditError("That spot is taken.")` if it overlaps another item in that row; start ≥ 0).
  - `trim(project, item_id, edge: Literal["start","end"], delta: float)` (delta in timeline seconds). Video start edge: `in_ += delta * speed`. Picture: duration. Music start edge: `start += delta; in_ += delta`. Text: start / end. Clamped by `normalize`.
  - `split(project, item_id, t: float) -> Project` (`t` = timeline time strictly inside the item, else `EditError("Put the playhead inside the clip to split it.")`). Both halves get new ids. The first keeps `fade_in` and `transition_in`. The second keeps `fade_out` and has no transition.
  - `update(project, item_id, **changes)` (unknown field → `EditError`); `set_settings(project, settings)`; `add_row(project, kind: Literal["text","music"])`.
  - `relink(project, source_id, path: Path, probed: Source)` and `remove_source(project, source_id)` (also removes every item using it).
- Produces in `history.py`: `History(project)` with `.project`, `.version: int` (+1 on every push / undo / redo), `push(project, label: str)` (no-op if `project is self.project`), `undo()`, `redo()`, `can_undo`, `can_redo`, `undo_label`, `redo_label`, `dirty`, `mark_saved()`, cap `MAX_STEPS = 300` (oldest dropped).

- [ ] **Step 1: Write failing tests:**
  - `test_split_video_clip` (0–4 s clip split at 1.5 → two clips, `in_/out` (0, 1.5) and (1.5, 4), new ids, total length unchanged).
  - `test_split_with_speed` (speed 2, in 0 out 4 → split at timeline 0.5 → source split at 1.0).
  - `test_split_outside_raises`.
  - `test_trim_clamps_to_source` (trim start by −5 s → `in_ == 0`; trim end past the source → `out == duration`).
  - `test_transition_capped_to_half_shorter` (neighbours 1 s and 4 s, transition 3 s → 0.5).
  - `test_first_item_has_no_transition` (`move_main` a clip with a transition to index 0 → its transition is none).
  - `test_move_item_overlap_raises`; `test_add_at_audio_finds_free_spot`.
  - `test_remove_source_removes_items`; `test_project_too_large` (an image added 501 times → `ProjectTooLarge`).
  - `test_history_undo_redo_dirty` (push, push, undo → previous project, `dirty` True; `mark_saved` → False; redo → `dirty` True; version increases on every step).
  - `test_history_cap` (305 pushes → 300 undo steps).
  - `test_edits_do_not_mutate_input` (`p` before == `p` after for each edit).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add video editor edits and undo history`.

---

### Task 3: Sources, project files and `.umcvideo` detection

**Files:**
- Create: `src/umc/videoedit/media.py`, `src/umc/videoedit/projectfile.py`
- Modify: `src/umc/tools/ffmpeg.py` (`MediaInfo.rotation: int = 0`, read from the video stream's `side_data_list[*].rotation` or `tags.rotate`), `src/umc/core/detect.py` (`MediaFamily.PROJECT = "project"`, `PROJECT_EXTS = {"umcvideo"}` checked in `family_of_ext`)
- Modify: `tests/conftest.py`: add `os.environ.setdefault("UMC_NO_AUDIO", "1")`, and a session fixture `vmedia` (needs ffmpeg) that writes:
  - `red.mp4`: 2 s, 320×240, 30 fps, `color=red`, 440 Hz sine;
  - `blue.mp4`: 2 s, same size, `color=blue`, **no audio**;
  - `green_tall.mp4`: 1 s, 240×320, with sound;
  - `rot.mp4`: `red.mp4` copied with `-display_rotation 90`;
  - `pic.png`: 200×100 orange;
  - `music.wav`: 4 s, 880 Hz, stereo.
- Test: `tests/test_videoedit_files.py`

**Interfaces:**
- Consumes: Tasks 1–2, `ffmpeg.probe`, `slideshow.oriented_size`.
- Produces:
  - `probe_source(path: Path, source_id: str) -> Source` (kind from `detect.family_of_ext`; images: duration 0 and `oriented_size`; video width / height swapped when `rotation` is ±90; raises `ConversionError` for files FFmpeg can't read).
  - `new_project(paths: Sequence[Path]) -> tuple[Project, list[str]]`: source ids `s1`, `s2`, …. Videos and images go on the main row in order; audio goes on music row 0, one after another from 0 s. Unreadable files are skipped, with the message `Couldn't open “name”.` in the list. A path ending in `.umcvideo` is loaded with `load_project` instead (it must be the only path).
  - `FORMAT = "umcvideo"`, `VERSION = 1`; `save_project(project, path: Path)` (JSON, UTF-8, indent 1; each source stores `path` (absolute) and `relpath` (relative to the project folder, or `null` on another drive); items are tagged `"type": "video"|"picture"|"audio"|"text"`; atomic write).
  - `@dataclass MissingSource(source_id: str, path: Path)`.
  - `load_project(path) -> tuple[Project, list[MissingSource]]`: a source is found by `path`, then by `relpath` resolved against the project folder. A newer `version` raises `ConversionError("This project was made by a newer version of the app.")`. Bad JSON raises `ConversionError("This isn't a valid video project file.")`.

- [ ] **Step 1: Write failing tests:**
  - `test_new_project_puts_files_on_rows` (red, pic, music → main = [VideoClip, PictureClip], music row 0 = [AudioClip]).
  - `test_new_project_skips_bad_file` (a `bad.mp4` holding text → message names it, the other files still load).
  - `test_probe_source_uses_display_rotation` (`rot.mp4` → width 240, height 320).
  - `test_project_round_trip` (save → load → equal `Project`, no missing sources).
  - `test_load_finds_moved_folder_by_relpath` (save project + sources in `a/`, move the folder to `b/` → no missing sources, paths point into `b/`).
  - `test_load_reports_missing`, `test_load_newer_version_refused`, `test_load_garbage_refused`.
  - `test_detect_umcvideo_family` (`detect(p).family == MediaFamily.PROJECT`; `targets_for` offers no video / image targets for it).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.** Also grep for code that maps every `MediaFamily` member (labels, icons, accepted-families checks in `main_window.add_files`) and make `.umcvideo` accepted rather than "unsupported".
- [ ] **Step 4:** PASS, plus the existing `pytest tests/test_detect*.py tests/test_gui*.py -q` still green.
- [ ] **Step 5: Commit** `Add video project files and source probing`. **Land** (spec step 1).

---

### Task 4: Render windows (the preview's slice of the timeline)

**Files:**
- Create: `src/umc/videoedit/window.py`
- Test: `tests/test_videoedit_window.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `@dataclass(frozen=True) Window(project: Project, lead: float, duration: float)` and `window(project, start: float, end: float) -> Window`. Rendering `Window.project`, dropping `lead` seconds and keeping `duration` seconds gives exactly the timeline from `start` to `end`. Rules:
  1. First kept main item `f` = the last one with `span.start <= start` (or item 0).
  2. If `start` falls inside `f`'s incoming transition (`start < span_f.start + f.transition_in.duration`) and `f > 0`, also keep `f - 1`, cut so that only its overlap with `f` remains.
  3. The first kept item is cut at `cut = window_start - span.start`, converted to source time for video (`in_ += cut * speed`) or a shorter duration for pictures. **If `cut` is inside its `fade_in`, it isn't cut** (`cut = 0`): the extra goes into `lead`, so fades stay exact. The first kept item's transition becomes none.
  4. Items starting at or after `end` are dropped, except one whose transition starts before `end`.
  5. Text and music items overlapping `[window_start, end]` are kept and shifted by `-window_start`. Their `start` **may become negative**; the renderer and `build_ass` handle that exactly (Tasks 5–6).
  6. `duration = end - start`; `lead = start - window_start`.
- `window` does **not** call `normalize` (negative music / text starts are allowed here only).

- [ ] **Step 1: Write failing tests:**
  - `test_window_on_cut` (two 2 s clips, start 2.0 → only clip 2, `lead == 0`).
  - `test_window_inside_clip` (start 1.0 → clip 1 `in_ == 1.0`, `lead == 0`).
  - `test_window_inside_transition` (crossfade 0.5, start 1.6 → keeps both, the first cut to its last 0.5 s, `lead == pytest.approx(0.1)`).
  - `test_window_inside_fade_in_not_cut` (fade_in 1.0, start 0.4 → `in_ == 0`, `lead == 0.4`).
  - `test_window_past_main_end` (main ends at 4, text 0–6, window 5–5.5 → main empty, text `start == -5`, `end == 1`, `duration == 0.5`).
  - `test_window_shifts_music_negative` (music 0–4, start 1 → music `start == -1`).
  - `test_window_drops_items_after_end`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add timeline windows for the preview`.

---

### Task 5: Renderer — main row, sound and music

**Files:**
- Create: `src/umc/videoedit/render.py`, `tests/videoedit_helpers.py`
- Test: `tests/test_videoedit_render.py`

**Interfaces:**
- Consumes: Tasks 1, 4; `converters.video.atempo_chain`; `imaging.open_image`, `imaging.flatten`; `ImageOps.exif_transpose`.
- Produces:
  - `@dataclass RenderPlan(args: list[str], video_label: str | None, audio_label: str | None, total: float, cwd: Path)`. `args` = inputs + `-filter_complex <graph>`. The caller adds `-map`, codecs and the output. Labels are `"[vout]"` / `"[aout]"`.
  - `build_render(project, tmp: Path, *, size: tuple[int,int] | None = None, video=True, audio=True, lead=0.0, duration: float | None = None) -> RenderPlan` (size defaults to `output_size`; fps = `output_fps`; W/H/fps/bg below).
- Per main item *i* (filters joined with `,`, all numbers `:.3f`):
  - Video input: `-ss {in_} -t {out-in_} -i path`. Picture input: a PNG written once per source to `tmp/pics/<id>.png` (exif-transposed, flattened on `bg`), `-loop 1 -framerate {fps} -t {dur} -i png`.
  - Chain, in this order:
    1. `setpts=(PTS-STARTPTS)/{speed}`;
    2. crop `crop=iw-{l}-{r}:ih-{t}-{b}:{l}:{t}` (if any);
    3. rotate (90 → `transpose=1`, 180 → `hflip,vflip`, 270 → `transpose=2`), then `hflip` / `vflip`;
    4. `eq=brightness={b/200}:contrast={1+c/100}:saturation={1+s/100}` (if any non-zero);
    5. fit: letterbox `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2:color={bg}`; crop `scale=W:H:force_original_aspect_ratio=increase,crop=W:H`; stretch `scale=W:H`;
    6. `setsar=1,fps={fps},format=yuv420p`;
    7. `tpad=stop_mode=clone:stop_duration={dur},trim=duration={dur}` (exact length);
    8. `fade=t=in:d={fi}:color={bg}`, `fade=t=out:st={dur-fo}:d={fo}:color={bg}`;
    9. `settb=AVTB` → `[v{i}]`.
  - Join pairwise: `[acc][v{i}]xfade=transition={kind}:duration={td}:offset={span_i.start}` when a transition is set, else `concat=n=2:v=1:a=0`.
  - Sound per main item: `[k:a]asetpts=PTS-STARTPTS,{atempo chain},volume={v}` (only when the source has audio and the clip isn't muted), else `anullsrc=r=48000:cl=stereo`. Then `aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={dur}`, plus `afade` in / out. Joined with `acrossfade=d={td}:c1=tri:c2=tri` or `concat=n=2:v=0:a=1`.
  - Empty main row: `color=c={bg}:s=WxH:r={fps}:d={total}` and `anullsrc`.
  - Tail past the main row: `tpad=stop_mode=add:stop_duration={total-main_end}:color={bg}`; sound `apad=whole_dur={total}`.
  - Music clip: input `-ss {in_} -t {out-in_}` (with `-stream_loop -1` before `-i` when `loop_until`, then `atrim=duration={loop_until-start}`). Chain: `asetpts=PTS-STARTPTS,volume,afade…`. If `start < 0`: `atrim=start={-start},asetpts=PTS-STARTPTS` **after** the fades, and no delay. Otherwise `adelay={ms}:all=1`.
  - Mix: `[amain][m…]amix=inputs=n:normalize=0:duration=longest,alimiter=limit=0.95,atrim=duration={total}`.
  - Final: when `lead` or `duration` is set, `trim=start={lead}:duration={duration},setpts=PTS-STARTPTS` (and `atrim` / `asetpts` for sound). `total` = `duration` or `length(project)`.
  - Text is added in Task 6. Leave a single hook: the video chain ends with `{text_filter}` before `[vout]`.
- `tests/videoedit_helpers.py`:
  - `render_to(project, dest, tmp, **kw) -> Path` (`build_render` + libx264 `-crf 18 -preset veryfast` + aac → `ffmpeg.run_ffmpeg`);
  - `frame_at(path, t) -> PIL.Image`;
  - `rms(path, start, end) -> float` (`subprocess.run` of ffmpeg decoding that slice to s16le mono on stdout; RMS with numpy, 0–1);
  - `durations(path) -> tuple[float, float]` (video, audio stream durations via ffprobe).

- [ ] **Step 1: Write failing tests** (`needs_ffmpeg`, using `vmedia`):
  - `test_render_join_two_clips` (red + blue → duration 4 ± 1 frame; frame at 0.5 mostly red, at 3.5 mostly blue).
  - `test_render_crossfade_mixes` (`fade` 1 s → length 3; frame at 1.5 has R and B both within 128 ± 45).
  - `test_render_speed_and_trim` (red in 0.5 out 1.5 speed 2 → 0.5 s).
  - `test_render_letterbox_tall_clip` (project 320×240, `green_tall` letterboxed → pixel (5, 120) equals the background, the centre is green).
  - `test_render_mixed_audio_stays_in_sync` (red + pic + blue; audio duration == video duration ± 0.05; `rms` during red > 0.05, during pic < 0.01).
  - `test_render_music_volume_and_fade` (music at 0.5 vs 1.0 → RMS ratio 0.5 ± 0.1; fade-out 1 s → last 0.3 s RMS < 30 % of the middle).
  - `test_render_text_only_project` (empty main row, text item → background-coloured video of the text's length).
  - `test_window_render_matches_full_render` (red → `fade` → blue with fade_in on red; for t in (0.3, 1.6, 2.0, 3.2): `window(t, t + 1/fps)` frame vs full-render frame, mean abs difference < 6).
  - `test_render_music_negative_start` (music start −1 → its sound starts immediately, 3 s long).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `pytest tests/test_videoedit_render.py -v` PASS.
- [ ] **Step 5: Commit** `Render video projects with FFmpeg`.

---

### Task 6: Text as ASS subtitles

**Files:**
- Create: `src/umc/videoedit/text_ass.py`
- Modify: `src/umc/videoedit/render.py` (fill the text hook)
- Test: `tests/test_videoedit_render.py` (append)

**Interfaces:**
- Consumes: `subtitles._ass_colour(hex_rgb, alpha)`, `subtitles.fonts_dir()`, `editor.fonts.find_font(family, bold, italic)`.
- Produces: `build_ass(items: Sequence[TextItem], size: tuple[int,int]) -> str`. In `render.py` the text hook becomes `,ass=text.ass:fontsdir=fonts` (only when the project has text items). The `.ass` file and the needed font files (the bundled DejaVu folder copied plus each `find_font` result) go into `tmp`, and `RenderPlan.cwd = tmp` (the caller passes `cwd` to FFmpeg, as `subtitles._burn` does).
- ASS rules:
  - `PlayResX/Y` = output size, `WrapStyle: 0`, `ScaledBorderAndShadow: yes`.
  - One style per item: `Fontsize = size% × H`, bold / italic, `PrimaryColour` = colour.
  - Without a box: `BorderStyle=1`, `Outline=outline`, `OutlineColour` = outline colour.
  - With a box: `BorderStyle=3`, box colour with alpha `round(255 × (1 − box_opacity))`. **Check which colour field the bundled libass uses for the box** (`OutlineColour` or `BackColour`), set it, and pin it with the test below. The outline width becomes the box padding.
  - Position: `{\an<n>\pos(x,y)}`. Anchor base points: x = 5 % / 50 % / 95 % of width, y = 5 % / 50 % / 95 % of height, plus the offsets.
  - Fades: `\fad(in_ms,out_ms)`. When `start < 0`, the event starts at 0 and uses `\fade(a0,0,0,0,t2,t3,t4)`, with `a0` = the alpha the fade-in had reached at time 0 (or no fade if already past it).
  - Escape `\` → `\\`, `{` → `\{`, `}` → `\}`, newline → `\N`. Times `H:MM:SS.cc`.

- [ ] **Step 1: Write failing tests:**
  - `test_build_ass_escaping` (text `a{b}\c` + newline → `a\{b\}\\c\N…`).
  - `test_build_ass_position_and_size` (bottom anchor on 320×240 → `\an2\pos(160,228)`, `Fontsize` 19 for 8 %).
  - `test_build_ass_negative_start_partial_fade`.
  - `test_render_text_visible` (blue clip + white "HELLO" 20 % centred → frame at 1 s has > 50 pixels with R > 200 in the centre third; frame at 2.5 s past `end` has none).
  - `test_render_text_box_colour` (box red, opacity 1 → a pixel inside the box area is red ± 30).
  - `test_text_escaping_and_unicode_path` (source copied to `tmp/Ærø 'it' {x}.mp4`, text `"Ærø ✓ {b} \\ 'q'"` → renders without error, length correct).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add text titles to the video renderer`.

---

### Task 7: Export (codecs, groups, GIF)

**Files:**
- Create: `src/umc/videoedit/export.py`
- Test: `tests/test_videoedit_export.py`

**Interfaces:**
- Consumes: Tasks 5–6; `codecs.resolve_video_codec`, `resolve_audio_codec`, `video_encoder`, `audio_encoder`, `video_quality_args`, `pixel_format_args`, `CONTAINERS`, `X264_PRESETS`, `VIDEO_CODEC_LABELS`, `AUDIO_CODEC_LABELS`; `gif.VideoToGifConverter` + `GifOptions`; `ffmpeg.run_ffmpeg`.
- Produces:
  - `EXPORT_CONTAINERS = ("mp4", "mkv", "webm", "mov", "avi", "gif")`.
  - `@dataclass ExportOptions` (with `opt`):
    - `container="mp4"` (choices above);
    - `video_codec="auto"` (auto + h264/h265/vp9/av1/mpeg4);
    - `audio_codec="auto"` (auto + aac/mp3/opus/flac);
    - `crf=20` (slider 0–51, help "Lower = better quality, bigger file.");
    - `preset="medium"` (`X264_PRESETS`);
    - `video_bitrate=0` (kbps, 0 = use quality);
    - `audio_bitrate=192` (kbps);
    - `resolution="project"` (project / 480p / 720p / 1080p / 4k; scales the project size by its height, even width);
    - `remove_audio=False`;
    - `gif_fps=15` and `gif_width=480` (`show_if` container == "gif").
    Codec / audio / bitrate fields are hidden for gif.
  - `render_groups(project, tmp: Path, progress: Callable[[float], None], cancel_event) -> Project`: when `len(main) > GROUP_SIZE` (16), each run of 16 main items is rendered without text / music (first item without transition) to `tmp/group_<level>_<k>.mkv` (libx264 `-crf 12 -preset veryfast`, `pcm_s16le`). Returns a project whose main row is one `VideoClip` per group (`fit="stretch"`, `in_=0`, `out` = group length, `transition_in` = the original transition of the group's first item) plus the original text / music, repeated level by level until ≤ 16. `length()` must be unchanged.
  - `run_export(project, options: ExportOptions, dest: Path, *, on_progress: Callable[[float, str], None], cancel_event: threading.Event) -> Path`: inside `TemporaryDirectory(prefix="umc-vedit-")`, `render_groups` (progress 0–40 % when used) → `build_render` → codec args (`-c:v`, quality, pixel format, `-c:a`, `-b:a`, `-movflags +faststart` for mp4/mov, `-an` when `remove_audio`, `-t total`) → `run_ffmpeg(cwd=plan.cwd)` into the `.part` file → `os.replace`. GIF: render an x264 CRF 12 intermediate, then run `VideoToGifConverter` with `GifOptions` defaults + `fps=gif_fps`, `width=gif_width` (custom if not a preset), and move its output to `dest`. Errors use `"Couldn't export the video."`.

- [ ] **Step 1: Write failing tests:**
  - `test_export_mp4_defaults` (red → blue crossfade + text + music → mp4 opens, h264 + aac, length 3.5 ± 1 frame).
  - `test_export_webm_vp9_opus`, `test_export_remove_audio` (no audio stream), `test_export_resolution_720p` (720 high, even width).
  - `test_export_gif` (opens with Pillow, > 1 frame).
  - `test_render_groups_keeps_timing` (20 pictures of 0.2 s with `fade` 0.1 between them → grouped project `length` equals the original's ± 0.001; exported length ± 1 frame; frame at a transition that crosses a group boundary is a mix).
  - `test_export_cancel_leaves_no_file` (`cancel_event` set from the progress callback at > 10 % → `ConversionCancelled`, neither `dest` nor `.part` exist).
  - `test_export_failure_keeps_existing_dest` (dest with known bytes, monkeypatched `run_ffmpeg` raising → bytes unchanged).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Export video projects`. **Land** (spec step 2).

---

### Task 8: Preview helpers and sound output

**Files:**
- Create: `src/umc/videoedit/preview.py`, `src/umc/videoedit/audio_out.py`
- Modify: `src/umc/tools/ffmpeg.py`, `pyproject.toml` (`"sounddevice>=0.5"` in `dependencies`; `pip install -e .` in the worktree venv)
- Test: `tests/test_videoedit_preview.py`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces in `tools/ffmpeg.py`:
  - `ffmpeg_bytes(args, *, cwd=None, timeout=60.0, cancel_event=None) -> bytes`: binary stdout, `-loglevel error`, `CREATE_NO_WINDOW`, `ConversionError` with the stderr tail on failure.
  - `ffmpeg_popen(args, *, cwd=None) -> subprocess.Popen` (binary stdout pipe, stderr discarded, `-nostdin`).
- Produces in `preview.py`:
  - `PREVIEW_HEIGHT = 360`, `CHUNK_SECONDS = 30.0`.
  - `preview_size(project, height=PREVIEW_HEIGHT) -> tuple[int, int]` (keeps the aspect ratio, even, never larger than the project size).
  - `still(project, t: float, tmp: Path, height=PREVIEW_HEIGHT) -> bytes` (RGB24, `w*h*3` bytes): `window(t, t + 1/fps)` → `build_render(video=True, audio=False)` → `-frames:v 1 -f rawvideo -pix_fmt rgb24 pipe:1`. Past the end → the background colour.
  - `StillCache(max_frames=200)`: `get(version, frame_no)`, `put(version, frame_no, data)`, LRU.
  - `PlaybackStreams(project, t: float, tmp: Path, height=PREVIEW_HEIGHT)`: properties `fps`, `size`. `frames() -> Iterator[tuple[float, bytes]]` (timeline time, RGB24) and `audio() -> Iterator[bytes]` (s16le 48 kHz stereo). Both are rendered chunk by chunk: a chunk ends at `min(t + CHUNK_SECONDS, start of the 17th main item after t, length)`. Each chunk is a video process (`-frames:v exact`) and an audio process (`-t exact`), and the next chunk's processes start when the current one is half read, so chunks join seamlessly. `close()` kills every process. Ends at `length(project)`.
  - `thumbnails(source: Source, count: int, height: int, tmp: Path) -> list[Path]` (one FFmpeg run, `fps={count/duration},scale=-2:{height}` → JPEGs; an image source gives one).
  - `waveform(source: Source, buckets: int) -> list[float]` (decode mono 8 kHz s16le via `ffmpeg_bytes`, peak per bucket 0–1; `[]` without audio).
- Produces in `audio_out.py`:
  - `class AudioOut(Protocol)`: `write(chunk: bytes)` (blocks while the buffer has > 0.5 s queued), `played_seconds() -> float`, `close()`, `silent: bool`.
  - `SoundDeviceOut`: `sounddevice.RawOutputStream(samplerate=48000, channels=2, dtype="int16", callback=…)`; the callback copies from a locked `bytearray` and counts played frames; an underrun plays silence and doesn't advance the count.
  - `NullAudioOut`: wall clock from the first `write`; `silent = True`.
  - `open_output() -> AudioOut`: `NullAudioOut` when `UMC_NO_AUDIO` is set, `sounddevice` fails to import, or no output device exists (all logged).

- [ ] **Step 1: Write failing tests:**
  - `test_preview_size` (1920×1080 → 640×360; 320×240 → 320×240; 1080×1920 → 202×360).
  - `test_still_size_and_colour` (red/blue project, t 3.0 → `len == w*h*3`, mostly blue).
  - `test_still_cache_lru`.
  - `test_playback_streams_one_second` (start 1.0 on a 2 s project → frame count == fps × 1 ± 1; audio bytes == 48000 × 4 × 1 ± 4800; times increase from 1.0).
  - `test_playback_streams_across_chunks` (monkeypatch `CHUNK_SECONDS = 0.5`, red+blue → frames for 4 s with no gaps or repeats: times strictly increasing by 1/fps).
  - `test_thumbnails_and_waveform` (red: 4 thumbnails exist; waveform of red has peaks > 0.1, blue → `[]`).
  - `test_null_audio_clock` (write 0.2 s of bytes → `played_seconds()` grows with time, `silent` True).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add video preview rendering and sound output`.

---

### Task 9: Timeline widget

**Files:**
- Create: `src/umc/gui/videoedit/__init__.py`, `src/umc/gui/videoedit/timeline.py`
- Modify: `src/umc/gui/theme.py` (tokens `clip_video`, `clip_picture`, `clip_audio`, `clip_text`, `playhead` for light and dark), `src/umc/gui/style.qss`
- Test: `tests/test_gui_videoedit.py`

**Interfaces:**
- Consumes: Tasks 1–2 (`layout`, `edits.*`, `EditError`).
- Produces: `TimelineView(QWidget)`:
  - `set_project(project)`; `playhead: float`; `set_playhead(t)`; `pixels_per_second` (default 80, range 5–800; `zoom_in()` / `zoom_out()` ×1.25; Ctrl+wheel); horizontal scroll with the wheel.
  - Rows top to bottom, heights: ruler 24, each text row 28, main row 64 (with a 16 px waveform strip of the clip's own sound), each music row 40. "+ row" buttons at the left edge of the text and music groups call `edits.add_row`.
  - `selected_id: str | None`; transitions are selected as `"tr:<item id>"`.
  - `hit_test(pos: QPoint) -> HitResult | None` with `HitResult(item_id: str, part: Literal["body","left","right","transition"])`. Edges are 6 px; the transition handle is 12 px centred on the boundary.
  - Signals: `edit(object, str)` (new `Project`, undo label), `seek(float)`, `selection_changed(object)`, `source_dropped(str, float, str, int)` (source id, time, row kind, row) for the mime type `application/x-umc-source`.
  - Dragging:
    - edges → `edits.trim`;
    - main body → `move_main` (the index from the drop x);
    - text / music body → `move_item`.
    A ghost is drawn while dragging; on release `edit` is emitted once. An `EditError` cancels the drag silently. Snapping within 8 px to the playhead and item edges; Alt disables it. A click on the ruler or empty space → `seek`.
  - `set_thumbnails(source_id, images: list[QImage])`, `set_waveform(source_id, peaks: list[float])` (both optional; clips paint a plain colour without them).

- [ ] **Step 1: Write failing tests** (offscreen, a project built directly from `Source`s without FFmpeg):
  - `test_timeline_hit_test_edges` (at 80 px/s a clip 0–2 s: x 2 → "left", x 80 → "body", x 158 → "right").
  - `test_drag_right_edge_trims` (drag from x 158 to x 118 → `edit` emitted once with the clip 0.5 s shorter).
  - `test_drag_main_reorders`.
  - `test_drag_overlapping_music_cancelled` (no `edit` signal).
  - `test_click_ruler_seeks` (x 120 → `seek(1.5)`).
  - `test_snapping_to_playhead` (playhead 1.0, dragging a text item so its start lands within 8 px of 1.0 → start == 1.0; with Alt → not snapped).
  - `test_zoom_limits`.
- [ ] **Step 2:** FAIL. **Step 3:** implement (custom `paintEvent`; no `QGraphicsView`). **Step 4:** `QT_QPA_PLATFORM=offscreen pytest tests/test_gui_videoedit.py -v` PASS.
- [ ] **Step 5: Commit** `Add the video timeline widget`.

---

### Task 10: Video editor page (media list, still preview, settings, undo, project save)

**Files:**
- Create: `src/umc/gui/videoedit/preview_view.py`, `media_list.py`, `inspector.py`, `dialogs.py` (`LocateDialog` only for now), `editor_page.py`
- Modify: `src/umc/core/options.py` (add kind `"textarea"` to `KINDS`), `src/umc/gui/widgets/settings_panel.py` (`QPlainTextEdit` editor for `"textarea"`); use `kind="textarea"` for `TextItem.text`
- Test: `tests/test_gui_videoedit.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3, 8, 9; `SettingsPanel.set_schema(options_cls, target_id, values)` / `.values()` / `changed`; the image editor's `_SaveWorker` / `QThread` pattern in `gui/editor/editor_page.py`.
- Produces:
  - `FrameService(QObject)`: `request(project, version: int, t: float)`. Debounced 40 ms; renders `preview.still` on one worker `QThread`; only the newest request is rendered and emitted. Cache = `StillCache`. Signal `ready(int, float, QImage)` (version, t, frame). Results whose version is older than the newest request are dropped.
  - `PreviewView(QWidget)`: `show_frame(QImage)`, aspect-correct on the `@window` dark background. Transport row: play / pause (`play_toggled` signal), previous / next frame, time label `m:ss.cc / m:ss.cc`, loop toggle (`loop: bool`).
  - `MediaList(QListWidget)`: one row per `Source` (thumbnail, name, duration). Drags with mime `application/x-umc-source`. A double-click emits `add_requested(str)`. Buttons "+ Add files" (`files_requested`) and "+ Text" (`text_requested`).
  - `Inspector(QWidget)`: `show_item(project, item_id | None)`. It shows the `ProjectSettings`, `VideoClip`, `PictureClip`, `AudioClip`, `TextItem` or `Transition` fields through `SettingsPanel`, and swaps `TextItem.font` for a `QFontComboBox` as the image editor's `ToolSettings` does. Signal `changed(dict)` → the page calls `edits.update` (or `set_settings`) with label `"Change <field>"`. Edits are coalesced into one undo step per change. While a slider or spin box is still moving, the page only requests stills for the unpushed `edits.update` result. On slider release / `editingFinished` it pushes that result once.
  - `LocateDialog(missing: list[MissingSource])` → `dict[source_id, Path | None]` (None = remove from project).
  - `VideoEditorPage(QWidget)`:
    - Signal `back_requested`; `message: QLabel`; `history: History | None`; `timeline`, `preview`, `inspector`, `media_list`.
    - `open_paths(paths) -> bool` (`new_project`; inline messages; the locate dialog for a project with missing sources; False when nothing usable opened).
    - `add_paths(paths)` (to the media list via `add_sources`, one undo step).
    - `maybe_leave() -> bool` ("Save project?" Save / Discard / Cancel when `history.dirty`).
    - `seek(t)`; `on_shown()` (requests the first still, focuses the timeline).
    - Top bar: ← Back to Convert, Undo, Redo (tooltips show labels), Save project, Save project as…, Export… (disabled until Task 13).
    - Shortcuts from the spec: Space, S, Delete, ← / →, Shift+← / →, Home / End, Ctrl+Z / Y / Shift+Z, Ctrl+S, Ctrl+Shift+S, Ctrl+E, + / −, L.
    - The page owns one `TemporaryDirectory(prefix="umc-vedit-")`, cleaned up in `closeEvent` / when the main window drops the page.
    - Save project as: `QFileDialog.getSaveFileName` filter `Video project (*.umcvideo)`, default name = first source stem + `.umcvideo`; `save_project` on a worker thread; `mark_saved` on success.

- [ ] **Step 1: Write failing tests** (`needs_ffmpeg`, `vmedia`):
  - `test_page_open_puts_clips_on_rows` (red, pic, music → 2 main items, 1 music item, the media list has 3 rows).
  - `test_split_shortcut_and_undo` (playhead 1.0, select red, key S → 3 main items, Undo enabled; Ctrl+Z → 2).
  - `test_inspector_change_is_one_undo_step` (set speed 2 via the inspector → `length` shorter; one undo restores it).
  - `test_scrub_newest_request_wins` (three `FrameService.request`s in a row with versions 1, 2, 3 → exactly one `ready`, with version 3).
  - `test_save_and_reopen_project` (monkeypatch the save dialog; file written; `open_paths([file])` gives an equal project; `dirty` False).
  - `test_open_project_with_missing_source_asks` (monkeypatch `LocateDialog.exec` → remove; the items are gone, the message names the file).
  - `test_leave_with_unsaved_changes_asks` (Cancel → False; Discard → True).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add the video editor page`.

---

### Task 11: Convert | Edit switch for videos

**Files:**
- Modify: `src/umc/gui/main_window.py`
- Test: `tests/test_gui_videoedit.py` (append)

**Interfaces:**
- Consumes: `VideoEditorPage` (Task 10), the existing `EditorPage`.
- Produces in `MainWindow`:
  - `edit_target() -> Literal["image","video"] | None`, replacing `edit_available()`:
    - `"video"` for a single `.umcvideo`, or when at least one file is a video and all are video / image / audio;
    - `"image"` when all are images (incl. `.ora`);
    - else None.
  - Keep `edit_available()` as `edit_target() is not None` for existing callers / tests.
  - `self.video_editor` (created on first use, added to `self.stack`).
  - `_active_editor()` returns the page currently shown, or None. Replace every `self.stack.currentIndex() == 1` check (`enter_edit`, `leave_edit`, `handle_drop`, `closeEvent`) with it.
  - The Edit tooltip follows the target: `"Open the images in the image editor (each image becomes a layer)"` / `"Open the files in the video editor"`.
  - Video Edit resizes to at least 1200 × 760 and calls `page.on_shown()`; Back restores the size as today.

- [ ] **Step 1: Write failing tests:**
  - `test_switch_targets` (pictures → "image"; red.mp4 → "video"; red + pic + music → "video"; music only → None; red + a `.txt` → None; one `.umcvideo` → "video").
  - `test_video_edit_and_back` (Edit → `stack.currentWidget()` is the video page, window ≥ 1200 wide; Back → converter, previous size).
  - `test_image_edit_still_works` (the existing image test flow passes unchanged: run `tests/test_gui_editor.py` too).
  - `test_drop_in_video_edit_adds_to_media_list`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full `pytest -q` PASS.
- [ ] **Step 5: Commit** `Open videos in the video editor from the Convert | Edit switch`. **Land** (spec step 3).

---

### Task 12: Playback with sound, thumbnails and waveforms

**Files:**
- Create: `src/umc/gui/videoedit/player.py`
- Modify: `src/umc/gui/videoedit/editor_page.py`, `preview_view.py`
- Test: `tests/test_gui_videoedit.py` (append)

**Interfaces:**
- Consumes: Task 8 (`PlaybackStreams`, `open_output`, `thumbnails`, `waveform`), Task 10.
- Produces: `Player(QObject)`:
  - `play(project, t: float)`, `stop()`, `is_playing: bool`, `loop: bool`.
  - Signals `frame(QImage)`, `position(float)`, `finished()`, `notice(str)`.
  - A reader thread fills a queue of at most 12 frames from `PlaybackStreams.frames()`; a feeder thread writes `audio()` into `open_output()`.
  - A `QTimer` (16 ms) takes `clock = t + audio.played_seconds()`, drops queued frames older than `clock`, emits the newest frame ≤ `clock` and `position(clock)`.
  - At the end: loop → restart from 0, else `finished()`.
  - `notice("No sound device — preview is silent")` once when `open_output().silent` and `UMC_NO_AUDIO` isn't set.
  - `stop()` closes the streams and output and joins the threads (≤ 1 s).
- Page wiring:
  - Space / ▶ toggles play.
  - Any `timeline.edit`, inspector change, undo / redo, seek or Back calls `player.stop()` first.
  - The playhead follows `position`; after `stop` a still is requested at the playhead.
  - On open / add: thumbnails (one per 120 px of clip at the default zoom, max 40 per source) and waveforms are made on a background `QThread` and passed to `timeline.set_thumbnails` / `set_waveform`.

- [ ] **Step 1: Write failing tests** (`UMC_NO_AUDIO=1`, so `NullAudioOut`):
  - `test_play_advances_playhead` (play from 0, `qtbot.wait(700)` → playhead between 0.3 and 1.2; at least one `frame` emitted).
  - `test_edit_stops_playback` (play, then emit a timeline edit → `is_playing` False; within 300 ms no more `frame` signals).
  - `test_play_reaches_end_and_stops` (start at length − 0.3 → `finished` within 2 s, playhead == length ± 0.05).
  - `test_loop_restarts`.
  - `test_thumbnails_arrive` (`qtbot.waitUntil` the timeline has thumbnails for red).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. Also try it by hand once: `python -m umc`, add `red.mp4` + music, Edit, Space: the picture moves and the sound plays in sync.
- [ ] **Step 5: Commit** `Play the video editor preview with sound`. **Land** (spec step 4).

---

### Task 13: Export dialog

**Files:**
- Modify: `src/umc/gui/videoedit/dialogs.py` (`ExportDialog`), `editor_page.py` (enable Export…, Ctrl+E)
- Test: `tests/test_gui_videoedit.py` (append)

**Interfaces:**
- Consumes: Task 7 (`ExportOptions`, `run_export`, `EXPORT_CONTAINERS`).
- Produces: `ExportDialog(project, parent)` with:
  - a `SettingsPanel` for `ExportOptions` (the `target_id` is the container; values remembered in `QSettings("videoedit/export")`);
  - an output path field + "Browse…" (`QFileDialog.getSaveFileName`, filter by container), with the default `unique_path(first source folder, f"{first stem}_edit", ext)`, and the extension following the container;
  - the Export button runs `run_export` on a `QThread` (refs kept until `QThread.finished`), with a progress bar + message and Cancel;
  - on success: "Open folder" (`QDesktopServices.openUrl` of the folder) and Close;
  - errors inline;
  - a typed path that already exists asks "Replace “name”?" (Yes / No).
  - The project is never changed by exporting.

- [ ] **Step 1: Write failing tests:**
  - `test_export_dialog_default_path` (`red_edit.mp4` in the source folder; switching the container to webm changes the extension).
  - `test_export_dialog_runs` (`qtbot.waitUntil` the file exists and opens; the progress bar reached 100).
  - `test_export_dialog_cancel` (cancel right after start → no output, no `.part`).
  - `test_export_dialog_existing_path_asks` (monkeypatch the question → No → nothing exported).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `Add the video export dialog`.

---

### Task 14: Self-test, docs, final check

**Files:**
- Modify: `src/umc/selftest.py`, `README.md` ("What works where"), `CLAUDE.md` (architecture tree: `videoedit/`, `gui/videoedit/`; roadmap 10.3; handoff "Next phase" step 3 ✅)
- Check: `packaging/umc.spec` (the `sounddevice` hook from pyinstaller-hooks-contrib collects `_sounddevice_data`; add `hiddenimports` only if the self-test fails)

- [ ] **Step 1:** Add `_video_editor_check(d: Path) -> list[Path]` to `selftest.py`:
  - generate a 1 s red and a 1 s blue clip and a 2 s tone with the bundled FFmpeg;
  - build a project with a crossfade (0.5 s), a text item and the tone as music;
  - `save_project` → `load_project` (no missing sources);
  - `run_export` to MP4 (length 1.5 s ± 1 frame);
  - `import sounddevice; sounddevice.get_portaudio_version()`.
  
  Step name `("video editor: project -> mp4 (crossfade, text, music)", …)`. Run `python -m umc --self-test report.txt` → `RESULT: OK`.
- [ ] **Step 2:** README rows (desktop / website):
  - "Video editor: cut, split, trim, reorder, join; pictures; music rows; text titles; transitions; speed, volume, fades, rotate/crop, brightness/contrast/saturation" — ✅ / coming later;
  - "Video editor preview with sound" — ✅ / coming later;
  - "Video project files (.umcvideo)" — ✅ / coming later.
- [ ] **Step 3:** CLAUDE.md roadmap: `3. ✅ Video editor, first version (desktop) — spec docs/superpowers/specs/2026-11-10-video-editor-design.md: …`, with the spec's "Not in this version" list and "Website: phase 2 (own spec)". Add the architecture lines.
- [ ] **Step 4:** `ruff check . && ruff format --check .`, `pytest -q` (all green, skips only for missing external tools), `python scripts/convert_matrix.py --out matrix-out` (converter unaffected: the same results as before).
- [ ] **Step 5: Commit** `Add video editor self-test step and document the video editor`. **Land** (spec step 5). Then run "Build Windows executable" (`gh workflow run build-windows.yml --ref main`) and check that its self-test step passes; fix any packaging gaps in a follow-up commit.
