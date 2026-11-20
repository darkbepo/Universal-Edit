# Subtitle editor (both apps) — design

Date: 2026-11-20. Roadmap item 16.3.

## Why

Subtitle files (SRT, VTT, ASS, SSA, SMI, LRC) can be converted, shifted as a whole, burnt in or
added as a track, but fixing one line's text or timing means opening the file in the code editor
and counting milliseconds by hand. The start screen already shows a "Subtitle editor — Coming
soon" tile; this fills it.

## Goals

- **Where:** the hub tile opens an empty editor ("Open…"); Edit opens a subtitle file in it
  (the code editor stays as the second choice under Edit's ▾). One video plus one subtitle file
  added together → the subtitle editor with that video as its preview (owner's choice).
- **Layout:** a table of cues (#, start, end, length, text, problem mark); under it an edit box
  for the chosen cue (text, start, end, length — typed like the audio editor's time boxes);
  beside it an optional video preview with the current cue drawn over the picture.
- **Editing:** text, start / end / length, add, delete, split (at the caret, else in the middle),
  join, sort by time, set start / end to the play head, find & replace (match case, whole word,
  regex — the code editor's rules), undo / redo.
- **Timing:** shift all or the chosen cues by a typed amount; *sync* = give the right time for two
  cues (usually the first and last) and every cue is stretched between them (fixes drift).
- **Checks:** overlaps, ends before it starts, too short (< 0.7 s), too fast (> 21 characters a
  second), a line over 42 characters, more than two lines, no text. Shown as a mark per row with
  a plain-language note.
- **Saving:** Save writes the same format back; Save as writes SRT, VTT, ASS or LRC (SSA files can
  also go back to SSA; SMI is read only → Save goes to Save as, SRT first). Untouched files save
  byte for byte; ASS / SSA keep their script info, styles and each line's style / margins /
  effect fields; VTT keeps its header and cue settings. Saving into another format lists what
  will be lost (styles, comment lines, positions).
- Same model, behaviour and shortcuts in both apps; shared cases checked by both test suites.

## Non-goals (first version)

- A waveform / timeline strip, per-line styling (colour, position), spell check, translation
  side by side, TTML writing (the converter still does TTML).

## Model (`umc/subedit/` ↔ `web/src/subedit/`, line-by-line ports, no Qt / DOM)

- `formats`: `Cue` (start / end in ms, text with "\n" between lines in the file's own markup,
  `extra` = format fields kept as written), `SubDoc` (format, cues, header, footer, ASS field
  names). `parse(text, fmt)` / `write(doc, fmt)` for srt, vtt, ass, ssa, lrc (+ smi read only),
  markup converted between the HTML-like (SRT / VTT / SMI), ASS (`{\i1}`) and plain (LRC) kinds.
  LRC has no end times: a cue ends where the next starts (or at a blank time stamp), the last
  after 4 s.
- `edits`: pure functions on the cue tuple. `checks`: the problems above. `history`: snapshot undo.
- `io`: bytes ↔ document through `codeedit.textfile` (same encoding, BOM and line endings back;
  untouched = the bytes as read). "Reopen with encoding…" uses the converter's charset list and
  then saves UTF-8 (the web has no encoder for legacy charsets, so both apps do the same).
- Limits: 20 MB, 100 000 cues.
- Shared cases `tests/fixtures/subedit_cases.json` (`python scripts/make_subedit_cases.py`, only
  on purpose).

## Views

- Desktop add-on "subtitle" (`umc.gui.subtitleeditor`): `QTableView` over a list model, edit box,
  preview = the viewer's `Playback` + `QVideoWidget` with a label overlay; self-test step.
- Web `web/src/ui/subtitleeditor/`, loaded on demand: a virtualised table (big files), `<video>`
  preview with an overlay (formats the browser can't play show the cues without a picture).
