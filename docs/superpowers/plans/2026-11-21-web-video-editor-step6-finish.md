# Website video editor, step 6: phones, live tile, docs, cross-app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** finish the website video editor: a phone layout, the start tile and Edit switch live for every visitor, README / CLAUDE.md updated, a cross-app project-file test — and fix the desktop Linux CI failure on `main` (`test_coming_soon_badges_are_never_cut_off[1280]`, tile "vector") so the release builds start green.

**Architecture:** CSS + a small phone mode in `web/src/ui/videoeditor/page.ts` (bottom sheets for media / inspector, like the image editor's `FloatingHost.onlyOnPhone` idea), touch gestures as pure maths in `timelinemath.ts`; remove the `?video-editor` gate; desktop hub tile layout fix in `src/umc/gui/` (hub widgets).

**Tech Stack:** TypeScript, CSS, Playwright; Python / PySide6 for the hub fix.

**Spec:** `docs/superpowers/specs/2026-11-14-web-video-editor-design.md` (sections "Phones (≤ 560 px)", "Start screen and switch", "Testing" → Cross-app, "Calls" point 3).

## Global Constraints

- Phones (≤ 560 px, `isPhone()` in `ui/back.ts`): preview on top (16:9 box, full width), transport under it, timeline below filling the rest (taller rows, drag handles ≥ 24 px, pinch = zoom, two-finger drag = scroll). Top bar: "← Back", Undo, Redo, Export; Save / Save as / Add files in `.top-tools`. Media list and the inspector are bottom sheets (one at a time), opened by buttons in the toolbar row and by selecting an item (inspector). Check at 375 px wide in the e2e suite.
- Remove `"video"` from `WEB_LATER` in `src/umc/core/editors.py` and the web `available: false` in `web/src/core/editors.ts` (the hub test keeps both lists in step). The tile opens an empty project ("Add files" / drop).
- `editChoices()` in `ui/app.ts` offers `"video"` for every visitor (no `?video-editor` / `?e2e` gate); `.umcvideo` is already family "project".
- README "What it can do" video-editor rows: website column ✅ (with the limits footnote); CLAUDE.md roadmap 10.16 updated.
- Cross-app: the desktop's `load_project` opens a web-saved `.umcvideo`, and the web opens a desktop-saved one (python in the e2e setup, like the document editor's cross-app test; skipped without a Python that has `umc`).
- Commits: imperative subject, author darkbepo, no trailers. Never `git stash`.

## Review Focus

1. A phone rotated / resized across 560 px while a sheet is open → the layout switches cleanly (no sheet left floating on desktop, no hidden panel on phone).
2. Pinch on the timeline while a clip is under one finger → zooms, never moves / trims the clip.
3. Old links with `?video-editor` keep working (the parameter is simply ignored).
4. A desktop-saved project whose files sit next to it → the web's locate window matches them all by name + size in one pick.
5. The desktop hub at other widths (1100 / 1440 / 1920) still passes after the badge fix, on Windows and Linux.

---

### Task 1: Desktop hub badge test on Linux

**Files:**
- Modify: the hub tile widget under `src/umc/gui/` (find `EditorHub` / tile class used by `window.hub.tiles`), possibly `src/umc/gui/style.qss`
- Test: `tests/test_editor_hub.py::test_coming_soon_badges_are_never_cut_off`

**Interfaces:** none new.

CI log (Linux, both ubuntu-latest and Debian 12): `AssertionError: (1280, 'vector')` at `tests/test_editor_hub.py:305` — the title or badge of the "Vector editor" tile is narrower than its size hint at 1280 px. Linux has no Segoe UI; Qt falls back to a wider font (DejaVu Sans is the likely face; the repo has it in `packaging/fonts` after setup).

- [ ] **Step 1:** reproduce on Windows: in a scratch test (not committed) or by a temporary fixture, set the app font to DejaVu Sans (`QFontDatabase.addApplicationFont` on `packaging/fonts/DejaVuSans.ttf`, then `app.setFont`) and run the test → it fails like CI. If it doesn't reproduce, report BLOCKED with what you tried.
- [ ] **Step 2:** fix the tile layout so the title wraps (or the badge shortens to "Soon") whenever the width is short, for any font — not by special-casing a width. Make the font swap part of the committed test (parametrise it: system font and DejaVu Sans) so Windows CI catches it too.
- [ ] **Step 3:** `.venv/Scripts/python.exe -m pytest tests/test_editor_hub.py -q`, then the whole suite `.venv/Scripts/python.exe -m pytest -q -x` (long), `ruff check .`, `ruff format --check .`.
- [ ] **Step 4:** commit `Keep hub tile titles and badges whole with wider fonts`.

### Task 2: Phone layout and touch gestures

**Files:**
- Modify: `web/src/ui/videoeditor/page.ts`, `web/src/ui/videoeditor/timeline.ts`, `web/src/ui/videoeditor/timelinemath.ts`, `web/src/styles.css`
- Test: `web/tests/videoedit-timelinemath.test.ts` (or the existing timelinemath test file), `web/e2e/videoeditor.spec.ts`

**Interfaces:**
- Produces in `timelinemath.ts`:
  - `pinchView(view: View, startDistance: number, distance: number, centreX: number, startView: View): View` — zoom (pixels per second) scaled by `distance / startDistance` around `centreX`, clamped like the wheel zoom; the time under `centreX` stays under it.
  - `panView(view: View, dx: number, dy: number): View` — two-finger drag scrolls (time and rows), clamped like the scrollbars.
  - `TOUCH_HANDLE_PX = 24` and the row height on phones (`PHONE_ROW_PX`, builder's choice ≥ 44).
- Page on phones: a toolbar row `.ve-phone-bar` with buttons "Media" and "Settings" (aria-pressed); sheets = `.video-media.sheet` / `.video-inspector.sheet` (one open at a time, a close button, Esc closes); selecting an item opens the inspector sheet; "Add files" in `.top-tools`. Leaving phone width closes the sheets and restores the desktop grid.
- Timeline: with two touch pointers down, gestures go to `pinchView` / `panView` and any started clip drag is cancelled (no edit, no undo step); a single touch keeps today's behaviour with `TOUCH_HANDLE_PX` handles.

- [ ] **Step 1:** failing vitest cases: `pinchView` doubles zoom for distance ×2 and keeps the time under the centre; clamps at the limits; `panView` clamps at 0 and the end.
- [ ] **Step 2:** implement; vitest passes.
- [ ] **Step 3:** failing e2e at 375 × 812 (`page.setViewportSize`): no horizontal page scroll; preview box above transport above timeline, preview full width at 16:9 (± 2 px); "Media" opens the media sheet, "Settings" swaps it for the inspector (only one visible); selecting a clip opens the inspector sheet; Esc closes; resizing to 1280 shows the three-column layout with no sheet; a two-pointer touch pinch (dispatch `pointerdown`/`pointermove` with `pointerType: "touch"` on the timeline canvas) increases the zoom and leaves the project unchanged (`history.steps` same).
- [ ] **Step 4:** implement page + CSS + timeline wiring; the whole `e2e/videoeditor.spec.ts` passes (`npx playwright test -c playwright.edge.local.config.ts e2e/videoeditor.spec.ts`, one run at a time); `npx tsc --noEmit`, `npx vitest run`.
- [ ] **Step 5:** commit `Fit the website video editor to phones`.

### Task 3: Live for everyone, docs

**Files:**
- Modify: `web/src/core/editors.ts`, `src/umc/core/editors.py`, `web/src/ui/app.ts`, `web/e2e/hub.spec.ts` (and any e2e that expects the video tile to be "Coming soon" or uses `?video-editor`), `web/e2e/videoeditor.spec.ts` (drop the gate from URLs where it only served the gate; keep `?e2e` for hooks), `tests/test_editor_hub.py` (if it asserts WEB_LATER), `README.md`, `CLAUDE.md`

**Interfaces:**
- `videoEditorGate` / `videoTileGate` removed (no remaining callers); `WEB_LATER = frozenset()` stays defined (empty) so the hub test still compares lists.
- README: row 45 drops "(desktop; website coming soon)" → "(desktop and website)"; the big table's video-editor row website column → "✅ ¹" with footnote "¹ In the browser: MP4 / WebM / MKV / MOV / GIF export (no AVI), files the browser can't read directly under 1 GB, pictures under 16,000 px a side, downloads under 2 GB without Chrome / Edge; the desktop app is much faster." (place it with the table's other footnotes, numbered to fit); the start-screen row's website column drops "(video editor: "Coming soon")"; line ~332 stops listing "the video editor" among desktop-only things.
- CLAUDE.md roadmap 10.16: replace "Website: still "Coming soon" (`core/editors.WEB_LATER`)." with "Website: ✅ (steps below)."; add "Website: step 6 ✅ — phones (sheets, pinch / two-finger pan in timelinemath), gate removed (tile + Edit for everyone), cross-app .umcvideo test."; in item 10.9 drop the "the website's video tile is, `WEB_LATER`" remark.

- [ ] **Step 1:** update the hub / switch e2e expectations first (video tile available, Edit offers video without query parameters, an old `?video-editor` URL still works) → they fail.
- [ ] **Step 2:** make the changes; `npx tsc --noEmit`, `npx vitest run`, `.venv/Scripts/python.exe -m pytest tests/test_editor_hub.py -q`, the full web e2e suite once (`npx playwright test -c playwright.edge.local.config.ts`; the known local-only failures are `hub.spec` "Coming soon labels stay whole…" — re-check it, it may now pass or need the tile count — and `properties.spec` needing `python3`).
- [ ] **Step 3:** commit `Open the website video editor to everyone`.

### Task 4: Cross-app project files

**Files:**
- Modify: `web/e2e/global-setup.ts`, `web/e2e/videoeditor.spec.ts`
- Create (if needed): `web/e2e/videoedit_cross.py` — a tiny script run by the e2e with the found Python: `save <json-spec> <out>` writes a desktop project with `umc.videoedit.projectfile.save_project` over fixture files; `load <path>` prints `load_project`'s project as JSON (items, durations, text, missing sources).

**Interfaces:**
- Consumes: `findPython(check)` from `global-setup.ts` (use check `"import umc.videoedit.projectfile"`), the page's save-project download and the locate window.

- [ ] **Step 1:** e2e "a web-saved project opens on the desktop": build a project in the page (video clip, picture, music, text), Save project (download), run `load` on it next to the fixture files → same number of main items, music clips and texts, durations within 0.01 s, text equal, no missing sources. `test.skip` without Python.
- [ ] **Step 2:** e2e "a desktop-saved project opens on the website": `save` a project over the fixture files (in global-setup or in the test), open the `.umcvideo` in the page, pick the files in the locate window in one go → every source matched, the timeline has the same items / durations as the desktop project.
- [ ] **Step 3:** run both (and the whole videoeditor file); commit `Check video projects between the desktop and the website`.
