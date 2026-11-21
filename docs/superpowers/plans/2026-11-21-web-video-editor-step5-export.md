# Website video editor, step 5: export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the website video editor's "Export…" button writes the project to MP4 / WebM / MKV / MOV or an animated GIF, in the conversion worker, straight to disk where the browser can.

**Architecture:** DOM-free export maths in `web/src/videoedit/export.ts` (options schema, sizes, codec choice, estimate, name). The pipeline `web/src/ui/videoeditor/exporter.ts` runs in any context: it opens the sources with the existing `SourceBank` (no copying in the worker), draws each output frame on an `OffscreenCanvas` with `draw.ts`, mixes the sound in 1 s chunks with `SoundStore` + `mixWindow` + one `Limiter`, and feeds a Mediabunny `Output` (`CanvasSource` + `AudioSampleSource`). The hidden worker converter `video-edit-export` runs it and returns the bytes (`BufferTarget`) or writes to a `FileSystemFileHandle` the page picked (`StreamTarget`). GIF = a near-lossless WebM in memory, then the existing `gifConverter`. The page gets an export window (`exportdialog.ts`) on the existing `SettingsForm`.

**Tech Stack:** TypeScript, Mediabunny (already a dependency), WebCodecs, OffscreenCanvas, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` (sections "Export", "Limits", "Calls made after the builder check", "Testing").

## Global Constraints

- Options (web): format MP4 / WebM / MKV / MOV / GIF; video codec Auto / H.264 / VP9 / AV1 / H.265 (only those the browser can encode, checked with `canEncodeVideo`); quality Very high / High / Medium / Low (Mediabunny quality presets) or a bitrate; audio AAC / Opus (AAC falls back to Opus where the browser has no AAC encoder) + audio bitrate; resolution project / 480p / 720p / 1080p / 4K (`exportSize`, same maths as the desktop); remove audio. GIF: GIF fps / width.
- No AVI export, no encoder-speed preset on the web.
- Output name: `<first source name> edit.<ext>`.
- Progress = exported seconds / project length; Cancel terminates the worker.
- Output goes straight to disk where the browser can (`showSaveFilePicker` → `StreamTarget`); elsewhere `BufferTarget` + download, refused above `LIMITS.downloadBytes` (2 GB estimated) with `exportProblem`'s message.
- H.265 is labelled "if your device supports it"; codecs the browser can't encode are hidden.
- No audio encoder → "Export with sound needs Chrome or Edge — export without sound, or use the desktop app."
- The export window shows the estimated size, `LIMITS_NOTE`, and a "the desktop app is much faster" note (as `SLOW_IN_BROWSER` in `ui/app.ts`).
- FFmpeg.wasm is loaded only for GIF (and for sources that already needed it); an MP4 export of MP4 / PNG / MP3 sources never loads it.
- Same messages / numbers as the desktop where the desktop has them (`src/umc/videoedit/export.py`: `export_size`, defaults GIF fps 15, width 480, audio bitrate 192).
- Commits: imperative subject, author darkbepo, no trailers. Never edit outside `web/`, `tests/fixtures/videoedit_cases.json`, `scripts/make_videoedit_cases.py`, `CLAUDE.md`, this plan.

## Review Focus

1. A project whose sources aren't all found (unlinked) → Export refuses with "Find the missing files first" instead of rendering black.
2. A project with no sound at all (pictures + text only) or "Remove audio" → no audio track is written and no audio encoder is needed.
3. Cancel mid-export with a save-to-disk file → the partial file is removed (writable aborted), no half file left; the next export works (new worker).
4. An odd project size (e.g. 1081 high) → the encoded size is even, so H.264 doesn't refuse it.
5. Length: the exported video is the project length within one frame, audio as long as the video.

---

### Task 1: Export maths

**Files:**
- Create: `web/src/videoedit/export.ts`
- Modify: `scripts/make_videoedit_cases.py`, `tests/fixtures/videoedit_cases.json` (new key `export_size`)
- Test: `web/tests/videoedit-export.test.ts`

**Interfaces:**
- Produces:
  - `EXPORT_OPTIONS: OptionSpec[]` — ids `container` ("mp4" | "webm" | "mkv" | "mov" | "gif", default "mp4"), `video_codec` ("auto" | "h264" | "vp9" | "av1" | "h265"), `quality` ("very-high" | "high" | "medium" | "low", default "high"), `video_bitrate` (int kbps, 0 = use quality, max 200000), `audio_codec` ("auto" | "aac" | "opus"), `audio_bitrate` (int kbps 32–512, default 192), `resolution` ("project" | "480p" | "720p" | "1080p" | "4k"), `remove_audio` (bool), `gif_fps` (slider 1–50, default 15), `gif_width` (int 16–4096, default 480). `show_if` as the desktop (GIF hides codec / quality / bitrate / audio; remove_audio hides audio codec / bitrate).
  - `exportSize(p: Project, resolution: string): [number, number]` — port of `export_size`.
  - `evenSize([w, h]): [number, number]` — each side rounded down to even, min 2.
  - `gifSize(p: Project, width: number): [number, number]` — width as given (even), height from the project aspect, even.
  - `exportName(p: Project, ext: string): string` — `<first source's name without extension> edit.<ext>`; `"video edit.<ext>"` with no sources.
  - `type Encoders = { video(codec: "avc" | "vp9" | "av1" | "hevc" | "vp8"): Promise<boolean>; audio(codec: "aac" | "opus"): Promise<boolean> }`
  - `chooseCodecs(values: Values, hasSound: boolean, enc: Encoders): Promise<{ video: string; audio: "aac" | "opus" | null } | { problem: string }>` — Auto: mp4/mov/mkv → avc, then hevc, vp9, av1; webm → vp9, then av1, vp8. Explicit choice not encodable → problem "This browser or device can't encode <label>. Try another codec, or use the desktop app." Audio: null when `remove_audio` or `!hasSound`; webm → opus; else the choice (auto = aac), aac falling back to opus; none encodable → problem = the Global Constraints "Export with sound needs Chrome or Edge…" text. GIF → `{ video: "vp9" or "vp8" (first encodable), audio: null }`.
  - `availableCodecs(enc): Promise<string[]>` — the `video_codec` choice values to show ("auto" always first).
  - `exportEstimate(p: Project, values: Values): number` — `estimateBytes` with exportSize / outputFps / length; GIF: `gifSize` × gif_fps × length × 0.5 bits per pixel (ruling: rough, shown as "about").
  - `frameCount(length: number, fps: number): number` — `Math.max(1, Math.round(length * fps))`.

- [ ] **Step 1:** add an `export_size` section to `scripts/make_videoedit_cases.py` (a few projects incl. a portrait and an odd-height one × every resolution, results from `umc.videoedit.export.export_size`); regenerate the fixture (`.venv/Scripts/python.exe scripts/make_videoedit_cases.py`); check `git diff` only adds that key.
- [ ] **Step 2:** write the failing tests: shared `export_size` cases; `evenSize([1921, 1081])` → `[1920, 1080]`; `exportName` with "Holiday.mov" → "Holiday edit.mp4"; `chooseCodecs` with fake `Encoders` (avc missing → hevc for mp4; webm with no vp9 → av1; aac missing → opus; nothing audio → the exact problem text; remove_audio → audio null without asking the encoder; explicit h265 missing → problem); `frameCount(10, 30)` → 300; `exportEstimate` grows with quality step and is > 0 for GIF.
- [ ] **Step 3:** run `npx vitest run tests/videoedit-export.test.ts` (in `web/`) → FAIL.
- [ ] **Step 4:** implement `export.ts`.
- [ ] **Step 5:** run the test → PASS; `npx tsc --noEmit`; the Python suite's case-file test still passes (`.venv/Scripts/python.exe -m pytest tests -k videoedit_cases -q`).
- [ ] **Step 6:** commit `Add the website video editor's export maths`.

### Task 2: The export pipeline

**Files:**
- Create: `web/src/ui/videoeditor/exporter.ts`
- Modify: `web/src/ui/videoeditor/sources.ts` (client optional)
- Test: `web/tests/videoedit-exporter.test.ts`

**Interfaces:**
- Consumes: Task 1; `frameAt`, `textAt`, `drawFrame`, `drawText`, `SoundStore`, `audioNeeds` (playmath), `mixWindow`, `Limiter`, `SAMPLE_RATE`, `loadBundledFonts`, `hasFont`.
- Produces:
  - `SourceBank` constructor takes `ConversionClient | null`; with null, a file that needs a copy throws `ConversionError("<name> can't be read here.")` (the page always sends decodable files).
  - `class FrameReader<T>` — `constructor(open: (from: number) => AsyncIterator<Timed<T>>)`, `async at(t: number): Promise<T | null>` returns the newest frame with timestamp ≤ t + 1e-6 (the first frame when t is before it), reading forward; reopens when t is more than 1 s behind the last returned frame or 2 s past the read position; `close()`. Sequential (one call at a time).
  - `interface ExportJob { project: Project; size: [number, number]; fps: number; video: { codec: string; bitrate: number | "very-high" | "high" | "medium" | "low" }; audio: { codec: "aac" | "opus"; bitrate: number } | null; container: "mp4" | "webm" | "mkv" | "mov" }`
  - `exportProject(job: ExportJob, bank: SourceBank, target: import("mediabunny").Target, progress: (seconds: number) => void, signal: AbortSignal): Promise<void>` — formats: mp4 `Mp4OutputFormat({ fastStart: false })` with a StreamTarget / `"in-memory"` with a BufferTarget (ruling: pass `fastStart` via a `job` field `inMemory: boolean`); frames `i / fps` for `frameCount`, each drawn then `await videoSource.add(i / fps, 1 / fps)`; sound chunks of 1 s up to the video length added after the frames they cover (interleaved, so the muxer never buffers the whole sound); one reader per `Layer.itemId` (closed when the item leaves the frame), pictures cached per source; throws `ConversionCancelled` (core/errors) when `signal` aborts, after `output.cancel()`.

- [ ] **Step 1:** failing `FrameReader` tests with a fake iterator of timestamps 0, 0.04, 0.08…: `at(0.05)` → frame 0.04; `at(0)` before the first frame (first at 0.02) → that first frame; going back 1.5 s reopens (open called twice, from that time); a jump of 3 s reopens; an iterator that ends → the last frame keeps coming back; a throwing open → null, no further open for the same position.
- [ ] **Step 2:** run → FAIL; implement `FrameReader`; run → PASS.
- [ ] **Step 3:** make the `SourceBank` client optional (types + the copy path's error); existing tests still pass.
- [ ] **Step 4:** implement `exportProject`. It is exercised end to end in Task 4's e2e tests (vitest has no WebCodecs); keep the per-frame / per-chunk scheduling in small pure helpers (`chunksDue(videoSecondsDone, audioSecondsDone, length): number[]` — the 1 s chunk starts to add now) and unit-test those.
- [ ] **Step 5:** `npx tsc --noEmit`, `npx vitest run` → all pass.
- [ ] **Step 6:** commit `Add the website video editor's export pipeline`.

### Task 3: Worker converter and GIF

**Files:**
- Modify: `web/src/converters/videoedit.ts`, `web/src/core/registry.ts`
- Test: `web/tests/videoedit-export.test.ts` (option → job mapping)

**Interfaces:**
- Consumes: Tasks 1–2; `gifConverter` (`converters/video.ts`); `projectfile` read / write.
- Produces:
  - `EXPORT_TARGET: Target` (`id: "export"`, `converterId: "video-edit-export"`), `videoEditExportConverter` (no targets) registered beside `videoEditSourceConverter`.
  - Options it reads: `project` (the `.umcvideo` JSON text), `values` (the export window's values), `handle` (optional `FileSystemFileHandle`), `ids` (array: source id of each input file, same order as `files`).
  - `jobFor(p: Project, values: Values, codecs: {video, audio}, inMemory: boolean): ExportJob` (pure, in `export.ts`; tested: size = `evenSize(exportSize(...))`, GIF size = `gifSize`, fps = `outputFps` or `gif_fps`, bitrate = `video_bitrate * 1000` when set else the quality string).
  - Run: loads fonts (`loadBundledFonts`), opens every file in a `SourceBank(null)` under its id, `chooseCodecs` with `canEncodeVideo` / `canEncodeAudio`, then:
    - non-GIF with `handle`: `StreamTarget(await handle.createWritable())`; on error / cancel `writable.abort()`; returns `[]` (the page knows it was written).
    - non-GIF without: `BufferTarget` → `[{ name: exportName, data, type }]`.
    - GIF: WebM (vp9 / vp8, bitrate "very-high", no audio, `gifSize`, `gif_fps`) into a `BufferTarget`, progress 0…0.7; then `gifConverter.run` on it with `{ fps: gif_fps, width: "original", start: "", end: "", speed: 1, loop: 0, max_colors: 256, dither: "sierra2_4a" }`, progress 0.7…1; returns the GIF named `exportName(p, "gif")`.
  - Progress message "Exporting… <m:ss> of <m:ss>".

- [ ] **Step 1:** failing `jobFor` tests (above); run → FAIL; implement; → PASS.
- [ ] **Step 2:** implement the converter + registry entry; `npx tsc --noEmit`, `npx vitest run` pass.
- [ ] **Step 3:** commit `Run video editor exports in the conversion worker`.

### Task 4: Export window, wiring, tests, docs

**Files:**
- Create: `web/src/ui/videoeditor/exportdialog.ts`
- Modify: `web/src/ui/videoeditor/page.ts`, `web/src/styles.css`, `web/e2e/videoeditor.spec.ts`, `CLAUDE.md`
- Test: `web/e2e/videoeditor.spec.ts`

**Interfaces:**
- Consumes: Tasks 1–3; `SettingsForm`; the page's `ConversionClient`, `SourceBank.file(id)`, project-file writer.
- Produces:
  - Export button enabled when the project has at least one main item and no edit is running; opens a modal `dialog.video-export` (`h2` "Export video", the form, `.ve-export-estimate` "About 12 MB", the limits note `.ve-export-limits` = `LIMITS_NOTE`, the slow note `.ve-export-slow`, a problem line `.ve-export-problem`, buttons "Export" / "Cancel"). The codec list shows only `availableCodecs`. Export is disabled while a problem shows (missing sources → "Find the missing files first — Export needs every file."; `exportProblem`; `chooseCodecs` problem).
  - Chrome / Edge (`showSaveFilePicker` present): Export asks for the file (`suggestedName: exportName`) first; a cancelled picker does nothing. Elsewhere / GIF: the result is downloaded.
  - While exporting: playback stops, the page shows progress in the status line (`.ve-status`) with a "Cancel" button (`client.cancel()`); the timeline stays read-only (edits refused like during other jobs); success → "Exported <name>."; cancel → "Export cancelled." (and the picked file removed: `handle.remove?.()` when supported, ruling); error → its plain message.
  - Values remembered for the session in the page (not stored).
  - e2e hooks (`?e2e`): `__videoeditor.exportNow(values)` (skips the dialog, download path), `__videoeditor.useOpfsHandle(name)` (the next export writes to an OPFS file instead of the picker), `__videoeditor.inspectExport()` → `{ duration, width, height, hasAudio, codec, colourAt(t): [r, g, b] }` of the last export (read with Mediabunny on the page), `__videoeditor.setExportLimits(partial)` → `setLimitsForTests`.
- e2e tests (Edge locally, Chromium on CI), fixtures from `global-setup.ts`:
  1. "export MP4 keeps length, size and sound" — clip + music → MP4; duration within 1 / fps of the project length, size = project size, `hasAudio`.
  2. "export WebM at 480p" — size 480 high, even width, codec vp9 or av1.
  3. "a crossfade's middle frame mixes both clips" — two solid-colour clips with a 1 s crossfade; `colourAt(middle)` between the two colours (each channel within the pair's range ± 20).
  4. "pictures and text only export without sound" — `hasAudio` false.
  5. "export GIF opens" — the download starts with `GIF89a`, decodes in an `<img>` with the chosen width.
  6. "export straight to a file" — `useOpfsHandle`; the OPFS file is a playable MP4 of the right length.
  7. "cancel stops the export and leaves no file" — start a longer export to OPFS, Cancel, the OPFS file is gone or empty, status "Export cancelled.", a second export then succeeds.
  8. "too big for a download shows the limit" — `setExportLimits({ downloadBytes: 1000 })`, no picker (`useOpfsHandle` not set, `showSaveFilePicker` deleted in the test) → problem line text from `exportProblem`, Export disabled.
  9. "missing files block export" — open a project with an unlinked source → the problem line.
  10. "the dialog hides codecs the browser can't encode" — codec options ⊆ the encodable list.
- [ ] **Step 1:** write e2e tests 1–10 → run them (they fail).
- [ ] **Step 2:** implement `exportdialog.ts` and the page wiring; make the tests pass, one Playwright run at a time: `npx playwright test -c playwright.edge.local.config.ts e2e/videoeditor.spec.ts`.
- [ ] **Step 3:** `npx tsc --noEmit`, `npx vitest run`, the whole videoeditor e2e file pass.
- [ ] **Step 4:** CLAUDE.md, under roadmap 10.16's website lines, one line: `Website: step 5 export ✅ — videoedit/export.ts (options, exportSize = desktop cases, codec choice), ui/videoeditor/exporter.ts (FrameReader, OffscreenCanvas + draw.ts, SoundStore 1 s chunks → Mediabunny), hidden converter "video-edit-export" (StreamTarget on a picked FileSystemFileHandle, else BufferTarget; GIF = WebM then gifConverter); exportdialog.ts.`
- [ ] **Step 5:** commit `Export projects from the website video editor`.
