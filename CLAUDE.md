# CLAUDE.md — Universal Edit

Guidance for Claude (and humans) working in this repository.

## Start here: every session works in its own worktree

Several Claude sessions work on this project at the same time, so **never edit files in the main
checkout** (`G:\Claude\Universal-Media-Converter`). It only holds `main`, for merging and pushing.
If your session starts there, do this before anything else:

1. Pick a short name for the task (letters, digits, dashes, e.g. `video-timeline`) and create a
   worktree + branch from the current local `main`, then set it up:
   ```powershell
   git -C G:\Claude\Universal-Media-Converter worktree add -b <name> .claude\worktrees\<name> main
   powershell -ExecutionPolicy Bypass -File G:\Claude\Universal-Media-Converter\scripts\setup_worktree.ps1 -Worktree G:\Claude\Universal-Media-Converter\.claude\worktrees\<name>
   ```
   The script gives the worktree its own `.venv` (an editable install of *that* worktree's code;
   a shared venv would silently run another checkout's code), `web/node_modules`, and copies the
   git-ignored `packaging/bin`, `tessdata` and `fonts` from the main checkout.
2. Switch the session into it (EnterWorktree with `path` = the worktree folder). Edit and test only
   there, with its `.venv`.
3. Check `git worktree list` for the other sessions' branches. If your task touches files another
   one is clearly changing, tell the owner before starting.

When a task is finished and verified, land it on `main` and push:
```powershell
git rebase main                                                   # in your worktree
git -C G:\Claude\Universal-Media-Converter merge --ff-only <name>
git -C G:\Claude\Universal-Media-Converter push origin main
```
If the fast-forward fails because `main` moved, rebase again and retry. Never force-push, never
commit in the main checkout, never use a bare `git stash` (the stash is shared by all worktrees).
Keep the worktree for the next task on the same topic; remove it when the work is done
(`git worktree remove .claude\worktrees\<name>` from the main checkout, then delete the branch).

## Project overview

Universal Edit (called Universal Media Converter until Nov 2026) is a **desktop application
shipped as a standalone executable**.

**Naming:** the shown name is `branding.APP_NAME` ("Universal Edit"; folders / exe
`UniversalEdit`, repo + site `Universal-Edit`). The Python package stays `umc` and web
localStorage keys stay `umc-*` (internal, renaming would lose users' state). Old data is moved on
first start: `core/paths.migrated_dir` (app-data / log folder, incl. installed tools) and
`gui/branding.migrate_settings` (QSettings). The logo's only source is `src/umc/gui/logo.svg`
(a U made of scissors); `python scripts/make_icon.py` regenerates `packaging/icon.ico/png` and
`web/public/icon.svg/png` (`tests/test_branding.py` checks the copy). The window / taskbar icon
needs `app.setWindowIcon` + the Windows AppUserModelID (`branding.set_windows_app_id`).
The user drops in (or picks) one or more source files, chooses an output format, tweaks
optional settings, and converts. It covers three media families:

| Source family | Convert to same family | Cross-family conversions |
|---|---|---|
| **Documents** (pdf, docx, doc, odt, rtf, txt, md, html, epub, …) | Any document ↔ any document | Document → images (one image per page) |
| **Images** (png, jpg/jpeg, webp, bmp, tiff, gif, ico, heic, avif, …) | Any image ↔ any image | Multiple images → video (slideshow) |
| **Videos** (mp4, mkv, mov, avi, webm, flv, wmv, m4v, …) | Any video ↔ any video | Video → image sequence, Video → GIF |

**Guiding principle:** expose as many useful settings as possible for whatever the user
imports, but keep the defaults sensible so a user can convert with zero configuration.

## UI / UX requirements

- **Modern and minimalistic** ("calm & refined", redesigned Nov 2026 — spec
  `docs/superpowers/specs/2026-11-10-convert-redesign-design.md`). One wide window, no menus.
- Flow: **input → output format → (optional) settings → Convert**. Same layout in both apps:
  1. Top bar: logo + name centred (3-column grid), **Convert | Edit | Properties** switch on the
     right (Edit disabled unless the files can be edited; Properties whenever files are added —
     it swaps the right column for "About this file" and hides Convert in the bottom bar). The web puts the "never leave your device" badge left.
  2. Left column: drop zone (most of the window while empty, with the editor tiles below it; a
     slim "Add more files" bar after),
     then **Results** (after a run) and the **Queue** — one row per file with thumbnail, size and
     its own progress (`file_fractions` / `fileFractions`: converters report equal slices per file).
  3. Right column, two sideways tabs: **Convert to** (foldable category cards with icon tiles, all
     open; folds remembered) and **Settings** (opened by the cog; "Convert to" then shrinks to a
     strip showing the chosen format). Only formats valid for the detected inputs are shown.
  4. Bottom bar: summary/status, save-to (desktop), overall progress, Convert / Cancel.
  Below 900 px wide everything stacks in one column.
- Dark/light theme following the OS. Same tokens in `gui/theme.py` and `web/src/styles.css`.
  Icons: Tabler line icons (MIT) in `gui/icon_data.py` / `web/src/ui/icon_data.ts` (same set).
- Never block the UI thread — all conversions run in a worker thread with progress/cancel.
- Errors are shown inline in plain language (no raw stack traces in the UI; log them instead).

## Settings per conversion (minimum set)

**Images → video (slideshow)**
- Seconds per image (slider, e.g. 0.1 s – 10 s, default 2 s)
- Output resolution (match first image / 720p / 1080p / 4K / custom), fit mode (letterbox / crop / stretch)
- Frame rate, codec, quality (CRF), image order (as selected / by name / by date)
- Optional transition (none / crossfade) and transition duration

**Video → images**
- Frames to keep **per 30 frames** (slider 1–30; e.g. 10 = keep every 3rd frame)
- Output image format, quality, resolution/scale, start/end time trim

**Video → GIF**
- FPS, width (keep aspect), start/end trim, loop count, palette optimisation on/off, dithering

**Video → video**
- Container, video codec, audio codec, resolution, frame rate, bitrate / CRF quality, preset,
  trim, remove audio, rotate

**Image → image**
- Quality/compression, resize (px or %), keep/strip metadata, background colour for transparency
  when target has no alpha (e.g. PNG → JPG)

**Document → document / image**
- DPI and image format for document → images, page range, single multi-page TIFF option
- Page size / orientation / margins for PDF output (pages are re-placed onto the chosen paper)

**Document → split (several PDFs)**
- Every page into its own document, every N pages, or custom ranges (`1-3, 4-10, 11-`)
- File naming (`name_pages_1-3.pdf` / `name_part_01.pdf`), optional subfolder

## Web companion (`web/`)

A browser version that runs entirely on the visitor's device — **a companion, not a
replacement**: the desktop app stays for legacy Office files (DOC/XLS/PPT/ODS/ODP, which need
LibreOffice) and huge files.
See `web/README.md` for commands and deployment.

- **Stack:** TypeScript + Vite, no UI framework. Engines are WebAssembly, loaded lazily inside one
  long-lived module Worker (`src/worker/jobWorker.ts`): ffmpeg.wasm (`@ffmpeg/core`, multi-threaded
  `core-mt` when cross-origin isolated), ImageMagick (`@imagemagick/magick-wasm`), MuPDF (`mupdf`), three.js + occt-import-js (3D), tesseract.js (OCR), libarchive.js (archives), HarfBuzz hb-subset + woff2-encoder (fonts), resvg + vectortracer (vector), dxf (CAD),
  Pandoc (official `pandoc-wasm`, ~16 MB gz; wired via `pandoc-wasm-core` / `pandoc-wasm-binary`
  aliases in `vite.config.ts` because the package only exports a browser entry Vite can't bundle).
- **Documents:** `converters/docroute.ts` is a port of the desktop route planner (pandoc edges +
  mupdf edges). Pandoc-wasm can't write PDF (no PDF engine), so → PDF goes via HTML laid out by
  MuPDF (`htmlToPdf`, which injects CSS to undo Pandoc's `table{display:block}` — otherwise tables
  collapse). TXT → PDF wraps the text in `<pre>` HTML (UTF-8, falling back to Windows-1252).
- **Same architecture as the desktop app:** `core/` (detect, options schema → auto form, registry,
  types — no DOM/WASM), `converters/` (declare options + targets; `run()` imports engines dynamically
  so the page bundle stays around ~100 KB), `engines/`, `ui/` (`app.ts` page, `queue.ts` queue
  rows, `icons.ts`, `settings.ts` auto form). Target ids match the desktop ones. Test hooks the
  e2e suite relies on: `.pill[data-target]` (format tiles), `.status`, `.results li` /
  `a.fname`, `.results-head`, `.queue li[data-state]`, `.files-head` (queue only), `.note`,
  `.form`, buttons "Convert", "Cancel", "Clear", "Settings" (cog), "Convert to" (strip), "Edit".
  Start screen: `.editor-hub`, `.editor-tile[data-editor]` (their accessible names contain
  "editor", so match "Edit" with `exact: true`), `dialog.new-picture` (`#np-size/-width/-height`).
  Properties: button "Properties" (exact), `.properties-pane`, `.prop-file` (file picker),
  `.prop-card[data-title]` with `dt` / `dd` rows. Drop zone badges: `.drop canvas.orbit`
  (`data-moving`). A real `drop` event on the empty page waits `burstMs` (650 ms) before the
  files are added (the burst); `setInputFiles` and reduce-motion add at once.
  Tiles are hidden while Settings is open: tests choose formats with the `pick()` helper (strip →
  tile → cog again if Settings was open), never by clicking a tile directly.
  Playwright's own Chromium isn't always installed locally; `npx playwright test -c
  playwright.edge.local.config.ts` (in `web/`) or the matrix script's `--channel msedge` use Edge instead.
  The image editor is `editor/` (model, no DOM) + `ui/editor/` (page), imported on demand.
- **Cancel** = terminate the worker (kills FFmpeg's nested worker too); a new worker starts next run.
- **Must be cross-origin isolated** (COOP/COEP): headers in `vite.config.ts` / `public/_headers`;
  where a host can't send them (GitHub Pages) `coi-serviceworker.js` (emitted by a plugin in
  `vite.config.ts`, loaded first in `index.html`) adds them and reloads the first visit once.
- **Local test version:** `npm run package:local` → zip with `app/` + `Start Universal Edit.bat`
  / `serve.ps1` (Windows) and `serve.py`, all localhost-only. Keep `.bat` files CRLF.
- **Keep `build.assetsInlineLimit: 0`** — an inlined (data:) FFmpeg thread worker can't load the
  engine and every video conversion crashes.
- **Thread limits (`engines/threads.ts`):** every `Engine.run` gets explicit `-threads` /
  `-filter_threads` (decoder ≤ 4 and filters ≤ 4 with one input, both 1 with 2+ inputs; encoder
  ≤ 4). `core-mt` has a fixed pool of 32 workers and FFmpeg's automatic counts scale with the CPU,
  so on a 12-core PC an H.264 re-encode hung at 0 % (CI runners have few cores and never showed
  it). Encoder 6 crashed a 1080×1920 encode (wasm memory). Crossfade slideshows (one input per
  picture) hung after frame 1 until decoders and filters were single-threaded, and still hung
  above ~25 pictures (one reader thread per input). `slideshow.ts` now renders them in groups of
  `XFADE_GROUP` (12) into near-lossless clips (x264 CRF 12) and crossfades the clips together,
  level by level; `xfadeGraph` keeps the timing identical to a single pass. Video-type
  targets show a "desktop app is much faster" note (`SLOW_IN_BROWSER` in `ui/app.ts`).
- **Codecs:** ffmpeg.wasm encodes H.264, VP8 and MPEG-4 only (in @ffmpeg/core 0.12.10
  libvpx-vp9 crashes the tab, libx265 hangs, no AV1 encoder). VP9 / AV1 / H.265 go through the
  browser's WebCodecs encoders via Mediabunny (`engines/webcodecs.ts`, `WEBCODECS_VIDEO` in
  `codecs.ts`): resize, fps, quality, target size, rotate, flip, trim; no speed/volume. Inputs
  Mediabunny can't read (AVI/FLV/WMV) get a near-lossless FFmpeg intermediate first. H.265 needs
  a hardware encoder (not in headless Linux Chromium, so it has no e2e test).
  **H.264 tries WebCodecs first too** (`tryBrowserH264` in `converters/video.ts`): 15 s 1080×1920
  phone clip 21 s → 4.7 s. Falls back to ffmpeg.wasm for speed/volume, target size (needs
  two-pass), a chosen audio codec, AVI/FLV output, unreadable inputs or no H.264/AAC encoder.
- **Word → HTML / PDF** use mammoth.js (`docroute.ts` engine "mammoth", BSD-2) instead of Pandoc, so
  the commonest document job doesn't download Pandoc (~16 MB gz). Other DOCX targets use Pandoc.
- ImageMagick enum: pass the format *value* (`"JPEG"`), not `MagickFormat["JPEG"]` (keys are
  PascalCase, so that lookup is undefined and silently keeps the input format).
- ImageMagick can't recognise ICO / CUR / TGA from bytes (it uses the file name, which it never
  sees): `formatHint()` in `engines/magick.ts` passes the format, and icons keep their largest size.
- libopus in @ffmpeg/core 0.12.10 hangs after the first packet: Opus uses FFmpeg's own `opus`
  encoder (`audioEncoderArgs()` adds `-strict -2`). Plain TAR is written as TAR.GZ + gunzip
  (libarchive.js passes the unknown filter name "none" and writes 0 bytes).
- Pandoc's XLSX/PPTX/DOCX readers fail on absolute part paths (openpyxl/pandas write
  `Target="/xl/…"`): `engines/officezip.ts` makes them relative first.
- **Only offer what can work:** videos get `tracks` at detect time (`core/tracks.ts`: Mediabunny for
  audio, the MP4 `moov` / MKV `Tracks` list for text subtitles; `null` = unknown → still offered).
  "Audio only" / "Extract subtitles" are hidden when a file is known to lack them. Desktop does the
  same with one cached ffprobe per file (`ffmpeg.stream_summary`).
- **Tests:** `npm test` (vitest, logic) and `npm run e2e` (Playwright: real conversions through the
  UI in headless Chromium; fixtures generated in `e2e/global-setup.ts`). CI: `.github/workflows/web.yml`.

## Tech stack

- **Language:** Python 3.11+
- **GUI:** PySide6-Essentials (Qt 6) + PySide6-Addons (only Qt Multimedia, for the viewer) — Fusion style + `gui/style.qss` with `@token` colours filled from `gui/theme.py`
- **Images:** Pillow (+ `pillow-heif` for HEIC; AVIF is native in Pillow ≥ 11.3)
- **Video / GIF / slideshow / audio:** FFmpeg (bundled binary, invoked via `subprocess`; progress parsed from `-progress pipe:1`)
- **PDF:** PyMuPDF (`pymupdf`) for PDF → images/text, page ranges, EPUB/XPS → PDF, and future splitting.
  All PyMuPDF use is isolated in `tools/pdf.py` (it is AGPL — see Licensing).
- **Office documents:** LibreOffice in headless mode (`soffice --headless --convert-to`) — *not bundled; one-click install*
- **Text-ish formats:** Pandoc CLI (called directly via `subprocess`, no `pypandoc`) for md/html/epub/rst/tex ↔ docx etc. — *not bundled; one-click install*
- **Packaging:** PyInstaller one-folder build; FFmpeg bundled; LibreOffice/Pandoc installed on demand by the app
- **Tests:** pytest + pytest-qt (GUI tests run with `QT_QPA_PLATFORM=offscreen`)
  `tests/conftest.py` ends the test process with `os._exit(pytest's code)` after the summary:
  Python + Qt's final clean-up crashed at random after green runs (Windows CI failed with all
  tests passed). Don't remove it; release Qt objects in tests anyway (close servers, wait for
  `file_list.thumbnails_pending()`). Clipboard data we made must go through
  `gui/clipboard_owner.release_clipboard` before quitting (app `aboutToQuit`), else exit crashes.
  (After quitting, the clip pastes as a plain picture: centred, no longer "in place".)

Primary target platform is **Windows** (`.exe`); keep code cross-platform (macOS/Linux) where it costs nothing.

## Architecture

```
src/umc/
  app.py                 # entry point: logging, QApplication, theme, main window
  gui/                   # the ONLY package that imports Qt
    main_window.py       # top bar, left column (drop zone, results, queue), right tabs, bottom bar
    worker.py            # runs a ConversionJob on a QThread, reports via signals
    tool_dialog.py       # "LibreOffice/Pandoc needed" → Install it for me (+ manual fallbacks)
    theme.py, style.qss  # light/dark palettes + stylesheet (follows the OS colour scheme)
    icons.py, icon_data.py  # Tabler line icons rendered with QtSvg, tinted to the palette
    widgets/             # DropZone, FileQueue (painted rows, thumbnails off-thread),
                         #   FormatPicker (category cards + tiles), SideTabs (Convert to | Settings),
                         #   SettingsPanel (auto form), FlowLayout
    editor/              # image editor view: EditorPage (top bar, shortcuts, saving on a QThread),
                         #   EditorCanvas (zoom/pan, cached composite), ToolBar, LayersPanel, dialogs
    doceditor/           # document editor view: DocEditorPage (top bar, saving on a QThread),
                         #   text_view (QTextEdit), format_bar, find_bar, formatting (merge/toggle
                         #   helpers), html_export (QTextDocument → HTML subset)
    editor3d/            # 3D viewer: ViewerPage (top bar, info panel, loads on a QThread), gl_view
    viewer/              # "Open with" viewer: ViewerWindow, views (image/media/pages/text), tasks
    audioeditor/         # audio editor: AudioEditorPage (tool row, open/save on a QThread),
                         #   WaveView (ruler, lanes, selection), Player (QAudioSink, pulls int16)
    launcher.py          # AppController: converter windows + the viewer; single_instance.py
  editor3d/              # 3D viewer model — mesh.py (load + measurements), camera.py (orbit); no Qt
  viewer/                # viewer model — files.py (view kinds, folder list), preview.py; no Qt
  audioedit/             # audio editor model (numpy, no Qt) — document.py (AudioDoc, splice
                         #   undo), ops.py, peaks.py (waveform columns), wav.py, io.py (open/save)
  doceditor/             # document editor model — pure Python, no Qt
    types.py             # SaveContent / OpenedDocument, "parts that will be lost" warnings, limits
    html_subset.py       # the small HTML the editor shows and saves (sanitise, picture names)
    docx_reader.py, docx_writer.py  # DOCX ↔ that HTML subset (own code, python-docx / XML)
    routes.py            # other formats go through the document route planner (Pandoc/LibreOffice)
    io.py, save.py       # open_document(path) → OpenedDocument; save_document(content, dest, fmt)
  editor/                # image editor model — pure Python, no Qt
    document.py, layer.py  # Document (canvas size, layers bottom-first, selection), Layer (RGBA + x/y)
    history.py           # undo: PatchCommand (changed rect only), LayerList/DocumentState commands
    tools/               # Tool classes (press/move/release → Command); TOOLS = tool-bar order
    ops.py               # crop, resize, canvas size, rotate/flip, brightness/contrast, layer ops
    ora.py, io.py        # OpenRaster read/write; open_files(paths) → one layer per image
    save.py              # .ora, or composite → ImageConverter (all formats + their options)
  core/                  # pure Python, no Qt
    detect.py            # media family from extension, falling back to magic bytes
    registry.py          # Target dataclass + targets_for(files) across all converters
    options.py           # opt() field helper → OptionSpec metadata that drives the settings UI
    job.py               # ConversionJob (progress/cancel), unique_path()
    errors.py            # ConversionError (user-facing msg), ToolMissingError, ConversionCancelled
    paths.py             # app data folder, private tools folder (UMC_TOOLS_DIR overrides)
  converters/
    base.py              # Converter ABC: family, options_cls, targets(files), run(job)
    image.py             # image ↔ image; images → PDF
    imaging.py           # shared Pillow helpers (open incl. HEIC, flatten, fit/letterbox)
    slideshow.py         # images → video (concat demuxer, or xfade transitions) / animated GIF
    video.py             # video ↔ video (codec, CRF/bitrate, resize, fps, trim, speed, rotate…)
    codecs.py            # codec → encoder fallback lists, container rules, rate-control args
    video_frames.py      # video → image sequence ("N frames per 30" select filter)
    gif.py               # video → GIF (palettegen/paletteuse)
    audio.py             # audio ↔ audio, and video → audio only (shared _AudioConverter)
    document.py          # document ↔ document via a cheapest-route planner over engine "edges"
    document_images.py   # document → images (via PDF, rendered with PyMuPDF)
    split.py             # document → several PDFs (every page / N pages / custom ranges)
    data.py              # JSON ↔ YAML ↔ XML ↔ CSV/TSV/XLSX
    pdf_tools.py         # PDF toolkit: merge, compress, rotate, organize, protect, unlock
    ocr.py               # images / scanned PDFs → searchable PDF or text (Tesseract in PyMuPDF)
    model3d.py           # 3D models: STL/3MF/OBJ/PLY/GLB, STEP/IGES via OpenCASCADE
    archive.py           # archives: extract / repack, and pack any files into ZIP/7Z/TAR.GZ
    subtitles.py         # subtitles ↔ subtitles, extract from video, burn in / add as track
    fonts.py             # fonts: TTF ↔ OTF ↔ WOFF ↔ WOFF2, subsetting (fontTools)
    vector.py            # SVG/EPS/PS/AI ↔ each other / PDF / images; PDF → SVG; image tracing
    cad.py               # CAD: DXF/DWG → PDF/SVG/PNG/JPG, DWG → DXF, DXF versions (ezdxf)
  tools/                 # wrappers around external programs/libraries
    process.py           # find_executable (bundled → PATH → known install dirs), cancellable run()
    installer.py         # one-click download + verify + install of LibreOffice / Pandoc (Windows)
    archive.py           # ZIP (pyzipper) / 7Z (py7zr) / TAR / RAR (bsdtar) read + write, safe extraction
    ghostscript.py       # EPS/PS via Ghostscript (-dSAFER), found or one-click installed
    calibre.py           # Kindle MOBI/AZW3 via Calibre's ebook-convert, found or one-click installed
    libredwg.py          # AutoCAD DWG → DXF via LibreDWG's dwg2dxf, found or one-click installed
    tessdata.py          # OCR language files: bundled English, pinned SHA-256 downloads
    winsign.py           # Authenticode check via WinVerifyTrust/crypt32 (PowerShell's module is unreliable)
    ffmpeg.py, libreoffice.py, pandoc.py, pdf.py
tests/                   # fixtures are generated in conftest.py — no binary files in git
packaging/
  umc.spec               # PyInstaller spec
  icon.ico / icon.png    # generated by scripts/make_icon.py
  bin/                   # bundled ffmpeg/ffprobe (git-ignored; scripts/fetch_ffmpeg.py)
scripts/                 # fetch_ffmpeg.py, fetch_tessdata.py, fetch_font.py, make_icon.py
.github/workflows/build-windows.yml    # builds the .exe zip on windows-latest (manual or v* tag)
.github/workflows/test-installers.yml  # real LibreOffice/Pandoc one-click install test (manual)
```

**Target ids** are unique per input set: plain ids for same-family conversions (`png`, `mp4`),
prefixed ids for cross-family ones (`slideshow:mp4`, `frames:png`, `video:gif`, `audio:mp3`,
`doc:pdf`, `pages:png`, `images:pdf`). An option can be limited to some targets with
`opt(..., targets=(...))` and made conditional with `show_if=lambda values: ...`.

**Document routing:** `converters/document.py` lists every conversion each engine can do as a
weighted edge (e.g. pandoc md→docx, libreoffice docx→pdf) and picks the cheapest path, so
`md → pdf` becomes `md →(pandoc) docx →(libreoffice) pdf`. Engines that aren't installed get a
+100 penalty, so working routes are preferred and a missing tool only triggers the download
prompt when there's no alternative. To support a new document format, add edges — not special cases.
Built-in edges (`tools/htmlpdf.py`: mammoth DOCX → HTML, markdown-it MD → HTML, PyMuPDF Story
HTML/TXT → PDF via `pdf.layout_html`) make Word / Markdown / HTML / TXT → PDF work with nothing
installed. They're priced above LibreOffice (simpler layout), so LibreOffice wins when present.
LibreOffice reuses one profile (`%LOCALAPPDATA%\UniversalEdit\libreoffice-profile`,
claimed per run so a second window falls back to a temporary one): ~20 s → ~7 s per document.

Key rules:
- **The GUI never calls libraries directly** — it builds a `ConversionJob` and hands it to the
  registry. Converters know nothing about Qt.
- **Option schemas drive the UI.** Adding a setting = adding a field to the converter's options
  dataclass (with label, range, default); the settings panel renders it automatically.
- Adding a new format should usually mean touching one converter + its tests, nothing else.
- External tools (ffmpeg, soffice, pandoc) are located through `tools/` helpers that check the
  bundled path first, then `PATH`, and raise a clear, user-facing error if missing.
- Never overwrite an existing output file silently — auto-suffix (`name (1).ext`) or ask.
- Keep references to the worker and its QThread until `QThread.finished` (not the worker's
  `done` signal) — releasing the Python wrapper earlier crashes while `run()` unwinds.
- On Windows every subprocess uses `CREATE_NO_WINDOW` (handled in `tools/process.py`).

## Development commands

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python scripts/fetch_ffmpeg.py                      # Windows: download FFmpeg into packaging/bin
python -m umc                                       # run the app
pytest                                              # run tests (needs ffmpeg/pandoc/soffice for all; else skips)
ruff check . && ruff format .                       # lint + format
python scripts/build_addons.py                      # editor add-on zips (before PyInstaller)
pyinstaller packaging/umc.spec                      # build the executable into dist/
UniversalEdit.exe --self-test report.txt  # headless smoke test of a build (exit 0 = OK)
python scripts/convert_matrix.py --out matrix-out   # try EVERY conversion with defaults → report.md
node web/scripts/convert-matrix.mjs matrix-out/samples --channel msedge  # same for the website
```

The two matrix scripts are the broad safety net: one sample per input format, every target the
app offers, each output checked to open. Run them after anything that touches several converters;
"tool-missing" (Pandoc/Calibre/Ghostscript not installed) is not a failure.

Logs: `%LOCALAPPDATA%\UniversalEdit\umc.log` (Windows),
`~/.local/state/UniversalEdit/umc.log` (Linux), `~/Library/Logs/…` (macOS).

## Packaging

- PyInstaller one-folder build is the default (faster startup than one-file, easier to bundle binaries).
- FFmpeg/ffprobe are bundled under `bin/` (GPL build, includes x264/x265) together with its
  licence text (`FFMPEG-LICENSE.txt`). It's the BtbN **shared** build: two tiny exes + one set of
  FFmpeg DLLs = 194 MB, instead of two self-contained 162 MB exes (324 MB). `--static` restores those.
- LibreOffice (~1 GB installed) and Pandoc are **not bundled**. Lookup order: the app's private
  tools folder (`%LOCALAPPDATA%\UniversalEdit\tools`) → bundled → `PATH` → standard
  install locations (e.g. `C:\Program Files\LibreOffice\program\soffice.exe`).
- When a conversion needs a missing tool, the dialog's main button is **"Install it for me"**
  (Windows; `tools/installer.py`), with "Download … myself" and "I've installed it — check again"
  as fallbacks. After a successful install the conversion is retried automatically.
  - **LibreOffice:** newest version from `download.documentfoundation.org/libreoffice/stable/`,
    Windows x86-64 MSI. Must be a real MSI, trusted by `WinVerifyTrust` and signed by The
    Document Foundation (`tools/winsign.py` — don't switch back to PowerShell's
    Get-AuthenticodeSignature: its module fails to load in some environments). Unpacked with `msiexec /a` into the private folder (no admin, no file
    associations), then proven with a real txt → pdf conversion. If that copy won't run, fall
    back to `msiexec /i /passive` (Windows shows its permission prompt once; MS Office file
    associations are not taken over).
  - **Pandoc:** newest `pandoc-*-windows-x86_64.zip` from the GitHub release, verified against
    the SHA-256 digest GitHub publishes, extracted into the private folder (zip-slip checked).
  - Disk space is checked first; the MSI is deleted afterwards; msiexec's verbose log (~30 MB for
    LibreOffice) goes next to the tools and is deleted when the install succeeds.
- **Never fill the user's disk:** temporary folders are `tempfile.TemporaryDirectory(prefix="umc-…")`
  context managers. At startup `core/cleanup.py` (background thread) removes what crashed runs left:
  `umc-*` temp folders and `tools/downloads` files untouched for 2 days, installer logs after 7
  days. Installed tools are never touched.
- Conversions that don't need LibreOffice (images, video, PDF → images, Pandoc formats) must keep
  working when it is absent.
- **Antivirus false positives** (e.g. Defender `Trojan:Win32/Bearfoos.B!ml`, where `!ml` means a
  machine-learning guess): the prebuilt PyInstaller bootloader is shared with malware, so CI
  recompiles the bootloader from source (`PYINSTALLER_COMPILE_BOOTLOADER=1`), and the spec embeds
  Windows version info. Keep `upx=False` (UPX packing makes it worse). The real fix is code
  signing — not set up yet (needs the owner's decision; SignPath.io is free for open source).
- The installer is `packaging/installer.iss` (Inno Setup; roadmap 10.11), built and tested by the
  same workflow.
- Editors are add-ons (roadmap 10.12): `python scripts/build_addons.py` must run before
  `pyinstaller packaging/umc.spec`; a release needs a `v<version>` tag matching `umc.__version__`
  so the app finds its add-on downloads.
- The Windows build is produced by `.github/workflows/build-windows.yml` (Actions → "Build Windows
  executable" → Run workflow, or push a `v*` tag); download the zip from the run's artifacts.

## Licensing (decided: personal use / free & open source)

- The app is for personal use or free open-source distribution — never closed-source or sold.
  Ask the owner before adding anything that would conflict with that.
- FFmpeg: **GPL build** (x264/x265 included). `codecs.py` still keeps encoder fallbacks in case
  an LGPL build (`scripts/fetch_ffmpeg.py --lgpl`) or a system FFmpeg is used.
- PyMuPDF is AGPL-3.0. Together with the GPL FFmpeg, the project is licensed
  **AGPL-3.0-or-later** (`LICENSE`, declared in `pyproject.toml`, shipped inside the .exe).
  Any public release must make its source available. New dependencies must be
  AGPL-3.0-compatible (no proprietary or GPL-2.0-only libraries).

## Coding conventions

- Type hints everywhere; `ruff` for lint/format; small, pure functions in `converters/`.
- Every converter has tests that run against `tests/fixtures/` and check the output opens/decodes.
- Long operations must report progress (0–100) and honour cancellation.
- Log to a rotating file in the user's app-data folder; the UI shows friendly messages.
- Commit messages: imperative mood, short subject line (`Add video to GIF converter`).
- Work in your own worktree/branch and land finished tasks on `main` (see "Start here"). Commits
  are authored as `darkbepo` with no co-author or session trailers (the owner asked for this).

## Roadmap

1. ✅ Project skeleton, GUI (drop zone, file list, format picker, auto settings, progress, cancel), image ↔ image.
2. ✅ Video ↔ video, video → GIF, video → images (frames-per-30 slider), video → audio.
3. ✅ Images → video slideshow (seconds-per-image slider, transitions, music), images → PDF.
4. ✅ Documents ↔ documents, documents → images.
5. ✅ Packaging: PyInstaller spec, icon, CI build on windows-latest with tests and an
   `--self-test` run of the built .exe. No installer yet.
6. ✅ Document splitting (every page / N pages / custom ranges) and PDF page size & margins.
7. ✅ One-click install of LibreOffice / Pandoc from inside the app.
8. ✅ Web companion (`web/`): images, video, PDF and documents (Pandoc). Live at
   https://darkbepo.github.io/Universal-Edit/ — `.github/workflows/pages.yml` publishes
   on every push to `main` that touches `web/` and then runs all browser tests against the live site.
   Next: PWA/offline.
9. **Converter parity with the big online converters** (owner wants all of these, in this order;
   each lands in BOTH apps where possible, and the "What works where" table in README.md must be
   updated in the same commit):
   1. ✅ Audio files as input (MP3/WAV/FLAC/M4A/OGG… ↔ each other) — `MediaFamily.AUDIO` / family "audio"
   2. ✅ Camera RAW + PSD input — desktop: rawpy/LibRaw in `imaging.open_image`; web: magick-wasm
      needs an explicit format hint for RAW (`engines/magick.ts` readImage/readFrames), and PSD
      layers must not become animation frames. Test samples: `umc/core/samples.py` (DNG + PSD
      written from scratch; the web e2e setup calls it via python3).
   3. ✅ PDF toolkit: merge, compress, rotate, protect/unlock, reorder/delete pages — desktop
      `converters/pdf_tools.py` + `tools/pdf.py`; web `converters/pdftools.ts` (no rewriteImages in
      mupdf.js, so images are recompressed by hand: loadImage → Pixmap.warp (corners clockwise) →
      asJPEG → writeRawStream). Protect: owner password defaults to a random secret, otherwise the
      permissions are ignored. Option kind "password" exists in both apps.
   4. ✅ Compress to a target size — `converters/sizing.py` / `sizing.ts`: video two-pass for
      libx264/libvpx(-vp9) (else ABR + maxrate), audio bitrate from duration, images binary-search
      quality then downscale, PDF compress walks a stronger-settings ladder (best effort).
   5. ✅ Data formats: JSON ↔ CSV ↔ TSV ↔ XML ↔ YAML ↔ XLSX — desktop `converters/data.py`
      (openpyxl, PyYAML, ElementTree); web `converters/data.ts` (yaml, fast-xml-parser, and a
      hand-written XLSX reader/writer on fflate — SheetJS is only on its own CDN, npm copy is stale).
      Same conventions in both: XML attributes "@name", text "#text", nesting ↔ "a.b" columns.
      Group "Data" replaces doc:csv/doc:xlsx for data-readable inputs (no LibreOffice needed).
   6. ✅ 3D printing: STL, OBJ, 3MF, PLY, GLB, STEP/IGES — everything is normalised to mm.
      Desktop `converters/model3d.py` (trimesh; cascadio/OpenCASCADE for STEP/IGES — STEP comes
      back in metres, IGES in the file's own unit). Web `converters/model3d.ts`: three.js parsers
      for STL/OBJ/PLY, own GLB/3MF readers and PLY/GLB/3MF writers (three's 3MF/GLTF loaders and
      PLY/GLTF exporters need DOM APIs missing in workers), occt-import-js (LGPL) for STEP/IGES.
   7. ✅ OCR: images / scanned PDFs → searchable PDF (`ocr:pdf`) or text (`ocr:txt`). Desktop uses
      the Tesseract built into PyMuPDF (`Pixmap.pdfocr_tobytes`, all in `tools/pdf.py`) — only the
      language files are needed: `tools/tessdata.py` (tessdata_fast 4.1.0, SHA-256 pinned; English
      bundled via `scripts/fetch_tessdata.py`, others downloaded once). Web: tesseract.js in a nested
      worker (`engines/tesseract.ts`); verified files go into IndexedDB and it starts with
      cacheMethod "readOnly" (passing {code, data} is broken in tesseract.js 7.0.0). Both tables
      must match — `web/tests/ocr.test.ts` checks it.
   8. ✅ Archives: ZIP/7Z/RAR/TAR(.gz/.xz/.bz2) → extract (`archive:extract`) or repack; any files —
      also of mixed kinds — → `pack:zip|7z|tar.gz` (converters with `any_family` / `anyFamily`;
      mixed inputs now get only those instead of an error). Desktop `tools/archive.py`: pyzipper
      (AES zip), py7zr, tarfile; RAR via bsdtar (Windows' own System32\tar.exe, macOS tar,
      Linux `libarchive-tools`) — no unRAR code (licence). Extraction never writes outside the
      target (no `..`, absolute paths or links). Web: libarchive.js reads everything; ZIP is
      written with fflate, TAR flavours with libarchive.js (its worker + wasm are emitted by a
      vite plugin with a patched output-buffer size); no 7Z writing or encryption on the web.
   9. ✅ Subtitles (FFmpeg in both apps): SRT/VTT/ASS/SSA/SMI/LRC in → SRT/VTT/ASS/TTML/LRC out,
      with time shift and legacy-charset fallback (inputs are re-encoded to UTF-8 first);
      video → `subs:srt|vtt|ass` (text tracks only); one video + subtitle files (a mixed set, via
      `any_family`) → `burn:mp4` (libass `ass=` filter, run with cwd = temp dir so no path
      escaping; styled "Default" style; bundled DejaVu Sans via `fontsdir`) or
      `subtrack:mkv|mp4` (language from `name.da.srt` → ISO 639-2/B `dan`). Font: desktop
      `scripts/fetch_font.py` (npm tarball, SHA-256 pinned) → `packaging/fonts`; web imports
      `dejavu-fonts-ttf` with `?url` and writes it to /fonts in FFmpeg's file system.
   10. ✅ Fonts: TTF/OTF ↔ WOFF ↔ WOFF2 + subsetting (Latin / Latin-ext / Cyrillic / Greek ranges —
      the same strings in both apps, checked by `web/tests/fonts.test.ts` — or typed characters).
      Desktop `converters/fonts.py` (fontTools; TTF ↔ OTF via cu2qu / T2CharStringPen, variable
      fonts refused for outline changes). Web `engines/fonts.ts`: WOFF1 codec on fflate,
      woff2-encoder for WOFF2, HarfBuzz `harfbuzz-subset.wasm` driven through its C exports
      (keeps GSUB/GPOS) — fonteditor-core was rejected because it drops OpenType layout. No
      TTF ↔ OTF on the web (no JS CFF writer that keeps layout).
   11. ✅ Vector (family "vector": svg, eps, ps, ai): → `svg|eps|ps`, `vec:pdf|png|jpg|webp|tiff`;
      PDF → `pages:svg`; images → `trace:svg`. Desktop `converters/vector.py` goes through PDF:
      SVG / modern AI via PyMuPDF (`tools/pdf.py`), EPS / PS / old AI via Ghostscript
      (`tools/ghostscript.py`, always `-dSAFER`; one-click NSIS install, signed by Artifex, may need
      UAC once — covered by `test-installers.yml`), tracing with vtracer (MIT). Web: MuPDF.js has
      no SVG reader, so SVG → images uses resvg-wasm (MPL-2.0, DejaVu font); AI and PDF via MuPDF
      (DocumentWriter "svg"/"pdf"); colour tracing = ImageMagick quantize + vectortracer's binary
      tracer per colour, stacked (vectortracer has no colour mode). EPS/PS, SVG → PDF/SVG desktop only.
   12. ✅ PDF → Word keeping layout (desktop): pdf2docx (MIT; builds on PyMuPDF, wrapped in
      `tools/pdf.py`) is a `pdf → docx` edge in the document route planner (cost 1, below
      LibreOffice's PDF import), so PDF → ODT/DOC/RTF also go via it. Scans without text are
      sent to OCR first. The web keeps its flowing-text route (MuPDF HTML → Pandoc).
   13. ✅ eBooks (desktop): Calibre's `ebook-convert` (GPL-3, separate program, `tools/calibre.py`)
      is a document-route engine: Kindle MOBI/AZW3 in and out, plus EPUB/FB2 (expensive edges, so
      Pandoc keeps those routes). One-click install = the signed Calibre MSI (Kovid Goyal) unpacked
      with `msiexec /a` like LibreOffice, then a test conversion. Title / author options. Web: —.
   14. ✅ CAD (family "cad": dxf, dwg) → `dxf` (DWG → DXF / DXF version change, R12 via ezdxf's
      r12export), `cad:pdf|svg|png|jpg`. Desktop `converters/cad.py`: ezdxf (MIT) with its PyMuPDF
      + SVG drawing backends (paper sizes, margins, colour modes, line thickness, model space or
      every paper layout); DWG via LibreDWG `dwg2dxf` (GPL-3 program, `tools/libredwg.py`, one-click
      win64 zip from GitHub, SHA-256 checked; live test makes a DWG with its `dxf2dwg`). Web
      `converters/cad.ts`: `dxf` package (MIT) → SVG, restyled (colours, 1.5 px lines) → resvg for
      PNG/JPG. No DWG / PDF / DXF writing on the web.
   15. ✅ / ⏸ Close web gaps. ✅ VP9 / AV1 / H.265 on the web via WebCodecs (see "Codecs" above).
      ⏸ LibreOffice-wasm for DOC/XLS/PPT: investigated, not shipped — `@matbee/libreoffice-converter`
      (MPL-2.0, headless LibreOffice wasm) works in principle but `soffice.wasm` is 147 MB plus a
      100 MB `soffice.data`: over GitHub Pages' and jsdelivr's per-file limits and a ~250 MB first
      download per visitor. Options when revisiting: host on Cloudflare R2/Pages (25 MB file limit
      → would need splitting) or a release asset with CORS, load only on demand behind an explicit
      "download 250 MB engine" button, cache in Cache Storage; keep `DESKTOP_ONLY_DOCUMENT_EXTS`
      until then.
10. **Editor** (only after 9 is done): image editor (paint/photoshop-like), video timeline
    (trim, join, mix video + images + music), text/document editing and saving.
   1. ✅ Shell (desktop): a Convert | Edit switch above the file list, shown when every added file
      is an image; Edit swaps the window (QStackedWidget) to the editor, each image a layer.
   2. ✅ Image editor, first version (desktop) — spec `docs/superpowers/specs/2026-11-10-image-editor-design.md`:
      layers (add, duplicate, delete, merge down, reorder, hide, opacity, rename), brush, eraser,
      fill (tolerance, sample all layers), colour picker, line / rectangle / ellipse, text (new
      layer), rectangle selection, crop, resize image / canvas, rotate / flip (image or layer),
      brightness / contrast, undo / redo (patches of the changed rect, 500 MB cap). Saves `.ora`
      (layers; also readable by the converter) or any image format through `ImageConverter`.
      Layer objects are replaced, never changed in place, so undo can share them (history.py).
      Not yet: lasso / magic wand, blend modes, filters, hue / saturation / curves.
   3. ✅ Image editor on the website — spec `docs/superpowers/specs/2026-11-11-web-image-editor-design.md`:
      same tools and shortcuts. Model in `web/src/editor/` (port of `src/umc/editor/`, RGBA
      `Uint8ClampedArray` layers; Canvas 2D only behind the `Rasterizer` / `PngCodec` interfaces in
      `raster.ts`, so vitest runs it in node with fakes); page in `web/src/ui/editor/`, loaded on
      demand from `ui/app.ts`. Pixels stay in the page; the conversion worker only decodes formats
      the browser can't (HEIC/TIFF/RAW/PSD → PNG) and encodes non-`.ora` saves. Save downloads;
      "Save to a file…" (Chrome/Edge) uses `showSaveFilePicker` and Ctrl+S overwrites it. The web
      image converter reads `.ora` via its `mergedimage.png`. e2e: `e2e/editor.spec.ts` (the page
      exposes `window.__editor` only with `?e2e` in the URL).
   4. Layers list and shortcuts (owner's request, Nov 2026), one task at a time, desktop first:
      a. ✅ desktop layers: `Document.selected` (+ `selected_indices`, always includes `active`,
         restored by undo through `LayerListCommand`); ops `select_layers`, `move_layers_to` (drag:
         a gap 0…n), `move_layers_by` (arrows), `duplicate_layers`, `delete_layers` (≥ 1 stays),
         `merge_layers` (hidden ones dropped, like Merge down), `set_opacity_layers`,
         `set_visible_layers`. `gui/editor/layers_panel.py`: `LayerList` (ExtendedSelection; ▲▼
         painted by `LayerDelegate`, clicks caught in `mousePressEvent`; drops reported as a gap and
         answered with `IgnoreAction`, so Qt never moves/deletes rows itself — the list is rebuilt
         from the model), right-click menu `build_menu`. The Up / Down buttons are gone (owner:
         arrows on the row are clearer). Offscreen Qt can't run a real drag, so the drop test
         calls `dropEvent` directly — check a real mouse drag on Windows.
      b. ✅ web layers: same ops in `web/src/editor/ops.ts` (`EditorDoc.selected?: number[]`,
         `selectedIndices(doc)` in `document.ts`; `web/tests/editor-layers.test.ts` mirrors
         `tests/test_editor_layers.py`). `ui/editor/panels.ts` `LayersPanel`: selection is updated
         by classes (`selected`, `active`, list `multi`) without a rebuild so double-click rename
         keeps working; dragging = pointer events with pointer capture on the list (gap shown as
         `drop-above` / `drop-below`, auto-scroll at the edges, the click after a drag is
         swallowed); right-click / Shift+F10 / menu key opens a fixed `.context-menu` (arrow keys,
         Esc). e2e in `e2e/editor.spec.ts` drags with the real mouse.
      c. ✅ Crop bar (both apps): `gui/editor/crop_bar.py` / `web/src/ui/editor/cropbar.ts` — same
         `place_bar` / `placeBar` rules (outside the rectangle next to the corner nearest the
         release point, flip, then inside; clamped), both tested with the same cases. The canvas
         owns it (`bar`, `bar_target()` / `barTarget()`), shown only with the pointer up, for the
         crop tool's pending rectangle ("Apply crop / Cancel") or the select tool's selection
         ("Crop to selection / Deselect"); placed after every repaint. The web side-panel crop
         block is gone; both apps show the hint in the status line.
      d. ✅ Clipboard, Move tool, shortcuts (both apps). Model: `editor/clipboard.py` / `clipboard.ts`
         (`Clip` = RGBA + the canvas x/y it came from; paste = new selected layer, in place or
         centred on the view), `tools/move.py` / `move.ts` (first in the tool bar), `ops.nudge_layers`.
         One shortcut table per app (`gui/editor/shortcuts.py`, `web/src/ui/editor/shortcuts.ts`)
         drives both the key bindings and the "?" window; tests check every id has an action and no
         key clashes. Desktop clipboard: Qt, with our x/y in a private mime type. Web: Ctrl+V is
         left to the browser's `paste` event (no permission prompt); copy writes a PNG with
         `navigator.clipboard.write` and keeps the clip in memory — browsers re-encode clipboard
         images, so "is this our copy?" compares pixels (`samePicture`). Chrome reserves
         Ctrl+Shift+N, so a new layer is Ctrl+Alt+N on the web.
   5. Next, in this order (owner, Nov 2026; one step at a time, both apps, ship gate each):
      1. ✅ Adjust menu (both apps): Quick fix / Levels / Curves windows with live preview and
         "hold to see the original"; brightness / contrast moved into Quick fix. Model
         `editor/adjust.py` / `adjust.ts` (line-by-line ports: per-channel steps folded into 256
         tables, then saturation / vibrance per pixel; curves = Fritsch–Carlson monotone cubic).
         `tests/fixtures/adjust_cases.json` holds expected pixels both test suites check (web
         within 1) — regenerate it from adjust.py only on purpose.
      2. ✅ Filters menu (both apps): Blur, Sharpen, Looks. `editor/effects.py` / `effects.ts` is
         the shared live-preview / apply code for every effect (adjustments too): an effect is
         `effect(image, scale)` + `identity`, optional `margin()` = extra pixels it reads around
         the selection (blur). Big previews run on a `reduce`d copy (`scale` < 1). Blur/sharpen on
         premultiplied colour (desktop Pillow GaussianBlur/UnsharpMask on "RGBa"; web a 3-pass box
         blur — close, not identical). Looks = lists of Adjust-menu settings in
         `web/src/editor/looks.json`; `filters.LOOKS` must match it (tested).
      3. ✅ Selections of any shape (both apps): `editor/selection.py` / `selection.ts`.
         `doc.selection` stays the bounding Rect (so rect-only code keeps working) and
         `doc.selection_mask` / `selectionMask` (0…255 over that rect, None = plain rectangle) holds
         the shape; always set both through `set_selection` / `combine` (normalises, one undo step;
         `SelectionCommand` stores masks). Every pixel change goes through `tools.base.restrict`
         (premultiplied mix with the original by the mask) or, for the brush, multiplies its
         coverage by `selection_weight`. Tools: rectangle / ellipse (M), lasso / polygon (Q, Enter
         or double-click closes), wand (W); Shift add, Alt subtract, both intersect; a letter
         shared by tools cycles them (`tool_groups` / `toolGroups`). Web: Ctrl+Shift+I is the
         browser's devtools, so invert is Shift+F7 / Ctrl+Alt+I and feather Ctrl+Alt+D (both apps
         accept those). `web/src/editor/flood.ts` exists to break an import cycle
         (tools/base → selection → fill → base).
      4. ✅ Masks and gradients (both apps). `Layer.mask` (desktop "L" the image's size; web RGBA
         layout, grey in R, so crop/resize/rotate/flip reuse the pixel routines); `composite`,
         merges, copy and ORA save use the masked pixels (`Layer.pixels()` / `layerPixels`; ORA has
         no masks, so they're baked in). Desktop transforms go through `ops._moved(op=…)`, which
         applies the same op to the mask. Mask brush = the normal stroke tool painting a stand-in
         (desktop proxy RGBA → mask; web the mask array itself), undo `MaskPatchCommand`.
         `tools/gradient.py` / `gradient.ts`: colour, layer mask or a soft selection (adjustments
         then fade). Background blur = an effect with `outside_selection` / `outsideSelection`:
         effects.py/ts invert + feather the selection around it and put it back.
      5. ✅ Text v2 (both apps): `Layer.text` = a `TextSpec` (desktop dataclass / web object with the
         same snake_case fields) — `render_spec` / `renderSpec` draws it (glyphs + outline by Pillow
         `stroke_width` / canvas `strokeText`; box, shadow, watermark composed in the model);
         `ox`/`oy` = where the clicked point sits in the image so re-editing keeps it in place even
         after a move. Transforms (`_moved(op=…)`) drop the spec (it's pixels then). ORA keeps it as
         a `umc-text` JSON attribute on the layer element (same format in both apps).
      6. ✅ Annotation (both apps): `ArrowTool` (L, cycles with Line; head size from
         `arrow_head` / `arrowHead`, same numbers), `HighlighterTool` (H; a stroke tool at 40 %
         strength — strokes take the max coverage, so it doesn't build up). Stickers =
         `editor/stickers.py` (shapes on a 100 grid, colours "front"/"back"/hex, drawn 4×
         supersampled); `web/src/editor/stickers.json` is its output (`python -m
         umc.editor.stickers`, test checks they match). Web draws them through the new
         `Rasterizer.draw(box, items)` (outlines clipped inside, like Pillow). Insert:
         `ops.add_sticker` / `addSticker`, centred in view, "Stickers" button in the top bar.
      7. ✅ Pasteboard (both apps; the owner's choice of "artboard"): `Document.pasteboard(step)` /
         `pasteboard(doc, step)` = canvas ∪ visible layer bounds, at most half the longer side past
         each edge (less on big pictures: the whole area stays within `PASTEBOARD_BUDGET`, 40 MP
         desktop / 16 MP web), overhang rounded up to `step` (= longer side / 8) so a drag doesn't rebuild the
         cache on every move. The canvas cache covers that area (`composite(…, clip=False)`),
         drawn faded outside the picture with a thin border; pan limits use it, but the view is
         only re-clamped on full refreshes (never mid-drag). Saving composites the canvas only.
      8. ✅ Clone stamp + content-aware fill (both apps). `CloneStampTool` (S) is a stroke tool
         whose `_paint` / `paintPixel` hook copies from a snapshot taken at stroke start (layer or
         composite); Alt+click sets `source`, `offset` is kept while "aligned"; `notice` asks for a
         source first (canvas → status line), `marker()` = the cross the canvas draws. Fill:
         desktop `editor/inpaint.py` = OpenCV `cv2.inpaint` TELEA (opencv-python-headless,
         Apache-2.0, already pulled in by pdf2docx; checked by `--self-test`); web
         `editor/inpaint.ts` = own Telea port (fast marching, premultiplied average). Both work on
         the selection's box + a margin and blend soft selection edges. The web fill runs on the
         page, so it refuses selections above `MAX_HOLE` (1.5 MP).
      9. ✅ Background removal (both apps) = a layer mask (`ops.mask_from` / `maskFrom`, combined
         with an existing mask; pixels untouched). Desktop `tools/bgremove.py`: IS-Net
         general-use ONNX (Apache-2.0) from rembg's fixed `v0.0.0` release, SHA-256 + size
         pinned, into `tools/models/`, run with onnxruntime (MIT; CPU) at 1024², min-max
         normalised; asked once ("Download … ?"), then `_BackgroundWorker` on a QThread with a
         QProgressDialog. Web: @imgly/background-removal 1.7.0 (AGPL-3.0, peer onnxruntime-web
         1.21.0) `segmentForeground` → "image/x-alpha8", run in the conversion worker through
         the hidden `bgremove` converter (no targets, so never on the convert page); model + ORT
         files come from IMG.LY's CDN (staticimgly.com) as blob URLs. Vite still emits ORT's own
         24 MB wasm (unused). e2e fakes the model (`__editor.fakeSegmenter()`); the sandbox
         can't reach the CDN, so the real web download is only checked on the live site.
   6. ✅ Owner's feedback round (Nov 2026), in this order, both apps, one step at a time:
      1. ✅ Quick fixes. Web: `.layers` is `user-select: none` (+ `body.no-select` while a row
         is dragged) so dragging highlights no text; top-bar `details.menu` close on a pointerdown
         elsewhere / Esc, and opening one closes the others (`closeMenusOnOutsideClick`). Both:
         the mask brush gives a mask-less layer an all-white mask on its first stroke (a new Layer
         object; the stroke's undo is then a `LayerListCommand` that removes it); `ops.solo_layer` /
         `soloLayer` (Alt+click an eye or the layer menu; again = show all; one undo step);
         colour-picker loupe (`_paint_loupe` / `paintLoupe`: 11 × 11 cached pixels at 11×, up-left
         of the pointer, kept on screen).
      2. ✅ Move a selection (both apps): every selection tool has a `_SelectionMover` /
         `SelectionMover` (`tools/select.py` / `select.ts`): a press inside the selection with no
         Shift/Alt (`selection.contains`) drags the outline — previewed by offsetting
         `doc.selection` (mask kept), committed on release by `move_selection` / `moveSelection`
         (trimmed to the canvas by `shifted`, one undo step). A click inside still deselects (rect /
         ellipse / lasso), picks (wand) or starts a polygon. Arrows (Shift = 10 px) nudge it with a
         selection tool — a run of presses is one undo step (`nudge_selection` / `nudgeSelection`,
         trimmed once from where the run started); a drag starts after 3 screen px, so a wobble is
         still a click; the cursor turns into a move cursor inside it.
      3. ✅ Tool rail (both apps): one layout, `editor/rail.py` → `web/src/editor/rail.json`
         (`python -m umc.editor.rail`; `tests/test_editor_rail.py` checks they match, every tool is
         on it once, and every icon exists in both icon sets). Groups Select → Move & crop → Paint →
         Retouch → Shapes & text → Adjust → Filters → Image (Effects comes with step 7); items are
         tools, actions (run at once) or a menu (Rotate & flip). Icons are Tabler 3.24.0 (added to
         `gui/icon_data.py` / `web/src/ui/icon_data.ts`); the old painted / hand-drawn tool icons are
         gone. Desktop `gui/editor/tool_rail.py` `ToolRail` (QSettings `editor/rail_narrow`), web
         `ToolRail` in `ui/editor/panels.ts` (localStorage `umc-rail-narrow`; below 900 px it
         becomes an icon row). Names shown by default, the chevron narrows to icons. The top bar's
         Image / Select / Adjust / Filters menus and the Stickers button are gone; e2e uses `rail()`.
      4. ✅ Floating windows (both apps): `gui/editor/floating.py` (`PanelHost` / `FloatingPanel`,
         children of the canvas) and `web/src/ui/editor/floating.ts` (`FloatingHost`, the dialog
         element shown with `show()` inside `.canvas-host`). Title bar drags (kept inside the
         editor), — minimises into the rail icon (`set_panel_state` / `setPanelState` marks it
         "open" / "minimised"), × closes like Cancel; clicking the icon again toggles. Several
         open at once; positions in QSettings `editor/panel/<key>` / localStorage `umc-panel-<key>`.
         The old dialogs are embedded unchanged: desktop `QDialog`s become plain child widgets
         (`page._window(key, title, make, on_accept)`, OK = `accepted`), web `modal()` hands its
         <dialog> to the host (`setFloatingHost`; keys = the dialog class, `-` → `_`, = rail ids);
         Save options, "unsaved changes?" and the model download question stay modal. Live
         previews: the window changed last owns the preview (`page._preview_owner` /
         `previewRefresh`), a minimised one parks it, a picture change re-previews it. The tool
         options are a window too ("tool", only minimises, pinned to the canvas's top-right
         corner until dragged, title = the tool); clicking the chosen tool's icon toggles it. The
         right side holds only colours and layers. e2e `openEditor` minimises the tool window
         (it covers the picture); `showToolOptions()` brings it back.
      5. ✅ Live text layers (both apps): a Text-tool click puts a stand-in layer into the document
         straight away (`placeholder_layer` / `placeholderLayer`, no history); every keystroke,
         style or colour change redraws it (`live_text_layer` / `liveTextLayer` — also keeps a
         renamed / hidden / masked layer's settings) and refreshes the layers list. The typing box
         is transparent (desktop paints only the caret in `TextBox.paintEvent`, web
         `.text-box.live` + `caret-color`); a grip (`TextGrip` / `.text-grip`, never takes focus)
         drags the anchor. Committing restores the document (`_end_live_text` / `endLiveText`) and
         pushes the usual add / edit command — one undo step; `edit_text_layer(…, anchor)` moves it.
         The box follows zoom / pan (desktop `canvas.view_changed`).
      6. ✅ Shape layers (both apps): every line / arrow / rectangle / ellipse goes on a new layer
         (`ops.add_layers` / `addLayers`) carrying a `ShapeSpec` on `Layer.shape` (kind, points
         relative to the layer's x / y, colours, width, mode, heads, opacity; same fields in both
         apps; ORA attribute `umc-shape`). `shape_layer` / `shapeLayer` draws it (whole shape, no
         canvas clamp); `handles()` on the shape tools gives the corners / ends the canvas draws;
         a press on one reshapes (live, then one "Edit shape" step). A shape painted over
         (`shape_painted_over` / `shapePaintedOver` re-renders and compares) refuses with a notice.
         Transforms drop the spec like text (`_moved(op=…)`, web `shape: null`); layer menu
         "Rasterize layer" (`ops.rasterize_layer` / `rasterizeLayer`) does it for text and shapes.
         Shapes no longer follow the selection (they're layers).
         Step 5 review: every entry point commits text being typed first (`_commit_first`, canvas
         `before_press`, web `addFiles` / `setDocument` / a capturing pointerdown); focus moving to
         the tool options or the colours doesn't end the typing; live redraws repaint only the
         changed rectangle and the grip moves the layer instead of drawing it again.
      7. ✅ Layer effects (both apps): `Layer.fx` = a `LayerFx` (`editor/layerfx.py` / `layerfx.ts`, same
         fields; colours as "#rrggbb"): drop shadow, glow, outline, colour overlay, drawn live by
         `composite` from what the layer shows (mask applied, before opacity), bottom to top shadow →
         glow → outline → layer + overlay. `region` / `fxRegion` works on any rectangle (reads
         `margin` px around it), so partial repaints match the whole; the canvases grow dirty rects
         by the largest margin. Merges and "Rasterize layer" bake them in (`baked` / `bakedLayer`),
         transforms / duplicate / text and shape redraws keep them; ORA attribute `umc-fx` (the
         layer PNG stays its own pixels). Rail group "Effects" (`fx_shadow` …): one window per
         effect (`gui/editor/fx_window.py`, web `page.openFx`), opening it switches the effect on;
         a run of changes from one window is one undo step (`ops.tweak_layer_fx` / `tweakLayerFx`);
         an "fx" badge in the layers list, effect items + "Clear layer effects" in the layer menu.
         Desktop blur = Pillow GaussianBlur, web = its own 3-box approximation (soft edges differ a
         little). The web `SettingsForm` got working `set` / `setValues` and an id prefix.
         Step 7 review: whole-layer renders are cached (`_whole` / `whole`, last 4, keyed by a
         checksum of the pixels + mask + fx, so in-place strokes are noticed) and used when a
         repaint covers ≥ ¼ of the layer; desktop dilation uses OpenCV. An effect switched off keeps
         its settings (fx = None only when all back to defaults); file values are clamped to the
         window ranges (`LIMITS`); a run that ends where it started drops its redo
         (`History.drop_redo` / `dropRedo`). ORA marks checked recipes `umc-checked="1"`; recipes
         without it (files from before) keep the file's pixels as they are.
         Step 6 review: ORA keeps a text / shape recipe only while the pixels still match it (and
         there's no mask); on opening, a recipe is redrawn if the stored pixels are close (another
         app's anti-aliasing), else dropped — so shapes saved by one app reshape in the other.
         Reshaping keeps the mask on the same picture pixels and the handle's grab offset (a click
         changes nothing); flat rectangles add no layer; the live reshape preview is limited to the
         pasteboard.
   7. ✅ Document editor, first version (desktop) — spec `docs/superpowers/specs/2026-11-10-document-editor-design.md`:
      Convert | Edit for one DOCX, ODT, RTF, DOC, Markdown, TXT or HTML file. A `QTextEdit` page
      (`gui/doceditor/`) with headings, fonts, colours, highlight, lists, tables, pictures, links,
      page breaks, find & replace and undo. The model is Qt-free (`doceditor/`): the document is a
      small HTML subset plus picture bytes; DOCX is read and written by our own code, the other
      formats go through the document route planner. Saves back to the same format, as PDF, or as
      any document format; parts the editor can't keep (headers, comments, …) are listed in a
      notice before they are lost. Self-test step "document editor".
      Not yet: website, PDF editing, headers / footers, comments, tracked changes, zoom.
   8. ✅ 3D viewer (both apps; first step of the 3D editor): Convert | Edit when every file is a 3D
      model. Qt-free model `editor3d/` (`mesh.py` = `load_view_model` via the converter's
      `load_model`, so mm rules match converting; stood on the bed like "Centre on the print bed";
      size / triangles / parts / watertight / volume rows from `describe`; `camera.py` = Z-up orbit
      camera, presets `VIEWS`). Web `web/src/editor3d/mesh.ts` / `camera.ts` are line-by-line
      ports (`tests/test_editor3d.py` ↔ `web/tests/editor3d.test.ts`, same cases). Desktop view
      `gui/editor3d/gl_view.py`: QOpenGLWidget, GLSL 1.20, no VAOs (runs on GL 2.1 / Mesa /
      opengl32sw); blending must keep dest alpha (`glBlendFuncSeparate(…, ZERO, ONE)`) or Qt
      composites the widget with wrong colours; Qt's offscreen platform has no QOpenGLWidget, so
      `gl_problem()` makes the page show a message (pixel test runs under xvfb + xcb only). Web
      page `ui/editor3d/page.ts` (three.js WebGL, models read in the worker by the hidden
      `model3d-view` converter → `mesh.bin` + `info.json`; `window.__viewer3d` with `?e2e`).
      Next: basic editing for 3D printing (plan in the session handoff / owner's go-ahead).
   9. ✅ Start screen = hub (both apps): editor tiles under the empty drop zone, listed in
      `core/editors.py` / `web/src/core/editors.ts` (same order and text; `available` False =
      greyed "Coming soon" — video and document editor for now; tests/test_editor_hub.py checks
      both lists). Image tile → "New picture" (`editor/blank.py` / `blank.ts`, presets in
      `web/src/editor/presets.ts` so the start page doesn't load the editor) → `MainWindow.new_picture`
      / `EditorPage.openBlank`; 3D tile → viewer `open_empty` / `openEmpty` with an "Open…" button
      (`files_chosen` / `onOpen` add the models to the queue like a drop). Tiles hide once files
      are added. Later steps (owner, Nov 2026): own viewer for "Open with", Windows installer with
      optional editors + "Convert" context menu, editors as downloadable add-ons on desktop.
   10. ✅ Viewer (desktop): `UniversalEdit --view FILE` (what "Open with" will run, step 4). Model
      `viewer/files.py` (view kind per file: image / video / audio / pages (PyMuPDF opens it) /
      convert (doc:pdf, vec:pdf, cad:pdf into a `umc-view-*` temp dir first) / text / model /
      other; `folder_items` = the folder's viewable files, natural sort, hidden skipped;
      `editor_for` follows `core/editors.py`) + `viewer/preview.py`. Window `gui/viewer/`:
      `ViewerWindow` (stage, side list with lazy thumbnails, bottom bar Convert / Edit; ← → PgUp
      PgDn Home End, F11, Space), `views.py` (ImageCanvas with QMovie for animations, MediaView =
      QMediaPlayer + QVideoWidget, PagesView renders only visible pages, TextView, InfoView),
      `tasks.py` (`TaskRunner`: lanes "pool" (2 threads) and "pdf" (1 thread — **everything that
      touches PyMuPDF goes there**: MuPDF isn't safe on two threads, even for different docs);
      a newer exclusive job or `cancel_group` drops older results; `shutdown()` on close doesn't
      wait, the preview temp dir is removed by a thread once jobs end). Playback = Qt Multimedia
      from **PySide6-Addons** (LGPL; its own LGPL FFmpeg ~25 MB in the build; `app.py` forces
      `QT_MEDIA_BACKEND=ffmpeg`; `--self-test` plays frames of an H.264 clip — Qt's
      `supportedVideoCodecs()` omits H.264 although it decodes it, so don't test with that).
      The spec's bin/ FFmpeg-DLL filter only drops copies whose source is in `packaging/bin`
      (Qt's FFmpeg DLLs can have the same names). `app.py` →
      `gui/launcher.AppController` holds every window (converter windows import lazily so the
      viewer opens fast); `gui/single_instance.py`: the first GUI run holds a QLockFile in the
      app-data folder and listens on a local socket named after the user (later runs never take
      it over); `--view` first hands its file to a running copy. A missing tool in a preview
      says "Press Convert to install it" (Convert has the one-click installer). PyMuPDF is warmed up
      in the background (its first import takes ~1 s). Offscreen Qt has no GL / audio device:
      the 3D view shows a message there, video is checked by counting frames on `videoSink()`.
   11. ✅ Windows installer + Explorer integration (desktop). `packaging/installer.iss` (Inno Setup
      6, built + silently installed / checked / uninstalled by `build-windows.yml`, artifact
      `UniversalEdit-setup`): per user (`PrivilegesRequired=lowest`,
      `%LOCALAPPDATA%\Programs\Universal Edit`), never change its `AppId`. Tasks "contextmenu" /
      "openwith" run `UniversalEdit.exe --register context-menu|open-with`; uninstall runs
      `--unregister all` (handled in `app.py` before Qt starts). The keys are written only by
      `tools/shell_integration.py` (HKCU via a `Registry` adapter — tests use a fake):
      `SystemFileAssociations\.ext\shell\UniversalEdit.Convert` (MultiSelectModel=Player,
      `--convert "%1"`: Explorer starts one copy per file, `single_instance` passes them on and
      `AppController.convert` groups files arriving within 3 s into one window), ProgID
      `UniversalEdit.View` + `OpenWithProgids` + `Applications\UniversalEdit.exe` + Capabilities
      / `RegisteredApplications` (`--view "%1"`). A program can't make itself the default:
      "Choose default apps…" opens `ms-settings:defaultapps`. The app's Settings window
      (`gui/settings_dialog.py`, cog top-left of the converter) switches both. Windows 11 shows
      the entry under "Show more options" (the short menu needs a signed MSIX — not done).
      **Never touch `HKCU\Software\UniversalEdit`** — that's QSettings (the app's own settings);
      Capabilities live in `Software\UniversalEdit.Integration`. `register_*` first runs
      `unregister_*`, which scans for our verb / ProgID everywhere (older versions' extensions).
      `single_instance.claim`: copies started together (a multi-file right-click while the app
      is closed) race for the QLockFile; the winner becomes the window, the others keep
      offering their file for up to 8 s. The sender is plain Python (`_exchange`: the named
      pipe `\\.\pipe\<name>` on Windows, Qt's socket file elsewhere) and waits for the
      window's "ok" before hanging up — closing right after writing lost the message on
      Windows, and a Qt socket on a helper thread crashed Qt. The installer's [Run] writes each switch as ticked and
      its [Code] starts an upgrade's ticks from the current registry state (Settings window).
   12. ✅ Editors as add-ons (desktop build only; from source everything is present).
      `core/addons.py` `ADDONS` (ids = `core/editors.py`): image = `umc.gui.editor` + site
      package `onnxruntime` (~24 MB zip); model = `umc.gui.editor3d.viewer_page` (the viewer's
      3D preview `gl_view` and the Qt-free `umc.editor` / `umc.editor3d` stay in the app: the
      converter (.ora), video text fonts, the hub and the viewer use them). Zips =
      `addon.json` + `umc/…` + `site/…`, built by `scripts/build_addons.py`, which also writes
      the git-ignored `src/umc/addon_hashes.json` the spec bundles (**run it before
      PyInstaller**; the spec refuses otherwise and keeps every other `umc` module via
      `collect_submodules`). Installed to `%LOCALAPPDATA%\UniversalEdit\addons\<id>` only if the
      SHA-256 matches and the manifest's version is this app's; `activate` (no restart) puts
      `site/` on `sys.path` and appends the add-on folder to the frozen parent package's
      `__path__`; `app.py` calls `activate_all` (also for `--self-test`). Downloads come from the
      GitHub release `v<__version__>` (`build-windows.yml` publishes it on a `v*` tag;
      `UMC_ADDONS_URL` overrides). GUI: `gui/addon_install.ensure_installed` (asked by
      `MainWindow._editor_ready` for tiles and Edit), tiles' "Install" badge, Settings
      "Editors" (install / remove). Installer: [Components] image / model → copied to {tmp}
      and installed by `UniversalEdit.exe --install-addon all <folder>`; uninstall deletes the
      add-ons folder. CI installs the built add-ons before `--self-test` ("editor add-ons
      installed and loading"). Tests simulate the built app with `UMC_ADDONS_TEST=1` +
      `UMC_ADDONS_DIR`.
   13. ✅ Audio editor (both apps; owner, Nov 2026: "a simple design like Audacity"). Convert | Edit
      when every file is audio (several are joined end to end at the first one's rate / channels);
      hub tile opens it empty. Model `audioedit/` / `web/src/audioedit/` (line-by-line ports, same
      test cases in `tests/test_audioedit.py` ↔ `web/tests/audioedit.test.ts`): planar float32
      samples; every edit is a list of *splices* (replace `[start, start+len(old))` with `new`),
      so undo keeps only what changed and same-length edits are written in place (history capped:
      500 MB desktop / 300 MB web). Edits: cut / copy / paste (mono ↔ stereo fitted), delete, trim,
      silence, fade in / out (linear), amplify (dB), normalize (peak −1 dB), reverse — amplify /
      normalize / reverse use the whole sound when nothing is selected. Waveform = min / max per
      column from a 256-frame block summary (`peaks.py` / `peaks.ts`; a block shared by two
      columns counts for both), straight from the samples when zoomed in. Opening: FFmpeg →
      `pcm_f32le` WAV, `aformat=channel_layouts=mono|stereo` (desktop `io.decode`; web the hidden
      `audio-edit-open` converter in the worker), read by our own `wav.py` / `wav.ts`; limits
      `MAX_SAMPLES` 240 M desktop / 80 M web. Saving: a float WAV through the normal audio
      converter (all its formats; only the bitrate is asked, for lossy ones); the first Save asks
      for a name ("<name> edited.<ext>", never over the original). Playback: desktop
      `gui/audioeditor/player.py` (QAudioSink pulling int16 from a QIODevice; offscreen / no
      device → a plain message), web Web Audio (AudioBuffer of the range; pause = suspend).
      Worker results must reach the page through bound-method slots (a lambda runs on the worker
      thread). Shortcuts: Space, Home / End, Ctrl+X / C / V, Delete, Ctrl+T, Ctrl+L, Ctrl+A, Esc,
      + / − (desktop also Ctrl+1 / 3; Chrome keeps those for tabs), Ctrl+E, Ctrl+F, Ctrl+Z / Y,
      Ctrl+S. Desktop add-on "audio" (`umc.gui.audioeditor`; the model stays in the app); self-test
      step "audio editor". e2e `e2e/audioeditor.spec.ts` (`window.__audio` with `?e2e`).
      Not yet: several tracks / mixing, recording, spectrogram, noise reduction, speed / pitch,
      editable selection times, tags (title / artist) are not carried over when saving.
   14. Properties mode (both apps), step 1 = read-only "About this file" — spec
      `docs/superpowers/specs/2026-11-12-properties-design.md`. Model `properties/` (desktop, no
      Qt) ↔ `web/src/properties/`: each app reads a file with its own engines into the same plain
      "facts" dicts (`read.py` / `converters/properties.ts`, hidden converter "properties-info" →
      facts.json), and `info.build` / `build` turn them into sections — line-by-line ports, both
      suites check `tests/fixtures/properties_cases.json` + `properties_format_cases.json`
      (regenerate only on purpose; avoid exact .5 values there — Python rounds half to even,
      JS `toFixed` half up). Dates are display text (local time) inside the facts, so the
      builders never touch time zones. Desktop panel `gui/properties/panel.py` (TaskRunner; PDFs
      on its "pdf" lane), web `ui/properties.ts`; viewer button → `AppController._move_to_main(…,
      "properties")`. Sample `core/samples.write_test_photo` is hand-written EXIF (no Pillow, the
      web e2e setup calls it). Web: no folder, no subtitle track list, Mediabunny can't read
      AVI / WMV / FLV (shows a "desktop app can" note). In both apps the editors' own top bars
      have no mode switch ("← Back to Convert"), so Properties is reached from the converter
      page. Next steps (owner's go-ahead given): change photo details (lossless EXIF segment
      swap), music tags (mutagen, GPL-2.0+, approved), video, PDF / Office, file name + dates.
   15. Orbiting drop-zone badges (both apps): 12 file-type badges (icon + type, own colour,
      dimmed to 62 %) on three tilted rings around the empty drop zone's text, smaller / fainter
      on the far side, faded near the text. Dragging a file over pulls them in and speeds them up;
      a drop on the empty page bursts them outward, then adds the files. Model `core/orbit.py`
      (`layout`, Qt-free) → `web/src/ui/orbit.json` (`python -m umc.core.orbit`), port
      `web/src/ui/orbit.ts` (canvas behind the text, `requestAnimationFrame`); desktop
      `gui/widgets/orbit.py` (`Orbit` paints in `DropZone.paintEvent`, 60 fps timer only while
      visible + empty). Both check `tests/fixtures/orbit_cases.json`. Reduce motion (web
      `prefers-reduced-motion`; Windows "Animation effects" via SPI_GETCLIENTAREAANIMATION;
      `UMC_REDUCE_MOTION=1/0` overrides) = still badges, no burst delay.
      Kept calm on purpose (owner): laps of 72–100 s, pull 1.8× speed, 650 ms soft burst.
11. **First public release** (owner, Nov 2026: wait until more editors are ready). Push a
    `v<__version__>` tag (bump `umc/__init__.py` first; the workflow checks they match): the
    Windows build then publishes the .exe zip, the installer and the editor add-on zips as a
    GitHub release. Until that release exists the in-app "Install" buttons can't download
    anything (only the installer adds editors). Before it: pin `softprops/action-gh-release`
    to a commit SHA and make the installer report a failed `--install-addon`.
12. Ideas: presets, batch queue.

## Handoff — read this first (for the next Claude account)

The converter (roadmap items 1–9) is done in both apps except the one parked item (9.15
LibreOffice-wasm). The owner hands the project over at this point; the next phase is the
**Editor** (item 10). Everything below is the agreed way of working.

### How the owner likes to work
- Plain, non-technical language in replies. One task at a time, then **stop and summarise**
  (what changed, what was verified, what's next) and list every file created/modified.
- Commits: on your worktree's branch, authored as `darkbepo`, imperative subject, **no co-author
  or session trailers**. Merge into `main` and push after each finished task (see "Start here"). Update the README "What works where" table and the
  roadmap here in the same commit as the feature.
- New dependencies must be AGPL-3.0-compatible (MIT/BSD/Apache/MPL/LGPL/GPL-3/AGPL). Big external
  programs are never bundled: detect + "Install it for me" (signed MSI/NSIS or SHA-256-checked
  zip, private folder, test conversion) — see `tools/installer.py` (LibreOffice, Pandoc,
  Ghostscript, Calibre, LibreDWG).

### How every change is verified
1. `ruff check . && ruff format --check .`, `pytest` (desktop), and in `web/`: `npx tsc --noEmit`,
   `npm test`, `npm run e2e` (Playwright, real conversions; fixtures made in `e2e/global-setup.ts`,
   no binaries in git except the small, attributed samples in `tests/fixtures/`).
2. Add a step to `src/umc/selftest.py` for anything that adds a library or bundled file, then run
   "Build Windows executable" (Actions → Run workflow): it builds the .exe and runs `--self-test`
   inside it — that's how packaging mistakes (missing hidden imports/data files) are caught.
3. Pushing to `web/` deploys GitHub Pages and re-runs every browser test against the live site
   (`pages.yml`; one retry for network flakes only). Runs that never start are GitHub-side —
   re-run them.
4. One-click installers are only proven by the manual "Test one-click installers" workflow
   (`tests/test_installer_live.py`, real downloads on windows-latest) — run it after touching
   `tools/installer.py`. Status of the last run (Nov 2026): LibreOffice + Pandoc ✅; Calibre ❌
   because `code.calibre-ebook.com` serves an incomplete TLS chain → now resolved via the GitHub
   releases API (re-run to confirm); **Ghostscript ❌ — the silent NSIS install
   (`gsNNNNNw64.exe /S /D=…`) didn't exit within 900 s on windows-latest.** Next steps: check
   whether `gswin64c.exe` exists after the timeout (install done, process hung?), try
   `Start-Process -Wait` instead of a piped subprocess, or avoid running the installer at all
   (7-Zip can unpack NSIS installers — 7-Zip is LGPL; needs its full `7z.exe` + `7z.dll`).
   LibreDWG's installer is new too and also needs a green live run. Until then EPS/PS and DWG
   work with a manually installed Ghostscript / LibreDWG (on PATH or the standard folders).

### Next phase: the Editor (roadmap item 10) — proposed plan
Keep the converter untouched; the editor is a second mode of the same window and reuses the
converters for saving/exporting. Suggested order (most value first), one task per step:

1. ✅ (desktop) **Shell:** a "Convert | Edit" switch at the top of the window (keep it minimal); dropping a
   single image/video/document while in Edit mode opens the matching editor. Web: same switch in
   `web/src/ui/`. Add GUI tests (pytest-qt) and e2e tests from the start.
2. ✅ first version on desktop — see roadmap 10.2 for what's in and what's next.
   **Image editor** (Photoshop-light). Qt-free model in `src/umc/editor/image/` (layers as Pillow
   RGBA images or numpy arrays, a command/undo stack, selections as masks); Qt view in
   `src/umc/gui/editor/` (`QGraphicsView`, `QUndoStack`). Tools in this order: crop / resize /
   rotate / flip → brightness, contrast, saturation, hue, curves → brush, eraser, fill, shapes,
   text → rectangle / lasso / magic-wand selection → layers (add, reorder, opacity, blend modes)
   → filters (blur, sharpen, noise). Open via `imaging.open_image` (so RAW/PSD/HEIC work); save via
   the existing image converter so every format + option keeps working. Project file: layered
   PSD (Pillow can't write PSD; consider a ZIP of PNG layers + JSON, or OpenRaster `.ora`, an open
   standard GIMP/Krita read). Web: `web/src/editor/` on `OffscreenCanvas` in the worker, same
   command model; export through the existing converters.
3. **Video timeline:** a model of tracks (video, images, audio, text) and clips (source, in/out,
   start, transitions, volume). Rendering = one FFmpeg `filter_complex` (trim/setpts, concat,
   overlay, xfade, amix, drawtext/ass) — reuse `converters/slideshow.py`, `video.py`,
   `subtitles.py`. Preview: thumbnails/filmstrip via FFmpeg; real playback needs
   `PySide6.QtMultimedia` — now bundled for the viewer (PySide6-Addons, LGPL, roadmap 10.10), so
   the timeline can reuse `gui/viewer/views.MediaView` — or play low-res proxy frames rendered by
   FFmpeg. Web: Mediabunny + WebCodecs for decoding/preview/export.
4. **Text / document editing** (✅ desktop first version, see roadmap 10.6; the website is next): rich-text editing of DOCX/ODT/MD/TXT/HTML (`QTextEdit` reads and
   writes HTML and Markdown; round-trip DOCX through Pandoc/LibreOffice via the document route
   planner). Web: a contenteditable/ProseMirror-style editor (MIT) + pandoc-wasm for import/export.

For each editor feature add rows to the README table (desktop / website columns) and a roadmap
line here, exactly like the converter features.

### Known gaps / ideas left open
- 9.15 LibreOffice-wasm (above). H.265 on the web depends on the visitor's hardware.
- Web: no EPS/PS, SVG → PDF, DWG, 7Z writing, archive encryption, TTF ↔ OTF, Kindle formats, PDF →
  Word with layout (all desktop-only, listed in the README table).
- Code signing for the .exe (owner's decision; SignPath.io is free for open source) — the real fix
  for antivirus false positives.
- No release published yet (roadmap 11): in-app editor downloads need one.
- Item 12 ideas: presets, batch queue, PWA/offline.

