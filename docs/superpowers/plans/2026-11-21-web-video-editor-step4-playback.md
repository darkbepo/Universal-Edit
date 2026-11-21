# Website video editor — step 4: playback, thumbnails, waveforms — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The website video editor plays the project with sound (Space / ▶, Loop / L, a volume slider), the timeline follows the playhead while playing, and clips show thumbnails and waveforms — still behind the `?video-editor` / `?e2e` gate.

**Architecture:** Everything worth testing is DOM-free and covered by vitest: playback / thumbnail / waveform maths (`ui/videoeditor/playmath.ts`, ports of `gui/videoedit/player.py` constants and `timeline.py` `_paint_thumbs` / `_paint_wave`), a resampler (`videoedit/resample.ts`), a sound store that decodes source sound in 48 kHz stereo blocks for `mix.ts`'s `AudioLookup` (`ui/videoeditor/sound.ts`, worker-safe so step 5's export reuses it), and a frame stream that turns Mediabunny's async canvas iterator into "the frame at time t" (`ui/videoeditor/framestream.ts`). The player (`player.ts`) and the scanner (`scanner.ts`) are thin shells using Web Audio and `requestAnimationFrame`, covered by Playwright.

**Tech Stack:** TypeScript (strict), Vite, Web Audio (`AudioContext`, `AudioBufferSourceNode`, `GainNode`), Canvas 2D, Mediabunny 1.65 (`CanvasSink.canvases`, `AudioSampleSink.samples`, `AudioSample.copyTo` f32-planar), vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` ("Preview and playback" → Play, Preview size, Pictures, Thumbnails / waveforms; "Order of work" step 4; "Calls" 2 — anything a browser lacks is said in plain words). Desktop references: `src/umc/gui/videoedit/player.py` (`Player`: loop, `NO_SOUND`, end handling; `thumb_count`, `wave_buckets`, constants), `editor_page.py` (`_play_toggled`, `_stop_playback`, `_playing_position`, `_play_finished`, shortcuts Space / L), `timeline.py` (`set_playhead(follow)`, `_paint_thumbs`, `_paint_wave`, `WAVE_H`), `src/umc/videoedit/preview.py` (`thumbnails`, `waveform`: peak of |mono| per bucket, bucket = sample · buckets // expected, / 32768, ≤ 1).

## Global Constraints

- Work only in the worktree `G:\Claude\Universal-Media-Converter\.claude\worktrees\web-video-editor` (branch `web-video-editor`).
- The gate stays: `EDITORS` keeps `available: false` for `"video"`, `WEB_LATER` keeps `"video"` (step 6 changes both).
- No new dependencies. Mediabunny stays a dynamic import; nothing new enters the page bundle (all new files are reached only from `ui/videoeditor/page.ts`).
- Anti-goal kept: playback, thumbnails and waveforms never load FFmpeg.wasm (they read the bank's decodable file, a copy if one was made).
- Desktop numbers: `THUMB_HEIGHT 56`, `THUMB_EVERY_PX 120`, `THUMB_MAX 40`, `THUMB_PPS 80`, `WAVE_PER_SECOND 50`, `WAVE_MAX 4000` (min 50 buckets), `WAVE_H 16`, `FOLLOW_LEAD 0.05`; play starts at the playhead unless it is within 0.02 s of the end (then 0); at the end the playhead sits at `length(p)` and the still is shown; with Loop on it starts again from 0. `NO_SOUND = "No sound device — preview is silent"` shown once per page.
- Any edit, seek, undo / redo, opening files or leaving stops playback (like the desktop) — stopping shows the still at the playhead.
- Volume: the audio editor's slider and setting — `VOLUME_KEY` (`umc-audio-volume`, 0…100), `DEFAULT_VOLUME`, `gainFor` from `audioedit/volume.ts`; the slider sits in the transport row, `aria-label` "Volume".
- Sound is mixed by `mixWindow` + one `Limiter` carried across chunks (`videoedit/mix.ts`); nothing else mixes.
- e2e hook `window.__videoeditor` only with `?e2e`.
- Commits: identity `darkbepo`, imperative subject, no trailers, no mention of AI. Don't push. Don't touch README.

## Review Focus

1. **A late frame or chunk after stop**: a frame decoded or a sound chunk mixed for a playback that was stopped (seek, edit, leaving, Loop restart) never paints / plays; every async step checks the run is still current. → Task 4 e2e "seek while playing", unit test on `FrameStream` close.
2. **Transitions and speed ≠ 1 while playing**: two clips visible at once each get their own stream; a 2× clip's frames come at the source time `compose.ts` gives and its sound is stretched by `mix.ts`. → Task 3 `FrameStream` tests, Task 4 e2e "plays across a crossfade".
3. **Sound not decoded in time**: a chunk whose blocks aren't ready plays silence for that chunk instead of stalling the clock; the store never decodes the same block twice at once. → Task 2 tests.
4. **No sound device / AudioContext refused**: playback still runs on a `performance.now()` clock, silent, with `NO_SOUND` once. → Task 4 unit test on `Clock` choice + e2e with the hook forcing it.
5. **Relink / remove / a new project**: the scanner and the sound store drop that source's thumbnails, waveform and blocks (keyed by source id, cleared by `forget(id)`), so a relinked source never shows the old file's pictures. → Task 5 e2e.

---

### Task 1: Playback maths and resampling (DOM-free)

**Files:**
- Create: `web/src/ui/videoeditor/playmath.ts`, `web/src/videoedit/resample.ts`
- Test: `web/tests/videoedit-playmath.test.ts`

**Interfaces:**
- Consumes: `mixWindow`, `SAMPLE_RATE`, `AudioLookup` (`videoedit/mix.ts`); `Source`, `Project`, `length` (`project.ts`); `Rect` (`timelinemath.ts`).
- Produces:
  ```ts
  // playmath.ts
  export const THUMB_HEIGHT = 56, THUMB_EVERY_PX = 120, THUMB_MAX = 40, THUMB_PPS = 80;
  export const WAVE_PER_SECOND = 50, WAVE_MAX = 4000, WAVE_H = 16;
  export const CHUNK_SECONDS = 0.5, LEAD_SECONDS = 1.0, END_SLACK = 0.02;
  export const NO_SOUND = "No sound device — preview is silent";
  export function thumbCount(s: Source): number;            // image 1, else clamp(ceil(dur·PPS/EVERY), 1, MAX)
  export function waveBuckets(s: Source): number;          // clamp(round(dur·50), 50, 4000)
  export function thumbTimes(s: Source, count: number): number[]; // (i + 0.5) · dur / count; image [0]
  export function startTime(playhead: number, total: number): number;
  export interface Need { sourceId: string; from: number; to: number } // source seconds
  export function audioNeeds(p: Project, start: number, frames: number): Need[]; // mixWindow with a recording lookup, merged per source
  export function thumbSlots(rect: Rect, aspect: number, count: number, header: number, width: number): { index: number; rect: Rect }[]; // _paint_thumbs
  export function waveBars(rect: Rect, peaks: ArrayLike<number>, in_: number, out: number, duration: number, header: number, width: number): { x: number; half: number }[]; // _paint_wave (every 2 px, even x)
  export class PeakAccumulator { constructor(buckets: number, expected: number); add(mono: Float32Array): void; result(): Float32Array | null; } // preview.waveform's bucketing; null when nothing was added
  // resample.ts
  export function toStereo48k(chs: Float32Array[], rate: number): Float32Array[]; // mono → both; >2 channels → first two; linear interpolation; rate 48000 → copies
  ```

- [ ] **Step 1: tests** (`videoedit-playmath.test.ts`): `thumbCount` for a 10 s video = 7, 0.1 s = 1, 1000 s = 40, picture = 1; `waveBuckets` 0.5 s = 50, 10 s = 500, 1000 s = 4000; `startTime(3.99, 4)` = 0, `(1, 4)` = 1; `audioNeeds` for a project with one video clip in_ 2 over [0, 0.5) → `[{s1, 2, 2.5}]`, a muted clip → `[]`, a 2× clip → `to − from` = 1 s, a looping music clip crossing its loop point → one merged need covering the clip's [in_, out); `thumbSlots` covers the rect with slots of width `round((h − 16) · aspect)` and skips those left of `header` / right of `width`; `waveBars` maps the clip's in…out onto the peaks and gives `half ≥ 1`; `PeakAccumulator(4, 8)` fed `[0.1, −0.9, 0.2, 0.3, 0, 0, 0.5, 0]` → `[0.9, 0.3, 0, 0.5]`; `toStereo48k` mono 48 k → two equal copies, 24 k of length 2 → length 4 with interpolated middle, 44.1 k length 441 → 480.
- [ ] **Step 2:** run `npx vitest run tests/videoedit-playmath.test.ts` — FAIL (module missing).
- [ ] **Step 3:** implement. `waveBars` / `thumbSlots` follow `timeline.py` line for line (header clip, `(x − rect.x) % 2`, index `int((lo + frac·(hi − lo)) / dur · n)`).
- [ ] **Step 4:** run the test — PASS; `npx tsc --noEmit`.
- [ ] **Step 5:** commit "Add the website video editor's playback maths".

### Task 2: The sound store (worker-safe)

**Files:**
- Create: `web/src/ui/videoeditor/sound.ts`
- Modify: `web/src/ui/videoeditor/sources.ts` (add `audio(id)` and `file(id)`; keep at most the two newest `CanvasSink` heights per source — the step 3 leak)
- Test: `web/tests/videoedit-sound.test.ts`

**Interfaces:**
- Consumes: `toStereo48k` (Task 1); `AudioLookup`, `SAMPLE_RATE` (`mix.ts`).
- Produces:
  ```ts
  // sound.ts — no DOM, no AudioBuffer (works in the export worker)
  export const BLOCK_SECONDS = 4;
  export const BUDGET_BYTES = 256 * 1024 * 1024;
  export interface Decoded { timestamp: number; rate: number; channels: Float32Array[] } // one decoded piece
  export type Decoder = (sourceId: string, from: number, to: number) => AsyncIterable<Decoded>;
  export class SoundStore {
    constructor(decode: Decoder);
    ensure(needs: { sourceId: string; from: number; to: number }[]): Promise<void>; // decodes missing blocks; one decode per block at a time
    readonly lookup: AudioLookup;            // sync; null when any covering block is missing
    forget(sourceId: string): void;          // also drops decodes under way (their results are discarded)
    dispose(): void;
  }
  // sources.ts additions
  SourceBank.audio(id): Decoder-compatible iterator source — `samples(id, from, to): AsyncIterable<Decoded>` via AudioSampleSink (copyTo "f32-planar" per channel, sample.close())
  SourceBank.file(id): File | null           // the decodable file (the copy when one was made)
  ```
- Blocks: block `k` holds source seconds `[k·4, (k+1)·4)` as 48 kHz stereo (pieces placed by `round((timestamp − k·4)·48000)`, gaps silent); `lookup` slices across blocks. Over budget, the least recently used blocks go (never a block a pending `ensure` needs).

- [ ] **Step 1: tests** with a fake `Decoder` that yields a ramp at 48 k (and one at 24 k): `ensure` then `lookup(s, 1, 4800)` returns the ramp's samples; a range across a block edge (3.9 … 4.1 s) is continuous; before `ensure`, `lookup` is null; two `ensure` calls for the same block decode it once; `forget` during a decode → `lookup` stays null; the budget evicts the oldest block (budget lowered by a constructor option `budgetBytes`); a 24 k source comes back at 48 k length.
- [ ] **Step 2:** run — FAIL.
- [ ] **Step 3:** implement `SoundStore`, then `SourceBank.samples` / `file` / the two-heights sink limit (closing nothing it still returns — sinks have no close; just drop the map entry).
- [ ] **Step 4:** run the test, `npx tsc --noEmit`, and `npx playwright test -c playwright.edge.local.config.ts e2e/videoeditor.spec.ts` (sources still work) — PASS.
- [ ] **Step 5:** commit "Decode video editor sound in blocks for the mix".

### Task 3: Frame streams (DOM-free)

**Files:**
- Create: `web/src/ui/videoeditor/framestream.ts`
- Test: `web/tests/videoedit-framestream.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface Timed<T> { timestamp: number; duration: number; value: T }
  export class FrameStream<T> {
    constructor(open: (from: number) => AsyncIterator<Timed<T>>, ahead?: number /* default 3 */);
    at(t: number): T | null;   // newest frame with timestamp ≤ t (null before the first arrives); reads ahead in the background
    close(): void;             // stops reading (calls iterator.return()); at() → null afterwards
  }
  ```
- A request for a `t` more than 1 s behind the shown frame or more than 2 s ahead of the read position reopens the iterator at `t` (a seek inside a clip after a loop / transition).

- [ ] **Step 1: tests** with a fake iterator of frames every 0.1 s: `at(0.25)` after the reads settle → the 0.2 frame; never more than `ahead` frames read past `t`; `at(5)` reopens at 5; `close()` calls `return()` and a frame arriving afterwards is dropped; an iterator that throws makes `at` keep the last frame (and not throw).
- [ ] **Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** run — PASS, `npx tsc --noEmit`.
- [ ] **Step 5:** commit "Add frame streams for video editor playback".

### Task 4: The player, transport and page wiring

**Files:**
- Create: `web/src/ui/videoeditor/player.ts`
- Modify: `web/src/ui/videoeditor/preview.ts` (▶ / ⏸ button, Loop toggle `aria-pressed`, volume slider; `showFrame(canvas source)` for played frames; the still queue cleared while playing), `web/src/ui/videoeditor/page.ts` (play / loop actions, stop on every edit / seek / leave, `follow` while playing, hook additions), `web/src/ui/videoeditor/timeline.ts` (`setPlayhead(t, follow?)` using `timelinemath.follow`), `web/src/styles.css` (only if the transport needs it)
- Test: `web/e2e/videoeditor.spec.ts`, `web/tests/videoedit-playmath.test.ts` (clock choice)

**Interfaces:**
- Consumes: Tasks 1–3; `frameAt`, `textAt`, `drawFrame`, `drawText`, `SourceBank.picture` (pictures: the cached bitmap).
- Produces:
  ```ts
  export interface PlayerEvents { frame(paint: (ctx: CanvasRenderingContext2D, size: [number, number]) => void): void; position(t: number): void; ended(): void; notice(msg: string): void }
  export class Player {
    constructor(bank: () => SourceBank, sound: () => SoundStore, on: PlayerEvents);
    loop: boolean;
    volume: number;               // 0…1, applied with gainFor
    readonly playing: boolean;
    play(p: Project, t: number, size: [number, number]): void;
    stop(): void;
    dispose(): void;              // closes the AudioContext
    stats(): { chunks: number; loudChunks: number; frames: number }; // for the e2e hook
  }
  // playmath.ts addition
  export function pickClock(hasAudioContext: boolean): "audio" | "wall";
  ```
- Play: one `FrameStream` per visible video layer, keyed by item id (opened lazily from `CanvasSink.canvases(time)` at the preview height; closed when its item leaves the frame); each rAF tick draws `frameAt(p, clock)` with the streams' current frames on the offscreen surface and hands it to `preview` (same `drawFrame` / `drawText` as the still). Sound: chunks of `CHUNK_SECONDS` mixed while `scheduled < clock + LEAD_SECONDS`: `await sound.ensure(audioNeeds(...))` (or skip it if its start time has passed → silence), `mixWindow`, `limiter.process`, an `AudioBuffer` scheduled at `ctxStart + (chunkStart − t0)` through one `GainNode`. At `length(p)`: stop → `ended`. Hook: `play()`, `stop()`, `playing`, `stats()`, `forceWallClock()`.

- [ ] **Step 1: e2e tests** (fixtures `red.mp4`, `clip.mp4` with sound, `blue.png`, a tone if needed from `global-setup.ts`): "Space plays and the playhead moves" (playhead > 0.3 after 0.8 s, ▶ shows ⏸), "playing to the end stops at the end" (playhead ≈ `length` within 0.05, still shown, not playing), "Loop starts again" (L, play near the end → playhead goes back below 1), "seek while playing stops and shows the still at the seek", "an edit while playing stops", "sound plays" (`stats().loudChunks > 0` for `clip.mp4`, `0` when the clip is muted), "plays across a crossfade" (two clips + crossfade: frames > 0 and no error), "no sound device" (`forceWallClock()` then play: playhead moves, notice `NO_SOUND`), "the volume slider keeps its value" (reload → same value, key `umc-audio-volume`). vitest: `pickClock(false)` = "wall".
- [ ] **Step 2:** run them — FAIL.
- [ ] **Step 3:** implement the player, then the preview / page / timeline wiring. Replace the step-3 "Playback comes soon" buttons.
- [ ] **Step 4:** run `npx vitest run`, `npx tsc --noEmit`, the whole `e2e/videoeditor.spec.ts` in Edge — PASS.
- [ ] **Step 5:** commit "Play the project with sound in the website video editor".

### Task 5: Thumbnails and waveforms on the timeline

**Files:**
- Create: `web/src/ui/videoeditor/scanner.ts`
- Modify: `web/src/ui/videoeditor/timeline.ts` (paint thumbnails / waveform inside clips with `thumbSlots` / `waveBars`: video / picture clips get thumbnails above a `WAVE_H` band, video clips the wave in that band, music clips the wave over `h − 12`; colour `rgba(255,255,255,0.67)`), `web/src/ui/videoeditor/page.ts` (scanner per session; `want(sources)` after every change; `forget(id)` with the bank's), hook
- Test: `web/e2e/videoeditor.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  export class Scanner {
    constructor(bank: () => SourceBank, on: { thumbs(id: string, images: ImageBitmap[]): void; wave(id: string, peaks: Float32Array): void });
    want(sources: Source[]): void;   // scans unseen ids one at a time (thumbs, then wave), re-sends cached ones
    forget(id: string): void;
    dispose(): void;                 // stops the running scan; nothing is sent afterwards
  }
  ```
- Thumbnails: `thumbCount` frames at `thumbTimes` from a `CanvasSink` at `THUMB_HEIGHT` (pictures: the bitmap scaled once), as `ImageBitmap`s. Waveform: the source's sound through `SourceBank.samples` (whole file, mono = channel average, `PeakAccumulator(waveBuckets, round(duration · rate))`, pieces processed and dropped — never the whole file in memory).

- [ ] **Step 1: e2e tests:** "clips show thumbnails and a waveform" (hook `scanned()` → `{thumbs: {s1: n}, waves: {s1: buckets}}` with n = `thumbCount`, waves only for sources with sound; the clip's top-left pixel on the timeline canvas is no longer the plain clip colour); "a relinked source is scanned again" (relink via the locate flow → the old id's cached result is replaced, scan count 2); "leaving stops the scan" (leave mid-scan → no error, nothing logged afterwards).
- [ ] **Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** `npx vitest run`, `npx tsc --noEmit`, `e2e/videoeditor.spec.ts` in Edge — PASS.
- [ ] **Step 5:** commit "Show thumbnails and waveforms in the website video editor".

### Task 6: CLAUDE.md

- [ ] Add one line after "Website: step 3 page ✅ …": "Website: step 4 playback ✅ — player.ts (AudioContext clock, else wall clock + NO_SOUND; 0.5 s chunks mixed 1 s ahead through mix.ts + one Limiter), sound.ts (4 s 48 kHz blocks, worker-safe, reused by export), framestream.ts, scanner.ts (thumbnails / waveforms keyed by source id); volume = the audio editor's `umc-audio-volume`." Commit "Note the website video editor's playback in the roadmap".
