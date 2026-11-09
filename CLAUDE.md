# CLAUDE.md — Universal Media Converter

Guidance for Claude (and humans) working in this repository.

## Project overview

Universal Media Converter is a **desktop application shipped as a standalone executable**.
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

- **Modern and minimalistic.** One window, no menus cluttering the top.
- Flow: **input → output format → (optional) settings → Convert**.
  1. Input area: a large drop zone that also opens a file picker on click. Accepts multiple files.
  2. Output format selector: only shows formats valid for the detected source type(s).
  3. Settings panel: collapsible ("Advanced"), contents change based on source → target pair.
  4. Convert button + progress bar + "Open output folder" when done.
- Dark/light theme following the OS. Rounded corners, generous spacing, one accent colour.
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
LibreOffice), VP9/H.265/AV1 and huge files.
See `web/README.md` for commands and deployment.

- **Stack:** TypeScript + Vite, no UI framework. Engines are WebAssembly, loaded lazily inside one
  long-lived module Worker (`src/worker/jobWorker.ts`): ffmpeg.wasm (`@ffmpeg/core`, multi-threaded
  `core-mt` when cross-origin isolated), ImageMagick (`@imagemagick/magick-wasm`), MuPDF (`mupdf`),
  Pandoc (official `pandoc-wasm`, ~16 MB gz; wired via `pandoc-wasm-core` / `pandoc-wasm-binary`
  aliases in `vite.config.ts` because the package only exports a browser entry Vite can't bundle).
- **Documents:** `converters/docroute.ts` is a port of the desktop route planner (pandoc edges +
  mupdf edges). Pandoc-wasm can't write PDF (no PDF engine), so → PDF goes via HTML laid out by
  MuPDF (`htmlToPdf`, which injects CSS to undo Pandoc's `table{display:block}` — otherwise tables
  collapse). TXT → PDF wraps the text in `<pre>` HTML (UTF-8, falling back to Windows-1252).
- **Same architecture as the desktop app:** `core/` (detect, options schema → auto form, registry,
  types — no DOM/WASM), `converters/` (declare options + targets; `run()` imports engines dynamically
  so the page bundle stays ~60 KB), `engines/`, `ui/`. Target ids match the desktop ones.
- **Cancel** = terminate the worker (kills FFmpeg's nested worker too); a new worker starts next run.
- **Must be cross-origin isolated** (COOP/COEP): headers in `vite.config.ts` / `public/_headers`;
  where a host can't send them (GitHub Pages) `coi-serviceworker.js` (emitted by a plugin in
  `vite.config.ts`, loaded first in `index.html`) adds them and reloads the first visit once.
- **Local test version:** `npm run package:local` → zip with `app/` + `Start Universal Media Converter.bat`
  / `serve.ps1` (Windows) and `serve.py`, all localhost-only. Keep `.bat` files CRLF.
- **Keep `build.assetsInlineLimit: 0`** — an inlined (data:) FFmpeg thread worker can't load the
  engine and every video conversion crashes.
- **Codecs:** only H.264, VP8 and MPEG-4 video. In @ffmpeg/core 0.12.10 libvpx-vp9 crashes the
  tab, libx265 hangs and there's no AV1 encoder — re-test before enabling (WebCodecs is the
  likely route for VP9/AV1/HEVC).
- ImageMagick enum: pass the format *value* (`"JPEG"`), not `MagickFormat["JPEG"]` (keys are
  PascalCase, so that lookup is undefined and silently keeps the input format).
- **Tests:** `npm test` (vitest, logic) and `npm run e2e` (Playwright: real conversions through the
  UI in headless Chromium; fixtures generated in `e2e/global-setup.ts`). CI: `.github/workflows/web.yml`.

## Tech stack

- **Language:** Python 3.11+
- **GUI:** PySide6-Essentials (Qt 6) — Fusion style + `gui/style.qss` with `@token` colours filled from `gui/theme.py`
- **Images:** Pillow (+ `pillow-heif` for HEIC; AVIF is native in Pillow ≥ 11.3)
- **Video / GIF / slideshow / audio:** FFmpeg (bundled binary, invoked via `subprocess`; progress parsed from `-progress pipe:1`)
- **PDF:** PyMuPDF (`pymupdf`) for PDF → images/text, page ranges, EPUB/XPS → PDF, and future splitting.
  All PyMuPDF use is isolated in `tools/pdf.py` (it is AGPL — see Licensing).
- **Office documents:** LibreOffice in headless mode (`soffice --headless --convert-to`) — *not bundled; one-click install*
- **Text-ish formats:** Pandoc CLI (called directly via `subprocess`, no `pypandoc`) for md/html/epub/rst/tex ↔ docx etc. — *not bundled; one-click install*
- **Packaging:** PyInstaller one-folder build; FFmpeg bundled; LibreOffice/Pandoc installed on demand by the app
- **Tests:** pytest + pytest-qt (GUI tests run with `QT_QPA_PLATFORM=offscreen`)

Primary target platform is **Windows** (`.exe`); keep code cross-platform (macOS/Linux) where it costs nothing.

## Architecture

```
src/umc/
  app.py                 # entry point: logging, QApplication, theme, main window
  gui/                   # the ONLY package that imports Qt
    main_window.py       # drop zone, file list, format picker, settings, progress, Convert/Cancel
    worker.py            # runs a ConversionJob on a QThread, reports via signals
    tool_dialog.py       # "LibreOffice/Pandoc needed" → Install it for me (+ manual fallbacks)
    theme.py, style.qss  # light/dark palettes + stylesheet (follows the OS colour scheme)
    widgets/             # DropZone, FormatPicker (pills), SettingsPanel (auto form), FlowLayout
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
    pdf_tools.py         # PDF toolkit: merge, compress, rotate, organize, protect, unlock
  tools/                 # wrappers around external programs/libraries
    process.py           # find_executable (bundled → PATH → known install dirs), cancellable run()
    installer.py         # one-click download + verify + install of LibreOffice / Pandoc (Windows)
    winsign.py           # Authenticode check via WinVerifyTrust/crypt32 (PowerShell's module is unreliable)
    ffmpeg.py, libreoffice.py, pandoc.py, pdf.py
tests/                   # fixtures are generated in conftest.py — no binary files in git
packaging/
  umc.spec               # PyInstaller spec
  icon.ico / icon.png    # generated by scripts/make_icon.py
  bin/                   # bundled ffmpeg/ffprobe (git-ignored; scripts/fetch_ffmpeg.py)
scripts/                 # fetch_ffmpeg.py, make_icon.py
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
pyinstaller packaging/umc.spec                      # build the executable into dist/
UniversalMediaConverter.exe --self-test report.txt  # headless smoke test of a build (exit 0 = OK)
```

Logs: `%LOCALAPPDATA%\UniversalMediaConverter\umc.log` (Windows),
`~/.local/state/UniversalMediaConverter/umc.log` (Linux), `~/Library/Logs/…` (macOS).

## Packaging

- PyInstaller one-folder build is the default (faster startup than one-file, easier to bundle binaries).
- FFmpeg/ffprobe are bundled under `bin/` (GPL build, includes x264/x265) together with its
  licence text (`FFMPEG-LICENSE.txt`).
- LibreOffice (~1 GB installed) and Pandoc are **not bundled**. Lookup order: the app's private
  tools folder (`%LOCALAPPDATA%\UniversalMediaConverter\tools`) → bundled → `PATH` → standard
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
  - Disk space is checked first; the MSI is deleted afterwards; msiexec logs go next to the tools.
- Conversions that don't need LibreOffice (images, video, PDF → images, Pandoc formats) must keep
  working when it is absent.
- **Antivirus false positives** (e.g. Defender `Trojan:Win32/Bearfoos.B!ml`, where `!ml` means a
  machine-learning guess): the prebuilt PyInstaller bootloader is shared with malware, so CI
  recompiles the bootloader from source (`PYINSTALLER_COMPILE_BOOTLOADER=1`), and the spec embeds
  Windows version info. Keep `upx=False` (UPX packing makes it worse). The real fix is code
  signing — not set up yet (needs the owner's decision; SignPath.io is free for open source).
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
- Work on `main`. Commits are authored as `darkbepo` with no co-author or session trailers
  (the owner asked for this).

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
   https://darkbepo.github.io/Universal-Media-Converter/ — `.github/workflows/pages.yml` publishes
   on every push to `main` that touches `web/` and then runs all browser tests against the live site.
   Next: WebCodecs for VP9/AV1, PWA/offline.
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
   5. Data formats: JSON ↔ CSV ↔ XML ↔ YAML ↔ XLSX
   6. 3D printing: STL, OBJ, 3MF, PLY, GLB/GLTF, STEP/IGES (units, scale, mesh detail)
   7. OCR (tesseract)
   8. Archives: ZIP, 7Z, RAR, TAR
   9. Subtitles: SRT/VTT/ASS, extract from / burn into video
   10. Fonts: TTF/OTF/WOFF/WOFF2
   11. Vector: SVG/EPS/AI, image → SVG tracing
   12. PDF → Word keeping layout
   13. eBooks: MOBI/AZW3 output (desktop, Calibre one-click install)
   14. CAD: DXF (DWG if feasible)
   15. Close web gaps: WebCodecs for H.265/VP9/AV1, LibreOffice-wasm for DOC/XLS/PPT
10. **Editor** (only after 9 is done): image editor (paint/photoshop-like), video timeline
    (trim, join, mix video + images + music), text/document editing and saving.
11. Ideas: installer for the app itself (Inno Setup), presets, batch queue.
