# Video editor on the website — design

Date: 2026-11-14 · Roadmap item 10.16, phase 2 · Status: draft for the owner's review

Builds on the desktop design `2026-11-10-video-editor-design.md` (its "Phase 2" outline) and the
shipped desktop editor (`src/umc/videoedit/`, `src/umc/gui/videoedit/`).

## Intent

The desktop video editor is done. The website gets the same editor so a visitor can cut, join and
mix videos, pictures, music and text **without installing anything and without their files
leaving the device** — the site's promise. A project saved in one app opens in the other.
When it ships, the start screen's "Video editor" tile on the website stops saying "Coming soon".

Success: a visitor drops a phone clip and a song, trims, adds a title and a crossfade, previews it
smoothly with sound, and downloads an MP4 in roughly real time on a normal PC — on a phone too.

## Decisions (agreed with the owner, 14 Nov 2026)

| Question | Decision |
|---|---|
| Features | The same as the desktop editor's first version (cut & join, pictures + music, text & titles, transitions & effects, project file). |
| Phones | **Full editor on phones** (≤ 560 px): preview on top, timeline below, settings and media list as bottom sheets — like the other website editors. |
| How it renders | **Drawn in the browser, encoded by the browser** (approach 1 below). |
| Limits | The browser version must **show its limits visibly** (file size and others), not fail silently. |

## Approaches considered

1. **Canvas compositor + WebCodecs (chosen).** Sources are decoded with Mediabunny (already a
   dependency), every frame is drawn on a canvas (fit / crop / rotate / grade / fades /
   transitions / text), sound is mixed by our own code and played through Web Audio, which is
   also the playback clock. Export runs the same drawing code in the conversion worker on an
   `OffscreenCanvas`, frame by frame, into Mediabunny + the browser's encoders. Smooth live
   preview, export near real time. Cost: a second renderer next to the desktop's FFmpeg recipe —
   small differences (font smoothing, filter maths) are accepted; tests check timing, size and
   sample colours, not exact pixels.
2. **Canvas preview, FFmpeg.wasm export** (port `render.py`). Output closer to the desktop, but
   ~5× slower and FFmpeg.wasm hangs on graphs with many inputs (see the slideshow's
   `XFADE_GROUP`); the canvas preview is needed anyway, so it would be two renderers. Rejected.
3. **Everything through FFmpeg.wasm**, small preview clips ahead of the playhead, like the
   desktop. Browser FFmpeg can't keep up with live playback. Rejected.

## Goals

- Same Convert | Edit switch rules as the desktop: at least one video, the rest videos /
  pictures / audio → video editor; a single `.umcvideo` → video editor (opens the project).
  All pictures → image editor, all audio → audio editor (unchanged).
- Same layout, items, settings, shortcuts and timeline maths as the desktop editor (desktop spec
  "Editor layout", "What each item can do", "Timeline maths"), except where listed under
  "Differences from the desktop".
- `.umcvideo` files interchangeable between the apps (same JSON, version 1).
- Live preview with sound; scrubbing shows the frame under the playhead.
- Export to MP4 / WebM / MKV / MOV and animated GIF.
- Works at 375 px wide.
- Limits are shown before they bite (see "Limits").

## Non-goals (same as the desktop's "not yet")

Picture-in-picture / overlapping video tracks, keyframes / Ken Burns, chroma key, more filters,
audio effects beyond volume and fades, subtitle-track editing. Also: no AVI export on the web,
no x264 "encoder speed" preset, no system-font list (see "Text").

## Anti-goals (would make it a failure even if it "works")

- The page freezing or a tab crashing on a big export instead of a clear message beforehand.
- A plain MP4 / MOV / WebM + MP3 project downloading FFmpeg.wasm (~30 MB) — FFmpeg is loaded only
  for sources the browser can't read.
- Files uploaded anywhere. Everything stays on the device.
- A different project format from the desktop's.

## Constraints from the existing code

- Stack: TypeScript + Vite, no UI framework; engines load lazily; the page bundle stays small —
  the editor is imported on demand from `ui/app.ts` (as `ensureAudio()` does).
- Long work runs in the long-lived module worker (`worker/jobWorker.ts`); Cancel = terminate it.
- Thread limits (`engines/threads.ts`) apply to any FFmpeg.wasm run.
- Theme tokens in `styles.css` must match `gui/theme.py`; clip colours per kind already exist
  on the desktop (`theme.py`) and are copied as CSS tokens (the theme test compares them).
- Icons: Tabler set in `ui/icon_data.ts` (same set as the desktop).
- Phones: `isPhone()`, "← Back" (`backLabel`), `.top-tools`, bottom sheets as in the image
  editor (`FloatingHost.onlyOnPhone`).
- Test hooks with `?e2e` (`window.__videoeditor`), like the other editors.
- Licences: nothing new is needed (Mediabunny MPL-2.0, DejaVu fonts already in the bundle).

## Architecture

```
web/src/videoedit/          # model, no DOM — line-by-line ports of src/umc/videoedit/
  project.ts                #   types + defaults + option schemas, layout(), length(), find(),
                            #   outputSize(), outputFps()
  edits.ts                  #   normalize, add / insert / remove / move / trim / split / update,
                            #   applyChanges, addRow, relink, removeSource, limits
  history.ts                #   snapshot undo (300 steps, dirty flag)
  projectfile.ts            #   .umcvideo read / write (same JSON as projectfile.py)
  window.ts                 #   cut a project to [start, end) (port of window.py)
  compose.ts                #   NEW: what one frame shows — a list of draw steps (source frame,
                            #   rects, rotation, flips, grade, alpha, clip rect for wipes,
                            #   offset for slides, background) for time t. Pure maths.
  textlayout.ts             #   NEW: text box position / size / fades from the same rules as
                            #   text_ass.py (anchors, 5 % margins, size % of height, outline, box)
  mix.ts                    #   NEW: sound mix of a time window → planar float32 48 kHz stereo
                            #   (volume, mute, fades, crossfades at transitions, music loop /
                            #   start / fades, sum, limiter). Pure, fed with decoded PCM.
  tempo.ts                  #   NEW: pitch-keeping time stretch (WSOLA) for clip speed ≠ 1
  limits.ts                 #   NEW: the browser limits (below), one place
web/src/ui/videoeditor/     # the page (loaded on demand)
  page.ts                   #   top bar, layout, wiring, save / open / export, Back
  timeline.ts               #   canvas-painted timeline (port of gui/videoedit/timeline.py):
                            #   ruler, rows, clips, transitions handles, playhead, snapping,
                            #   trim / move / reorder by pointer events, pinch zoom on touch
  media.ts                  #   media list (thumbnails, durations), add files, + Text
  inspector.ts              #   settings of the selected item via the existing SettingsForm
  preview.ts                #   preview canvas + transport; drives player.ts
  player.ts                 #   playback: decoders per active clip, Web Audio clock, frame loop
  draw.ts                   #   executes compose.ts steps on any Canvas 2D context (page canvas
                            #   or OffscreenCanvas in the worker) — the one drawing routine
  sources.ts                #   opening sources: Mediabunny probe / decode, pictures → ImageBitmap,
                            #   fallback to the worker for unreadable files
  exportdialog.ts           #   export options + estimate + limits note
  locate.ts                 #   "pick the missing files" window when a project opens
web/src/converters/videoedit.ts  # hidden worker converters: "video-edit-export" (compositor in the
                            #   worker → Mediabunny Output) and "video-edit-source" (FFmpeg.wasm
                            #   near-lossless intermediate for AVI / FLV / WMV, other audio,
                            #   HEIC / TIFF / RAW / PSD pictures → PNG, as the converters do today)
```

The model files are ports in the same style as `audioedit/` ↔ `src/umc/audioedit/`: same
names (camelCase), same rules, same messages. Shared test cases go in
`tests/fixtures/videoedit_cases.json`, written by a small desktop script (layout, every edit and
its validation, normalize, window cuts, project-file round trip, text layout); both suites run
them.

### Preview and playback

- **Scrub / still:** decode the frame at t for each clip `compose.ts` says is visible (one or two
  during a transition) with Mediabunny `CanvasSink` at preview size, then `draw.ts`. Newest
  request wins; ~40 ms debounce while dragging the playhead (as the desktop).
- **Play:** one decoder iterator per active clip, started at the clip-local time; frames are drawn
  when the Web Audio clock reaches them (`AudioContext.currentTime`, offset from the play start).
  Sound is mixed in chunks (~0.5 s) by `mix.ts` from PCM decoded with Mediabunny
  `AudioBufferSink` and scheduled as `AudioBufferSourceNode`s through a volume `GainNode`
  (`gainFor` from `audioedit/volume.ts`, same slider as the audio editor). Any edit or seek stops
  playback, like the desktop.
- **Preview size:** the canvas size on screen (device pixels), capped at 720 px tall; decoders
  ask for frames at that size so 4K sources stay cheap.
- Pictures are decoded once to an `ImageBitmap` (cached per source).
- Thumbnails / waveforms on the timeline: a few `CanvasSink` frames per video; waveform peaks
  from the decoded audio (reuse `audioedit/peaks.ts`).

### Export

- Runs in the conversion worker: the page sends the project + the source `File`s; the worker
  opens them with Mediabunny, draws each output frame (frame times = `outputFps`) on an
  `OffscreenCanvas` with the same `draw.ts`, wraps it as a `VideoSample` and feeds a Mediabunny
  `CanvasSource`/`VideoSampleSource`; sound comes from `mix.ts` in 1 s chunks into an
  `AudioSampleSource`. Progress = exported seconds / project length; Cancel terminates the worker.
- **Output goes straight to disk where the browser can** (Chrome / Edge: `showSaveFilePicker` →
  a writable stream → Mediabunny `StreamTarget`), so size is only limited by the disk. Elsewhere
  it is built in memory (`BufferTarget`) and downloaded — that is where the size limit below
  applies.
- Options (web): format MP4 / WebM / MKV / MOV / GIF; video codec Auto / H.264 / VP9 / AV1 /
  H.265 (only those the browser can encode, checked with `canEncodeVideo`); quality
  Very high / High / Medium / Low (Mediabunny quality presets) or a bitrate; audio AAC / Opus
  (AAC falls back to Opus where the browser has no AAC encoder, as `webcodecs.ts` does) + audio
  bitrate; resolution project / 480p / 720p / 1080p / 4K (`exportSize`, same maths as the
  desktop); remove audio. GIF: render to a near-lossless WebM in memory, then the existing
  video → GIF converter (FFmpeg.wasm palette route), with GIF fps / width.
- Output name: `<first source name> edit.<ext>`; Save project / Save project as download (or
  `showSaveFilePicker`, Ctrl+S writes there again — as the image editor does).

### Text

`textlayout.ts` reproduces the desktop's ASS layout (anchor, 5 % margins, offsets in % of the
frame, size in % of the frame height, outline width, box with padding and opacity, fades); the
canvas draws it with `strokeText` + `fillText`. Fonts: DejaVu Sans (bundled, loaded with
`FontFace` in both page and worker) and whatever font name the project has **if the browser
has it** (`document.fonts.check`), otherwise DejaVu Sans — the inspector shows "not on this
device, using DejaVu Sans". Bold / italic: DejaVu's bold / oblique faces are bundled too.

### Grades, transitions

- Brightness / contrast / saturation: the same maths as the desktop's `eq` mapping, applied per
  pixel at preview / export size by a small routine (`compose.ts` gives the numbers, `draw.ts`
  applies them with `ctx.filter` where it matches closely enough, or an `ImageData` pass).
  Builder's call which, as long as the shared colour test cases pass within a tolerance.
- Transitions: crossfade = alpha blend; fade through black = each side to black then up; wipes =
  clip rectangles; slides = offsets — following FFmpeg `xfade`'s definitions so the two apps
  look alike. Sound crossfades over the same span (equal-power like `acrossfade`'s default).

## Limits (shown, never silent)

One table in `videoedit/limits.ts`; every limit has a plain message that suggests the desktop
app. My proposed numbers (the builder may tune them after measuring; changes go in this table):

| Limit | Value | Where it shows |
|---|---|---|
| Project length / items | 4 h / 500 items (same as the desktop) | refused edit, message |
| Sources the browser reads directly (MP4, MOV, MKV, WebM, MP3, M4A, WAV, OGG, FLAC; PNG, JPG, WebP, GIF, BMP, AVIF) | **no size limit** — read from the file on demand, never copied into memory | — |
| Sources needing an FFmpeg copy first (AVI, FLV, WMV, other audio; HEIC / TIFF / RAW / PSD pictures) | **≤ 1 GB per file** (FFmpeg.wasm keeps it in memory) | when the file is added: "too big for the browser — use the desktop app"; a note in the media list on converted files |
| Pictures | ≤ 16 000 px a side (as the image editor) | when added |
| Export without "save to disk" (Firefox, Safari) | **≤ 2 GB estimated output** | the export window shows the estimated size; above the limit Export is disabled with "too big for this browser — use Chrome / Edge, a lower quality, or the desktop app" |
| Codecs | only what this browser can encode | the codec list hides the others; H.265 says "if your device supports it" |
| Speed | export roughly real time or slower on phones; "the desktop app is faster" note as for other video targets (`SLOW_IN_BROWSER`) | export window |

The export window also has a short "Browser limits" line linking the list above in plain words.

## Calls made after the builder check (mine — the owner can overrule)

1. **Opens but can't decode** (e.g. an iPhone HEVC clip in a browser without an HEVC decoder,
   or AAC sound without an AAC decoder): sources are sorted by a **decode check**
   (Mediabunny `canDecode()` on the video and audio tracks), not by file type. A file that
   fails it takes the FFmpeg-copy route (and its 1 GB limit), with a note in the media list
   ("converted for this browser — the desktop app reads it directly"). FFmpeg is still never
   loaded for files that decode, so the anti-goal holds for them.
2. **Browsers:** Chrome / Edge (desktop and Android) get everything and are what the e2e suite
   tests. Firefox and Safari / iPhone get the editor too; any part their browser lacks is
   detected up front and explained in plain words instead of failing. Examples: no audio
   encoder → "Export with sound needs Chrome or Edge — export without sound, or use the
   desktop app"; no fonts in workers → the export draws on the page instead (same `draw.ts`,
   slower but it works); no `showSaveFilePicker` → download with the 2 GB limit. No extra
   encoders are written for them.
3. **Half-finished editor on the live site:** every push to `web/` deploys, so until step 6 the
   video editor is reachable only with `?e2e` or `?video-editor` in the URL. The Convert |
   Edit switch and the start tile behave as today for normal visitors. Step 6 removes the
   gate.
4. **Project file:** the source entry gets an optional `size` (bytes). The desktop writes and
   keeps it too (a small change to `Source` / `projectfile.py`, still version 1; older files
   without it still open). Missing sources are matched by name + size, falling back to name +
   duration when there is no size. Re-saving a project keeps each matched source's original
   `path` / `relpath`; only sources added in the browser get the bare file name.

## Differences from the desktop (accepted)

- No AVI export, no encoder-speed preset; quality is four steps (or a bitrate) instead of CRF.
- Fonts: DejaVu Sans plus fonts the browser already has (no system font list).
- Pixels are close to, not identical with, the desktop output (separate renderer).
- Opening a project can't reopen paths: the editor lists the project's sources and asks the
  user to pick them (matched as in "Calls" point 4; one pick of several files fills in all it
  can at once). Sources added in the browser are written with `path` = `relpath` = the file
  name, so the desktop finds them when the project sits next to them.

## Phones (≤ 560 px)

Preview on top (16:9 box, full width), transport under it, timeline below filling the rest
(taller rows, drag handles ≥ 24 px, pinch = zoom, two-finger drag = scroll). Top bar: "← Back",
Undo, Redo, Export; Save / Save as / Add files in `.top-tools`. Media list and the inspector
are bottom sheets (one at a time), opened by buttons in the toolbar row and by selecting an item
(inspector). Check at 375 px wide in the e2e suite.

## Start screen and switch

- Remove `"video"` from `WEB_LATER` in `src/umc/core/editors.py` and the web `available: false`
  in `web/src/core/editors.ts` (the hub test keeps both lists in step). The tile opens an empty
  project ("Add files" / drop).
- `editChoices()` in `ui/app.ts` gains `"video"`; `.umcvideo` becomes a detected type on the web
  (family "project", as the desktop's `detect.py`).
- README "What it can do" video-editor rows: website column ✅ (with the limits footnote);
  CLAUDE.md roadmap 10.16 updated; `SLOW_IN_BROWSER`-style note in the export window.

## Testing

- **vitest:** the shared model cases (`videoedit_cases.json`), `compose.ts` (what is drawn at
  given times: transitions, fades, past-the-end background), `textlayout.ts` (shared cases from
  `text_ass.py`'s maths), `mix.ts` (levels after volume / fade / crossfade / loop, silence for
  pictures), `tempo.ts` (length and pitch kept for 0.5× / 2×), `limits.ts`.
- **Playwright e2e** (`e2e/videoeditor.spec.ts`, fixtures made in `global-setup.ts`): Edit
  appears for video + picture + audio; clips land on the right rows; split, trim, undo; text
  shows in the preview (pixels change); play advances the playhead; export MP4 → length
  (± 1 frame), size, audio present, a crossfade's middle frame mixes both colours; GIF export
  opens; save project → reopen (locate window) → same timeline; 375 px layout; the start tile is
  live; a too-big source / export shows its message (limits lowered by an `?e2e` hook).
- **Cross-app:** the desktop's `load_project` opens a web-saved `.umcvideo`, and the web opens a
  desktop-saved one (python3 in the e2e setup, like the document editor's cross-app test).

## Order of work (each its own task: tests, review, land on main)

1. Model ports + shared fixtures (`project`, `edits`, `history`, `projectfile`, `window`);
   optional `size` in the desktop project file.
2. `compose`, `textlayout`, `mix`, `tempo`, `limits` + tests.
3. Page shell behind the `?video-editor` gate: switch, hub tile (still unavailable until step 6), media list, timeline,
   inspector, still preview with scrubbing, undo / redo, save / open project with locate.
4. Playback with sound, thumbnails, waveforms.
5. Export (worker, StreamTarget / BufferTarget, GIF, estimate + limits) .
6. Phones, start tile live, README / CLAUDE.md, cross-app test; live-site check after deploy.

## Left to the builder

Exact drawing technique for grades (filter vs. pixel pass), chunk sizes, decoder pooling, the
limiter's design, WSOLA parameters, how the estimate is computed (bitrate per quality step ×
length), and the timeline's exact touch gestures — within the rules above.
