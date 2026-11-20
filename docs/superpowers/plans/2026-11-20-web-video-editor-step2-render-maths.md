# Website video editor — step 2: picture and sound maths — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The DOM-free maths the website's preview and export will run on: what one frame shows (`compose.ts`), where text sits (`textlayout.ts`), the sound mix (`mix.ts`), pitch-keeping speed changes (`tempo.ts`) and the browser limits (`limits.ts`) — each checked against the desktop's FFmpeg recipe where one exists.

**Architecture:** New pure modules in `web/src/videoedit/` (no DOM, no Mediabunny, no WASM — vitest runs them in node). They don't port a desktop file line by line: the desktop hands the same job to FFmpeg (`src/umc/videoedit/render.py`, `text_ass.py`), so the reference numbers come from FFmpeg itself. A desktop script runs the bundled FFmpeg on tiny solid-colour inputs and writes the colours it produced into a shared fixture, which the web tests replay within a tolerance. `draw.ts` (step 3) will execute `compose.ts`'s output on a Canvas 2D context; this step only produces numbers.

**Tech Stack:** TypeScript (strict), vitest; Python 3.11 + the bundled `packaging/bin/ffmpeg` for generating the fixture.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` ("Architecture", "Grades, transitions", "Text", "Limits", "Order of work" step 2). Desktop references: `src/umc/videoedit/render.py` (`_geometry`, `_fit`, `_video_chain`, `_audio_chain`, `_music_chain`, the xfade / acrossfade joins, `alimiter=limit=0.95:level=0`), `src/umc/videoedit/text_ass.py`.

## Global Constraints

- Work only in the worktree `G:\Claude\Universal-Media-Converter\.claude\worktrees\web-video-editor` (branch `web-video-editor`), with its `.venv` and `web/node_modules`.
- No DOM, no Mediabunny, no WASM in `web/src/videoedit/`. No new dependencies in either app.
- Desktop behaviour is the reference. Where the spec and the desktop disagree, follow the desktop: sound crossfades and fades are **linear** (`acrossfade … c1=tri:c2=tri`, `afade` default), not equal-power.
- Sound format everywhere: planar `Float32Array[]`, 2 channels, `SAMPLE_RATE = 48000`.
- Limit numbers and messages (exact, `limits.ts`): picture side `16000`; FFmpeg-copy source `1024 ** 3` bytes; download export `2 * 1024 ** 3` bytes; project `MAX_LENGTH` / `MAX_ITEMS` are re-exported from `edits.ts`, not redefined. Messages end with a pointer to the desktop app (see Task 6).
- Ranges from the desktop's option schemas: speed 0.25–4, transition 0.1–3 s, text size 1–50 % of the height, outline 0–10 px, offsets −100…100 %.
- Commits: the repo's existing identity (`darkbepo`), imperative subject, no trailers, no mention of AI. Don't push.
- Nothing visible on the website in this step.

## Review Focus

1. **Transitions at their edges** — t exactly at a clip's start, at the end of a transition, at the project's end, a transition on the second item when the first is shorter than the transition. Expect exactly one or two layers, progress clamped to 0…1, never a layer from a clip whose span doesn't contain t. → Task 2 `frameAt edges`.
2. **Fades that overlap a transition or each other** (fade-in 2 s on a 1 s picture) — FFmpeg multiplies them; the fade amount must stay within 0…1. → Task 2 `fades multiply`, Task 5 `fades multiply`.
3. **Music that starts before 0, loops, or runs past the project end** — the mix must start mid-song, wrap exactly at `out - in_`, and stop at `length(project)`. → Task 5 `music start, loop and end`.
4. **Loud sums** (three loud tracks) — output never above 0.95, quiet sound untouched, no click when the gain recovers. → Task 5 `limiter`.
5. **Speeds at the range ends** (0.25×, 4×) and very short inputs (shorter than one WSOLA frame) — the right length out, no NaN, no exception. → Task 4.

---

### Task 1: Desktop — reference cases from FFmpeg and the text layout

**Files:**
- Modify: `scripts/make_videoedit_cases.py` (new sections; keep the existing ones byte-identical)
- Modify: `tests/fixtures/videoedit_cases.json` (regenerated)
- Modify: `tests/test_videoedit_cases.py` (checks the new sections exist and are well formed)

**Interfaces:**
- Produces new top-level keys in `videoedit_cases.json`:
  ```json
  "grade": [{"rgb": [r,g,b], "brightness": int, "contrast": int, "saturation": int, "out": [r,g,b]}],
  "transition": [{"kind": str, "duration": float, "t": float, "a": [r,g,b], "b": [r,g,b],
                  "width": 64, "columns": {"<x>": [r,g,b]}}],
  "fade": [{"fade_in": float, "fade_out": float, "duration": float, "t": float,
            "rgb": [r,g,b], "background": [r,g,b], "out": [r,g,b]}],
  "text": [{"item": <TextItem dict as the project file writes it>, "size": [w,h],
            "x": int, "y": int, "column": 0|1|2, "row": 0|1|2, "font_px": int,
            "fade_in": float, "fade_out": float}]
  ```
- `grade`: a 16×16 `color=c=0xRRGGBB` source → `format=yuv420p` → the item's `eq=…` exactly as `render._geometry` builds it → `format=rgb24`, one raw frame read through a pipe; `out` = the centre pixel. At least 12 cases: brightness / contrast / saturation each at −100, −50, 50, 100 alone, plus 3 combined, on 3 colours (a grey, a saturated red, a skin tone).
- `transition`: two 64×36 solid inputs (red `#d02020`, blue `#2040d0`), each 2 s, joined with `xfade=transition=<kind>:duration=<d>:offset=<2-d>` at 25 fps; `t` = a frame time inside the transition (3 per kind: early / middle / late, on exact frame times); `columns` = the pixel at y = 18 for x = 0, 16, 32, 48, 63. All six kinds in `project.TRANSITIONS` except `none`.
- `fade`: one 2 s solid clip through `fade=t=in:d=…:color=…` / `fade=t=out:st=…:d=…:color=…` as `_video_chain` writes them, sampled at given frame times.
- `text`: computed from `text_ass` itself (`_ANCHORS`, `_BASE`, `_fades`, `round(t.size / 100 * h)`) for all 9 anchors, non-zero offsets, a size of 50, and fades longer than the item (scaled down).
- FFmpeg is found with `umc.tools.ffmpeg` (the bundled one in `packaging/bin`). The script fails with a clear message when it is missing; the tests only read the JSON (no FFmpeg needed to run them).

- [ ] **Step 1:** add `test_render_sections_are_well_formed` to `tests/test_videoedit_cases.py`: every new key exists, each list is non-empty, every colour is three ints 0–255, every transition kind except `none` appears.
- [ ] **Step 2:** run `.venv\Scripts\python -m pytest tests/test_videoedit_cases.py -q` → FAIL (keys missing).
- [ ] **Step 3:** extend the script; regenerate with `.venv\Scripts\python scripts/make_videoedit_cases.py`. Check with `git diff --stat` that the old sections didn't change.
- [ ] **Step 4:** run the test → PASS; `ruff check` + `ruff format --check` clean.
- [ ] **Step 5:** commit `Add FFmpeg reference colours and text layout to the video cases`.

### Task 2: `compose.ts` — what one frame shows

**Files:**
- Create: `web/src/videoedit/compose.ts`
- Test: `web/tests/videoedit-compose.test.ts`

**Interfaces:**
- Consumes: `Project`, `MainItem`, `layout`, `length`, `outputSize`, `clipDuration`, `sourceOf` from `project.ts`.
- Produces:
  ```ts
  export interface Grade { brightness: number; contrast: number; saturation: number } // eq numbers: b/200, 1+c/100, 1+s/100
  export interface Layer {
    itemId: string; sourceId: string; kind: "video" | "picture";
    time: number;                 // source seconds (video: in_ + u * speed, clamped to [in_, out]); 0 for pictures
    crop: { left: number; top: number; right: number; bottom: number }; // fractions 0…1 (pictures: all 0)
    rotate: 0 | 90 | 180 | 270; flipH: boolean; flipV: boolean; fit: string;
    grade: Grade | null;          // null when all three are 0
    fade: number;                 // 0…1, how much the item's own fades cover it with the background
  }
  export interface Placed { layer: Layer; weight: number; clipX: [number, number] | null; offsetX: number }
  export interface Frame { width: number; height: number; background: string; mode: "over" | "add"; layers: Placed[] }
  export function frameAt(p: Project, t: number, size?: [number, number]): Frame;
  export function fitRect(srcW: number, srcH: number, outW: number, outH: number, fit: string):
    { sx: number; sy: number; sw: number; sh: number; dx: number; dy: number; dw: number; dh: number };
  export function gradeGrade(item: MainItem): Grade | null;
  export function gradeRgb(rgb: [number, number, number], g: Grade): [number, number, number];
  export function mixWeights(kind: string, progress: number): { mode: "over" | "add"; a: number; b: number };
  ```
- Rules (from `render.py` and FFmpeg's `xfade`; `progress` P runs 1 → 0 over the transition, P = 1 − (t − start_of_second) / duration, clamped):
  - No main items, or t ≥ the main row's end → no layers (background only). `size` defaults to `outputSize(p)`.
  - Layers are drawn first-clip-first. Each layer = a full frame: background, then the picture placed by `fitRect` (letterbox = fit inside, centred; crop = fill and cut the overflow, centred; stretch = whole frame), after crop → rotate → flips (same order as `_geometry`), then the item's fade as background at alpha `fade`.
  - `fade` = 1 − kin · kout with kin = min(1, u / fade_in), kout = min(1, (dur − u) / fade_out) (each 1 when its fade is 0), u = t − span.start.
  - `fade` kind: mode "over", first weight 1, second weight 1 − P.
  - `fadeblack`: mode "add" (black base, additive): a = P · smoothstep(0.8, 1, P), b = (1 − P) · (1 − smoothstep(0.2, 1, P)), smoothstep(e0, e1, x) = s²(3 − 2s), s = clamp((x − e0)/(e1 − e0)).
  - `wipeleft`: both weight 1, second drawn with `clipX = [width · P, width]`; `wiperight`: second with `clipX = [0, width · (1 − P)]`.
  - `slideleft`: first `offsetX = −width · (1 − P)`, second `offsetX = width · P`; `slideright`: first `+width · (1 − P)`, second `−width · P`.
  - `gradeRgb` = FFmpeg `eq` on yuv420p: RGB → BT.601 limited-range YUV, Y' = (Y/255 − 0.5) · contrast + 0.5 + brightness (×255), U/V' = (U/V − 128) · saturation + 128, back to RGB, rounded and clamped. Must match every `grade` fixture case within **±4** per channel (the fixture is the truth if this formula disagrees; adjust the formula, not the tolerance).

- [ ] **Step 1: tests**, in `videoedit-compose.test.ts`:
  - `fixture transitions` — for each `transition` case, build the two-clip project (pictures of the two colours, 2 s each, the transition on the second), evaluate `frameAt` at `t`, and resolve each sampled column's colour from the layers (a small test helper that applies weights / clipX / offsetX to solid colours) → within ±6 of `columns`.
  - `fixture grades` — `gradeRgb` vs every `grade` case, ±4.
  - `fixture fades` — `fade` vs every `fade` case: background · fade + colour · (1 − fade) within ±4.
  - `frameAt edges` — t = 0, t at the second clip's start with no transition (only the second), t = the transition's last instant (P clamps to 0, two layers), t = main end (no layers), a 1 s first clip with a 3 s transition (normalize caps it — use the `edits.normalize` result).
  - `fades multiply` — fade_in 2 + fade_out 2 on a 1 s picture stays within 0…1 and peaks mid-clip.
  - `source time` — speed 2, in_ 1: t = 0.5 into the clip → time 2.0; past the end clamps to `out`.
  - `fitRect` — 1920×1080 into 1080×1920 for each fit; a 90° rotation swaps the source size first.
- [ ] **Step 2:** `npx vitest run tests/videoedit-compose.test.ts` (in `web/`) → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** tests PASS, `npx tsc --noEmit` clean.
- [ ] **Step 5:** commit `Work out what each frame of a video project shows on the website`.

### Task 3: `textlayout.ts` — where text sits

**Files:**
- Create: `web/src/videoedit/textlayout.ts`
- Test: `web/tests/videoedit-textlayout.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export const FALLBACK_FONT = "DejaVu Sans";
  export interface TextBox {
    itemId: string; lines: string[]; family: string; bold: boolean; italic: boolean; fontPx: number;
    x: number; y: number; column: 0 | 1 | 2; row: 0 | 1 | 2;   // anchor point and which side of the text it is on
    colour: string; outlineColour: string; outlineWidth: number; // outline px at output size (ScaledBorderAndShadow)
    box: { colour: string; opacity: number; padding: number } | null; // padding = outline width, as libass's BorderStyle 3
    alpha: number;                                               // 0…1 from the fades at time t
  }
  export function fontFamily(t: TextItem, has: (family: string) => boolean): string; // t.font if has(t.font) or it is the fallback
  export function textFades(t: TextItem): [number, number];   // = text_ass._fades
  export function textAt(p: Project, t: number, size: [number, number], has: (family: string) => boolean): TextBox[];
  ```
- `textAt`: items with `start ≤ t < end` and non-empty text, rows in order (row 0 first, drawn first), items in row order. `x = round(w · (BASE[column] + x_offset / 100))`, `y = round(h · (BASE[row] + y_offset / 100))`, `BASE = [0.05, 0.5, 0.95]`, `fontPx = round(size / 100 · h)`, lines split on `\r\n` / `\r` / `\n`. alpha: linear up over the fade-in, down over the fade-out (after `textFades`).

- [ ] **Step 1: tests** — `fixture layout` (every `text` case: x, y, column, row, font_px, scaled fades exact), `visible window` (start inclusive, end exclusive, empty text skipped, row order), `alpha` (0 at start with a fade-in, 1 mid, 0.5 halfway through the fade-out), `font fallback`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** PASS + `tsc` clean.
- [ ] **Step 5:** commit `Lay out video text on the website like the desktop`.

### Task 4: `tempo.ts` — speed changes that keep the pitch

**Files:**
- Create: `web/src/videoedit/tempo.ts`
- Test: `web/tests/videoedit-tempo.test.ts`

**Interfaces:**
- Produces: `export function stretch(input: Float32Array[], speed: number, frames?: number): Float32Array[]` — WSOLA time stretch at 48 kHz; output length = `frames ?? Math.round(input[0].length / speed)` (cut or zero-padded to exactly that). Speed 1 returns copies. Inputs shorter than one analysis frame are resampled linearly instead (pitch may change; it's at most a few ms). Parameters are the builder's call (suggested: 40 ms frames, 50 % overlap, Hann window, ±10 ms search by cross-correlation on a decimated signal).

- [ ] **Step 1: tests** — for 0.25, 0.5, 2, 4: exact output length; a 440 Hz sine's frequency (zero crossings over the middle 80 %) within 2 %; no NaN; RMS within 20 % of the input's; `speed 1 is a copy`; `tiny input` (100 samples) gives the right length without throwing; two channels stay independent (silence in one stays silence).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** PASS + `tsc` clean.
- [ ] **Step 5:** commit `Change clip speed on the website without changing the pitch`.

### Task 5: `mix.ts` — the sound of a time window

**Files:**
- Create: `web/src/videoedit/mix.ts`
- Test: `web/tests/videoedit-mix.test.ts`

**Interfaces:**
- Consumes: `stretch` (Task 4); `layout`, `length`, `clipDuration`, `sourceOf` (`project.ts`).
- Produces:
  ```ts
  export const SAMPLE_RATE = 48000;
  /** Planar stereo 48 kHz for source seconds [from, from + frames / 48000); null = not decoded (yet) → silence. */
  export type AudioLookup = (sourceId: string, from: number, frames: number) => Float32Array[] | null;
  export function mixWindow(p: Project, start: number, frames: number, audio: AudioLookup): Float32Array[];
  export class Limiter { constructor(limit?: number /* 0.95 */); process(chs: Float32Array[]): void /* in place */; reset(): void }
  ```
- Rules (from `render.py`): main row = video items with sound (`!mute && source.has_audio`), volume / 100, item fades (linear, multiplied), speed ≠ 1 through `stretch` (ask the lookup for `frames · speed` source frames); pictures and muted clips are silence. Transitions overlap the two items' sound over the transition span — the first fades out linearly, the second fades in linearly, summed. Music: u = t − start (u < 0 → silence); source time = `in_ + (u mod (out − in_))` when `loop_until > 0`, else `in_ + u` while u < out − in_; volume and fades over `clipDuration`. Everything is summed; nothing after `length(p)`. `mixWindow` does **not** limit — the caller runs one `Limiter` across consecutive windows (state carries over), so window edges don't click. Limiter: instant attack to keep |x| ≤ limit, release over ~50 ms; quiet sound (|x| ≤ limit and gain already 1) passes unchanged.

- [ ] **Step 1: tests** (synthetic lookups returning constants or sines) — `volume and mute`, `fades multiply`, `crossfade` (halfway through the transition each side contributes 0.5), `pictures are silent`, `music start, loop and end` (start −1 s begins 1 s into the song; loop wraps at out − in_; zero after `length`), `speed uses stretch` (2× clip asks the lookup for twice the frames), `missing audio is silence`, `limiter` (three 0.6 sums never exceed 0.95; a 0.3 signal is bit-identical; gain recovers within 100 ms; splitting a signal into two windows gives the same output as one).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** PASS + `tsc` clean.
- [ ] **Step 5:** commit `Mix a video project's sound on the website`.

### Task 6: `limits.ts` — the browser limits, in one place

**Files:**
- Create: `web/src/videoedit/limits.ts`
- Test: `web/tests/videoedit-limits.test.ts`
- Modify: `CLAUDE.md` (one line under roadmap 10.16, after the step 1 line)

**Interfaces:**
- Produces:
  ```ts
  export const LIMITS: { pictureSide: number; copyBytes: number; downloadBytes: number }; // 16000, 1024 ** 3, 2 * 1024 ** 3
  export function setLimitsForTests(partial: Partial<typeof LIMITS> | null): void;       // null restores the defaults
  export const DIRECT_VIDEO: ReadonlySet<string>;   // mp4 mov mkv webm m4v
  export const DIRECT_AUDIO: ReadonlySet<string>;   // mp3 m4a aac wav ogg oga opus flac
  export const DIRECT_PICTURE: ReadonlySet<string>; // png jpg jpeg webp gif bmp avif
  export function needsCopy(ext: string, decodes: boolean): boolean; // not a direct type, or the decode check failed
  export function sourceProblem(f: { name: string; size: number; copy: boolean; width?: number; height?: number }): string | null;
  export type Quality = "very-high" | "high" | "medium" | "low";
  export function estimateBytes(o: { seconds: number; width: number; height: number; fps: number;
    quality: Quality | null; videoBitrate?: number; audioBitrate: number; audio: boolean }): number;
  export function exportProblem(estimated: number, streams: boolean): string | null; // streams = save-to-disk available
  export const LIMITS_NOTE: string;
  export { MAX_LENGTH, MAX_ITEMS } from "./edits";
  ```
- Messages (exact; sizes shown with `formatGb` = one decimal, "GB"):
  - copy: `` `${name} is too big for the browser (over 1 GB for this kind of file) — use the desktop app.` ``
  - picture: `` `${name} is too big for the browser (over 16,000 pixels on a side) — use the desktop app.` ``
  - export: `` `This video would be about ${gb} — too big for this browser (2 GB without saving straight to disk). Use Chrome or Edge, a lower quality, or the desktop app.` ``
  - `LIMITS_NOTE`: `"Browser limits: files the browser can't read directly must be under 1 GB, pictures under 16,000 pixels a side, and without Chrome or Edge an export must stay under 2 GB. The desktop app has none of these."`
  - The numbers in messages follow `LIMITS` (lowered limits in e2e show lowered numbers).
- Estimate: bits per pixel per frame by quality — very-high 0.20, high 0.10, medium 0.05, low 0.025 — × w × h × fps × seconds, or `videoBitrate × seconds` when given; plus `audioBitrate × seconds` when `audio`; ÷ 8; × 1.02 for the container.

- [ ] **Step 1: tests** — `needsCopy` (mp4 decodes → false; mp4 fails the decode check → true; avi → true; case-insensitive), `sourceProblem` (exactly at the limit passes, one byte over fails, the picture side, the message text), `estimateBytes` (a hand-computed case for each quality and for a bitrate), `exportProblem` (streaming never refused; download over 2 GB refused with the message), `setLimitsForTests` (lowered numbers show in the message; null restores).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** PASS + `tsc` clean; whole `npm test` green.
- [ ] **Step 5:** CLAUDE.md line: `Website: step 2 maths ✅ — compose / textlayout / mix / tempo / limits in web/src/videoedit/ (no DOM); FFmpeg reference colours in videoedit_cases.json (grade, transition, fade, text).`
- [ ] **Step 6:** commit `Add the website video editor's browser limits`.
