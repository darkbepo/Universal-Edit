# Convert screen redesign (desktop + website)

Status: approved in chat on 2026-11-10 (layout from mockups, style "calm & refined").

## Goal

Make the Convert screen of both apps look and feel like a 2026 app and use a 16:9 screen
properly, without changing what can be converted or how converters work.

## Layout

Wide window (desktop default 1280×800, remembered; minimum ~760×560):

```
┌ top bar ── app name ─────────────────────────── [ Convert | Edit ] ┐
├ left column ──────────┬ right column ──────────────────────────────┤
│ drop / click zone     │ ┌ Convert to ⚙ ───────────────────────────┐│
│ (slim once files in)  │ │ [Image ▾] [Video ▾] [Document ▾] …      ││
│                       │ │  category cards with format tiles,      ││
│ Queue · 3 images Clear│ │  all open by default, each foldable     ││
│ ▢ beach.heic  2.1 MB ×│ └─────────────────────────────────────────┘│
│ ▢ sunset.jpg  ▬▬▬ 55 %│  or, after clicking ⚙:                     │
│ Results (after a run) │ [⇄ strip] ┌ Settings · JPG ───── Reset ┐   │
│                       │           │ the existing auto form      │   │
├ bottom bar ── summary · Save to ▾ · progress · [Convert] ─────────┤
```

- **Convert | Edit** is always in the top bar; Edit is disabled (with a tooltip) when the files
  can't be edited. Behaviour of entering/leaving the editor is unchanged.
- **Drop zone**: large when empty; a slim "Add more files" bar once files are added. The whole
  window still accepts drops.
- **Queue**: one row per input file — thumbnail (real preview for images the toolkit can decode
  quickly, otherwise the family icon), name, details (size; dimensions when known), remove ×.
  Rows can be dragged to reorder (order matters for slideshows / merges).
  While converting each row shows its own progress bar; finished rows show a ✓, and when the job
  fails the row that was in progress shows the error state.
- **Results**: after a successful run a "Results" list appears above the queue with every output
  (name, size; desktop: click opens the file's folder, web: download link), plus the existing
  "Open folder" (desktop) / "Download all (.zip)" and "Save to folder…" (web) actions.
  (Deviation from the chat sketch, where each queue row had its own open button: outputs don't
  map 1:1 to inputs for many targets — slideshow, merge, split, pages — so one results list is
  used for all of them.)
- **Right column — two sideways tabs.** "Convert to" is open by default: every target group is a
  card (title + chevron, tiles with icon + label), all open; clicking a card's title folds it.
  The selected tile is accent-tinted. A cog button ("Settings") sits in the "Convert to" header.
  Clicking it opens the **Settings** pane (titled "Settings · <format>", Reset in its header);
  "Convert to" collapses into a thin vertical strip showing its icon and the chosen format.
  Clicking the strip returns to "Convert to". The settings form is the existing auto form,
  restyled. The cog is hidden when the target has no visible settings.
- **Bottom bar**: one-line summary of files and target ("3 images → JPG"), status text (errors, done), "Save to" menu
  (desktop), overall progress bar, Convert (becomes Cancel while running).
- **Narrow** (desktop < 900 px wide, web < 900 px): one column in the same order — drop, queue,
  results, right column, bottom bar.

## Per-file progress

No converter changes. Converters already report overall progress in equal slices per file
(`i / n … (i + 1) / n`), so row *i* shows `clamp(overall · n − i, 0, 1)`. For jobs that combine
files this still reads naturally (inputs are read in order). Pure helper with unit tests in both
apps (`file_fractions(overall, n)` / `fileFractions`).

## Visual style — calm & refined

- Tokens (same names in `gui/theme.py` and `web/src/styles.css`): softer neutral `bg`,
  white/raised `surface`, `surface_alt`, hairline `border`, `text`, `muted`, refined indigo
  `accent` (+ hover, soft, text), `danger`, `success`, plus `shadow` (web) and larger radii
  (cards 16 px, tiles 12 px, controls 10 px).
- Accent only on: selected tile, Convert | Edit selection, progress bars, Convert button, focus.
- Type: 14 px body (desktop 13→14 px), 600-weight headings, muted section labels in sentence case.
- Motion (web): 150–200 ms transitions for card folding, pane switch, progress; respects
  `prefers-reduced-motion`. Desktop: instant (Qt stylesheets don't animate).
- Icons: Tabler outline icons (MIT), a small copied set — `src/umc/gui/icons.py` (SVG strings,
  rendered with QtSvg, tinted to the palette) and `web/src/ui/icons.ts`. Replaces the emoji.
- Light/dark follows the OS as today.

## Architecture

Desktop (`src/umc/gui/`):
- `main_window.py` keeps the window, state and conversion flow; layout rebuilt as above.
- New widgets: `widgets/file_queue.py` (queue rows + results list), `widgets/format_picker.py`
  (rewritten: category cards + tiles, same `target_changed` signal / `set_targets` API),
  `widgets/side_tabs.py` (Convert to / Settings panes with collapsed strip),
  `widgets/drop_zone.py` (restyled, icon), `icons.py`.
- `style.qss` + `theme.py` updated; editor styles untouched.
- Thumbnails: `QImageReader` with a scaled size for formats Qt reads natively, else family icon.

Web (`web/src/ui/`): `app.ts` keeps the state/flow; DOM rebuilt in the same structure; new
`queue.ts` (rows, thumbnails via object URLs for browser-decodable images), `icons.ts`;
`styles.css` rewritten. Existing hooks stay: `.pill[data-target]` on tiles, `.status`,
`.results li` / `a.fname`, `.files-head`, `.note`, `.form`, button names "Convert", "Cancel",
"Clear", "Settings" (the cog), "Edit".

## Testing

- Desktop: update `tests/test_gui.py` / `test_gui_editor.py` for the new widgets; add tests for
  the side tabs (cog opens settings, strip returns), category folding, queue rows / per-file
  progress, results list, Edit disabled instead of hidden. Unit-test `file_fractions`.
- Web: vitest for `fileFractions`; e2e helpers adapted (tiles are hidden while Settings is open —
  `convert()` only clicks a visible tile); new e2e for tabs and folding.
- Full desktop pytest, ruff, `tsc`, vitest, Playwright e2e. Self-test step renders an icon (QtSvg
  in the frozen build).

## Out of scope

Converter behaviour, the editors, the tool-install dialog, per-file cancel/retry.
