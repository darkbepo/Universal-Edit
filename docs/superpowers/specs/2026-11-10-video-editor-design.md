# Video editor (desktop, then website) — design

Date: 2026-11-10 · Roadmap item 10.3 · Status: approved design, awaiting spec review

## Goal

When a video is added to the app, the user can choose **Convert | Edit**. Convert is the
existing converter, unchanged. Edit opens a timeline video editor in the same window: cut and
join videos, add pictures, music and text, transitions and simple effects, preview with sound,
export through the same codec machinery as the converter, and save the project to continue
later. Desktop app first (in full); the website follows with the same project format and model
(phase 2, outlined at the end).

## Decisions (agreed with the owner)

| Question | Decision |
|---|---|
| Which app first | Desktop. Website afterwards (README column says "coming later" until then). |
| Scope of the first version | All four groups: cut & join; pictures + music; text & titles; transitions & effects. |
| Preview | Live playback **with sound**, rendered by FFmpeg from the same recipe as the export. Sound through `sounddevice` (MIT, bundles PortAudio, MIT). No QtMultimedia (not in PySide6-Essentials). |
| When the Edit switch appears | When at least one video is added; any pictures and audio files added with it go onto the timeline too. All-pictures keeps opening the image editor. |
| Project file | Yes: `.umcvideo` (JSON) pointing to the original files. |
| Rendering approach | One FFmpeg `filter_complex` per render (export: full size; preview: small, from the playhead). Long projects are rendered in groups first (as `slideshow.py`'s crossfade groups). |
| Architecture | Qt-free model + renderer in `src/umc/videoedit/`; Qt only in `src/umc/gui/videoedit/`. The image editor is not touched. |

Not in this version (later steps): picture-in-picture / several overlapping video tracks,
keyframed motion (Ken Burns, animated text), chroma key, filters beyond brightness / contrast /
saturation, audio effects beyond volume and fades, editing subtitles tracks of a source.

## User flow

1. The user adds files as today. The Convert | Edit switch appears when:
   - every file is an image or a single `.ora` → **image editor** (unchanged);
   - at least one file is a video and the rest are videos, images or audio → **video editor**;
   - a single `.umcvideo` file → **video editor** (opens the project).
   Anything else (documents, archives …) hides the switch, as today.
2. **Edit** switches the `QStackedWidget` to the video editor page (window at least
   1200 × 760, previous size restored on Back).
   - Videos and pictures go onto the main row in file-list order; audio files go onto the first
     music row, one after another from 0 s.
   - Project resolution / frame rate default to "match the first video".
3. **Back to Convert** returns to the converter; with unsaved changes it asks
   "Save project?" (Save / Discard / Cancel).
4. Dropping more files while editing adds them to the media list (not the timeline); they are
   dragged onto the timeline from there or added at the playhead with a double-click.

## Editor layout

- **Top bar:** Back to Convert · Undo · Redo · Save project · Save project as… · **Export…**
- **Left — media list:** the project's files with thumbnail, name and duration; "+ Add files";
  "+ Text" (adds a text item at the playhead). Drag onto a row, or double-click to add at the
  playhead.
- **Centre — preview:** the frame at the playhead, aspect-correct on a dark background;
  under it: play / pause, previous / next frame, current time / total length, loop on/off.
- **Right — settings:** for the selected item (clip, picture, text, transition), or project
  settings when nothing is selected. Rendered from options dataclasses with the existing
  `opt()` / settings panel, so a new setting = a new field.
- **Bottom — timeline:** time ruler, zoom slider (and Ctrl+wheel), playhead (click / drag on
  the ruler), rows from top to bottom:
  - **Text rows** (one by default, "+ row"): text items placed freely in time.
  - **Main row**: videos and pictures back to back (no gaps), filmstrip thumbnails; a small
    handle between two clips holds the transition; the clip's own sound is drawn as a waveform
    strip under it.
  - **Music rows** (one by default, "+ row"): audio clips placed freely in time, waveforms.
- Editing on the timeline: drag a clip's left / right edge to trim; drag a clip to reorder (main
  row) or move in time (text / music rows, also between rows of the same kind); **S** splits the
  selected clip (or the clip under the playhead) at the playhead; Delete removes the selected
  item (the main row closes the gap). Snapping to the playhead and to item edges (Alt disables).
- Theme: existing `style.qss` tokens, same accent colour; clip colours per kind (video,
  picture, audio, text) defined as new tokens in `theme.py`.

### Shortcuts

Space play / pause · S split · Delete remove · ← / → one frame · Shift+← / → one second ·
Home / End · Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) · Ctrl+S save project · Ctrl+Shift+S save as ·
Ctrl+E export · + / − timeline zoom · L loop.

## What each item can do

| Item | Settings |
|---|---|
| **Project** (nothing selected) | Resolution: match first video / 720p / 1080p / 4K / vertical 1080×1920 / square 1080×1080 / custom W×H · frame rate: match first video / 24 / 25 / 30 / 50 / 60 · background colour (shown around letterboxed clips and behind fades to black). |
| **Video clip** (main row) | In / out (also by trimming on the timeline) · speed 0.25×–4× (audio follows with `atempo`, pitch kept) · volume 0–200 % · mute · fade in / out (picture and sound, 0–5 s) · fit: letterbox / crop / stretch · rotate 0 / 90 / 180 / 270 · flip H / V · crop (left / top / right / bottom px) · brightness, contrast, saturation (−100 … +100). |
| **Picture clip** (main row) | Duration (0.1–60 s, default 3 s) · fit · rotate / flip · fade in / out · brightness / contrast / saturation. |
| **Transition** (between two main clips) | None / crossfade / fade through black / wipe left / wipe right / slide left / slide right (FFmpeg `xfade` names) · duration 0.1–3 s (capped at half the shorter neighbour). Sound crossfades with `acrossfade`. |
| **Audio clip** (music row) | In / out · start time · volume 0–200 % · fade in / out · loop to fill until a chosen end time. |
| **Text item** (text row) | Text (multi-line) · start / end · position: 9 anchors + X / Y offset (% of the frame) · font (bundled DejaVu Sans + system fonts) · size (% of frame height) · bold / italic · colour · outline colour + width · background box on / off + colour + opacity · fade in / out. |

### Timeline maths

- The main row is a list; each clip's start = sum of the previous durations minus the
  transitions between them (a transition overlaps its two neighbours by its duration).
- A clip's timeline duration = (out − in) / speed; a picture's = its duration.
- Project length = max(end of main row, end of the last text / audio item).
  Past the end of the main row the background colour is shown.
- All times are stored in seconds (float) and snapped to the project frame rate when rendered.

## Model (`src/umc/videoedit/`, no Qt)

- `project.py` — frozen dataclasses (tuples, so undo can share them):
  `Project(settings, sources, main, text_rows, music_rows)`;
  `Source(id, path, kind: video|image|audio, duration, width, height, fps, has_audio)`
  (filled once by `ffmpeg.probe`); `VideoClip`, `PictureClip`, `Transition`, `AudioClip`,
  `TextItem`, `ProjectSettings`. Pure functions: `layout(project) -> Layout` (start / end of
  every item on the timeline), `length(project)`, `item_at(project, t)`.
- `edits.py` — pure functions `Project -> Project`: add, remove, move, reorder, trim, split,
  set settings, set transition, add / remove row. Each validates (no trim past the source, no
  negative durations, transition ≤ half the shorter neighbour).
- `history.py` — undo stack of whole `Project` snapshots (cheap: the project is small, frozen
  and structurally shared); cap 300 steps; "dirty" flag relative to the last save.
- `projectfile.py` — read / write `.umcvideo`: JSON `{"format": "umcvideo", "version": 1,
  "settings", "sources": [{"id", "path" (absolute), "relpath" (relative to the project file),
  ...}], "main", "text_rows", "music_rows"}`. On open, a source is looked up by `path`, then
  `relpath`; missing sources are reported (`MissingSource` list) so the GUI can ask to locate
  them.
- `render.py` — `build_render(project, *, start, end, size, fps, video=True, audio=True,
  tmp) -> RenderPlan` (input args, `filter_complex`, output maps, and the `.ass` file it wrote
  into `tmp`). One function shared by export and preview:
  - each main clip: `-ss in -t length -i src` (input seek), `setpts=(PTS-STARTPTS)/speed`,
    `fps`, crop / rotate / flip / `eq`, fit to project size (scale + pad or crop, as
    `slideshow.py` does), `format=yuv420p`, `fade` in / out;
  - pictures: `-loop 1 -t dur -i img` (RAW / HEIC / PSD are first converted to PNG with
    `imaging.open_image`, like the slideshow);
  - main row joined with `xfade` (transition) or `concat`; the background colour fills to
    the project length with `color=` + `overlay`;
  - text: one generated ASS file (styles per item, `\pos`, `\fad`, `\an`, box via
    `BorderStyle=3`) burnt in with `ass=` + `fontsdir`, run with cwd = temp dir — the same
    route as `subtitles.py`'s burn-in;
  - sound: each main clip `atrim` / `atempo` chain (`video.atempo_chain`) / `volume` / `afade`,
    silence (`anullsrc`) for clips without sound and pictures, `acrossfade` at transitions;
    music clips `atrim` / `aloop` / `volume` / `afade` / `adelay`; all mixed with
    `amix=normalize=0` + `alimiter`; output 48 kHz stereo;
  - `start` / `end` cut the plan to a window: only items overlapping it become inputs, with
    their seek offsets adjusted (that is what keeps preview start-up fast);
  - **groups:** if the main row has more than `GROUP_SIZE` (16) clips, the export renders
    groups to near-lossless intermediates (x264 CRF 12, PCM in MKV) in the temp dir and joins
    those, keeping the timing identical (as `slideshow.py`). Preview never needs this (it only
    renders a window).
- `export.py` — `ExportOptions` dataclass (rendered by the settings panel in the Export
  dialog): container (mp4 / mkv / webm / mov / avi / gif), video codec, audio codec, quality
  (CRF), preset, bitrate (optional), audio bitrate, resolution (project / 480p / 720p / 1080p /
  4K), "remove audio". Codec choice, encoder fallbacks and rate-control args come from
  `converters/codecs.py` (`resolve_video_codec`, `video_encoder`, `video_quality_args`,
  `pixel_format_args`, …). GIF renders a near-lossless intermediate and hands it to the
  existing GIF converter. `run_export(project, options, dest, job)` reports progress (0–100
  from `-progress`, against the project length) and honours cancel, like every converter.
- `preview.py` — Qt-free helpers the GUI drives from threads:
  - `still(project, t, height) -> bytes (RGB24)`: one frame via `build_render(start=t,
    end=t + 1/fps)` at preview height (default 360 px, even width). Results cached per
    (project version, frame number), LRU 200 frames.
  - `PlaybackStreams(project, t, height)`: starts two FFmpeg processes from `t` —
    video as `-f rawvideo -pix_fmt rgb24 pipe:1` at preview size, sound as `-f s16le -ar 48000
    -ac 2 pipe:1` — and yields frames / audio chunks; `close()` kills both (cancellable
    `tools/process.py`).
  - `thumbnails(source, count, height)` and `waveform(source, width) -> peaks` (FFmpeg once
    per source, cached in the editor's temp dir).
- `audio_out.py` — thin wrapper over `sounddevice.RawOutputStream` (48 kHz, int16, stereo)
  exposing `write(chunk)`, `played_seconds()` (the playback clock), `close()`. If
  `sounddevice` can't load or no output device exists, a `NullAudioOut` keeps a wall clock so
  playback still works silently; the GUI shows "No sound device — preview is silent".

Temporary files live in one `tempfile.TemporaryDirectory(prefix="umc-vedit-")` per open
editor (thumbnails, ASS files, converted pictures, export groups), removed when the editor
closes; startup cleanup already removes `umc-*` folders left by crashes.

## GUI (`src/umc/gui/videoedit/`)

- `editor_page.py` — page layout, top bar, wiring, save / export, Back with unsaved changes.
- `timeline.py` — custom `QWidget` (painted, not `QGraphicsView`): ruler, rows, items,
  playhead, hit-testing, drag-to-trim / move / reorder, snapping, zoom; emits edit requests
  (`edits.py` functions) instead of changing the model itself.
- `preview_view.py` — shows frames (`QImage` from RGB24), transport buttons, time label.
- `player.py` — `QObject` that owns playback: a reader thread fills a small frame queue from
  `PlaybackStreams`; a second thread feeds `audio_out`; a `QTimer` (≈ 60 Hz) shows the frame
  whose time matches `played_seconds()` and moves the playhead. Any edit, seek or Back stops
  playback. Scrubbing asks for `still()` on a worker thread, debounced (≈ 40 ms), newest
  request wins.
- `media_list.py`, `inspector.py` (settings panel per selected item), `export_dialog.py`
  (ExportOptions form + output path; the export runs as a `ConversionJob`-style job on the
  existing worker / `QThread` pattern with progress and Cancel), `locate_dialog.py` (missing
  sources: "Find file…" / "Remove from project").
- `main_window.py` — `edit_available()` becomes `edit_target()` returning `"image"`,
  `"video"` or `None`; the matching page is created on first use and added to the stack.

## Saving and exporting

- **Save project / Save project as…** — writes `.umcvideo` (atomic: temp file in the same
  folder, then replace). The first Save acts as Save as.
- **Export…** — dialog with `ExportOptions` and an output file (default: next to the first
  source, `name_edit.mp4`, made unique with `unique_path`). Choosing an existing file asks to
  replace it (system dialog). Export never changes the project.
- Errors are shown inline in plain language and logged; a failed or cancelled export leaves
  no partial output file.

## Error handling

- A file FFmpeg can't read: the others are still added; a plain message names it.
- A source missing when a project opens: the locate dialog; items using a removed source are
  deleted (one undoable step).
- FFmpeg missing: the existing clear "FFmpeg not found" message (it's bundled, so rare).
- A source whose resolution or frame rate differs from the project: fitted / converted
  automatically (that's what the fit mode and `fps` filter are for).
- Very long projects (> 4 h) or > 500 items: refused with a clear message.

## Licensing and packaging

- New dependency: `sounddevice` (MIT; its Windows wheel bundles PortAudio, MIT) — AGPL
  compatible. Added to `pyproject.toml`; PyInstaller has a hook for it (`_sounddevice_data`);
  verify in the built `.exe`.
- No change to the bundled FFmpeg (it already has `xfade`, `ass`, `acrossfade`, `amix`).
- `packaging/umc.spec` keeps excluding QtMultimedia.

## Testing

- `tests/test_videoedit_model.py` (no FFmpeg): layout maths (transitions overlap, speed,
  pictures, project length), every edit and its validation, split at a point, reorder,
  undo / redo round-trips and the dirty flag, `.umcvideo` write → read round-trip, missing
  sources found by relative path.
- `tests/test_videoedit_render.py` (FFmpeg; fixtures generated in `conftest.py`: colour /
  testsrc clips with and without sound, a sine-wave audio file, a picture): `build_render`
  argument snapshots for small projects; real exports checking duration (± 1 frame),
  resolution, audio present / absent, a crossfade's middle frame mixing both colours, text
  visible (pixels changed in the text area), music level after volume / fade, window renders
  (`start` / `end`) matching the same frames of a full render, group rendering identical in
  length to a single pass, GIF export opens.
- `tests/test_videoedit_preview.py`: `still()` size and cache, `PlaybackStreams` yields the
  expected number of frames / samples for a 1 s window, `NullAudioOut` clock.
- `tests/test_gui_videoedit.py` (pytest-qt, offscreen, `sounddevice` replaced by
  `NullAudioOut`): switch shows the video editor for videos, video + picture + audio, and a
  `.umcvideo` file, and the image editor still for pictures only; Edit puts clips on the right
  rows; S splits; trim by dragging an edge; Undo; Save project + reopen; Export to MP4 produces
  a file that opens; Back with unsaved changes asks.
- `src/umc/selftest.py`: a step that builds a two-clip project with a crossfade, text and
  music from generated sources, exports 2 s of MP4 inside the built `.exe`, and imports
  `sounddevice`.
- Lint and the full existing test suite stay green; run the convert matrix once (the
  converter must be unaffected).

## Order of work (desktop)

Each step is its own task: tests, commit, land on `main`.

1. Model: `project.py`, `edits.py`, `history.py`, `projectfile.py` + tests.
2. Renderer and export: `render.py`, `export.py` + FFmpeg tests.
3. Editor page: switch (`edit_target`), media list, timeline widget, inspector, still preview
   with scrubbing, undo / redo, save project.
4. Playback with sound: `preview.py` streams, `audio_out.py`, `player.py`, thumbnails and
   waveforms on the timeline; `sounddevice` dependency.
5. Export dialog, self-test step, README "What works where" rows (desktop ✅, website
   "coming later"), roadmap line 10.3 in `CLAUDE.md`; run "Build Windows executable".

## Phase 2 — website (outline; gets its own spec once the desktop editor has landed)

- **Same project format:** `web/src/videoedit/` holds a TypeScript port of `project.py` /
  `edits.py` / history and the layout maths; `.umcvideo` files are interchangeable. A browser
  can't reopen paths, so opening a project lists its sources and asks the user to pick them
  (matched by file name and size).
- **Same switch** in `web/src/ui/`: Convert | Edit when a video is added.
- **Preview:** Mediabunny decodes the sources (`CanvasSink` / `AudioBufferSink`); frames are
  composited on a canvas (fit, crop, rotate, `filter` for brightness / contrast / saturation,
  alpha for fades and crossfades, wipes / slides as clipped draws, text with `fillText` using
  the same anchor / size rules); sound through Web Audio, which is also the clock.
- **Export:** the same compositor runs in the worker on an `OffscreenCanvas`, frame by frame,
  into Mediabunny + WebCodecs (H.264 / VP9 / AV1; AAC / Opus), sound mixed with an
  `OfflineAudioContext`. Inputs Mediabunny can't read (AVI / FLV / WMV) get the existing
  near-lossless FFmpeg intermediate first. GIF via the existing video → GIF path.
- **Tests:** vitest for the model (shares the desktop test cases as JSON fixtures) and
  Playwright e2e: open the editor, split, add text, export, check duration and size.
- Known difference: the web renderer is a separate implementation, so tests compare length,
  size and sample frames against the same expectations as the desktop tests rather than
  bit-exact output.
