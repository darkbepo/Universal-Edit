# Properties mode — design (Nov 2026)

The owner's request: next to **Convert | Edit**, a **Properties** switch, like Windows' file
Properties: see what a file is and change the details people actually want to change, with
different rules per kind of file. Both apps.

## Decisions (owner, 12 Nov 2026)

1. Saving on desktop changes the original after one "this changes the original" confirmation,
   with **Save as copy** beside it.
2. mutagen (GPL-2.0-or-later, compatible with AGPL-3.0) may be added for music tags.
3. Both apps from the start.
4. The field list below is fine; more can come later.

## Steps

1. ✅ The switch and the read-only **About this file** panel (this step).
2. Photos: title, description, author, copyright, tags, date taken, rotation flag; remove
   location / remove all hidden details. Lossless: only the EXIF / XMP segment is replaced
   (JPEG APP1, PNG eXIf / iTXt, WebP EXIF chunk), the picture bytes stay. Windows reads Title /
   Authors / Tags from the XP* tags (UTF-16), so they are written too. HEIC / RAW read-only.
3. Music: title, artist, album, album artist, track / disc, year, genre, cover picture (mutagen
   on desktop; ID3v2.3 for MP3 so Windows and every player read it). Web: FFmpeg remux.
4. Video: title, description, date, rotation flag, track language / default (FFmpeg
   `-map 0 -c copy`; check first which streams the container can't keep and warn).
5. PDF (incremental save keeps signatures; password asked) and Word / Excel / PowerPoint / ODF
   (core.xml / meta.xml replaced in the zip).
6. Desktop only: file name and "modified" date.

Several files at once: fields that differ show "(several values)" and only change when typed in.
Every save writes a new file next to the original and swaps it in when complete; a file another
program holds open is reported in plain language. The web downloads a changed copy (Chrome /
Edge can write back to a file picked with the File System Access API).

## Step 1 — how it works

- **Same text in both apps.** Each app reads a file with its own engines into plain "facts"
  (desktop `umc/properties/read.py`: Pillow, ffprobe, PyMuPDF, zip + XML, fontTools, the 3D
  viewer's loader; web `web/src/converters/properties.ts`, a hidden converter in the worker:
  ImageMagick for pictures and EXIF, Mediabunny for video / music, MuPDF, fflate, an own font
  table reader, the 3D viewer's reader). `info.build` (desktop) and `build` (web) are
  line-by-line ports that turn facts into sections; both test suites check
  `tests/fixtures/properties_cases.json` and `properties_format_cases.json`.
- Dates are turned into local display text while reading, so the builders don't depend on the
  time zone. EXIF dates have no zone and are shown as written; MP4 / Office / PDF dates are
  converted to local time.
- Sections: File · Picture · Camera · Details · Location · Video · Audio (track n) · Subtitles ·
  Tags · Document · Font · 3D model · Note. Empty sections are left out.
- Something that can't be read leaves the "File" section and a plain note; it never fails.
- Desktop: right column = `gui/properties/panel.py` (file picker when several files, cards of
  label / value rows, read on a `TaskRunner`; PDFs on its single PyMuPDF thread). The bottom bar
  hides Convert / Save-to while Properties is on. The viewer got a Properties button.
- Web: `ui/properties.ts` in the right column (the Convert to / Settings panes are hidden by
  CSS, so they keep their state); the newest request wins.

Known gaps on the web: no folder (browsers don't tell), no subtitle track list, no details for
AVI / WMV / FLV (Mediabunny can't read them; the note points to the desktop app).
