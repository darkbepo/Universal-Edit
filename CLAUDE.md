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
  so the page bundle stays around ~100 KB), `engines/`, `ui/`. Target ids match the desktop ones.
- **Cancel** = terminate the worker (kills FFmpeg's nested worker too); a new worker starts next run.
- **Must be cross-origin isolated** (COOP/COEP): headers in `vite.config.ts` / `public/_headers`;
  where a host can't send them (GitHub Pages) `coi-serviceworker.js` (emitted by a plugin in
  `vite.config.ts`, loaded first in `index.html`) adds them and reloads the first visit once.
- **Local test version:** `npm run package:local` → zip with `app/` + `Start Universal Media Converter.bat`
  / `serve.ps1` (Windows) and `serve.py`, all localhost-only. Keep `.bat` files CRLF.
- **Keep `build.assetsInlineLimit: 0`** — an inlined (data:) FFmpeg thread worker can't load the
  engine and every video conversion crashes.
- **Codecs:** ffmpeg.wasm encodes H.264, VP8 and MPEG-4 only (in @ffmpeg/core 0.12.10
  libvpx-vp9 crashes the tab, libx265 hangs, no AV1 encoder). VP9 / AV1 / H.265 go through the
  browser's WebCodecs encoders via Mediabunny (`engines/webcodecs.ts`, `WEBCODECS_VIDEO` in
  `codecs.ts`): resize, fps, quality, target size, rotate, flip, trim; no speed/volume. Inputs
  Mediabunny can't read (AVI/FLV/WMV) get a near-lossless FFmpeg intermediate first. H.265 needs
  a hardware encoder (not in headless Linux Chromium, so it has no e2e test).
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
11. Ideas: installer for the app itself (Inno Setup), presets, batch queue.

## Handoff — read this first (for the next Claude account)

The converter (roadmap items 1–9) is done in both apps except the one parked item (9.15
LibreOffice-wasm). The owner hands the project over at this point; the next phase is the
**Editor** (item 10). Everything below is the agreed way of working.

### How the owner likes to work
- Plain, non-technical language in replies. One task at a time, then **stop and summarise**
  (what changed, what was verified, what's next) and list every file created/modified.
- Commits: on `main`, authored as `darkbepo`, imperative subject, **no co-author or session
  trailers**. Push after each finished task. Update the README "What works where" table and the
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

1. **Shell:** a "Convert | Edit" switch at the top of the window (keep it minimal); dropping a
   single image/video/document while in Edit mode opens the matching editor. Web: same switch in
   `web/src/ui/`. Add GUI tests (pytest-qt) and e2e tests from the start.
2. **Image editor** (Photoshop-light). Qt-free model in `src/umc/editor/image/` (layers as Pillow
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
   `PySide6.QtMultimedia`, which `packaging/umc.spec` currently **excludes** (and is not in
   PySide6-Essentials — needs PySide6-Addons, check size/licence) — or play low-res proxy frames
   rendered by FFmpeg. Web: Mediabunny + WebCodecs for decoding/preview/export.
4. **Text / document editing:** rich-text editing of DOCX/ODT/MD/TXT/HTML (`QTextEdit` reads and
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
- Item 11 ideas: installer for the app itself (Inno Setup), presets, batch queue, PWA/offline.

