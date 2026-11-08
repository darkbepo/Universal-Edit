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
- Page size / margins where the target supports it

## Tech stack

- **Language:** Python 3.11+
- **GUI:** PySide6 (Qt 6) — custom QSS stylesheet for the minimal look
- **Images:** Pillow (+ `pillow-heif` for HEIC/AVIF)
- **Video / GIF / slideshow:** FFmpeg (bundled binary, invoked via `subprocess`; parse `-progress pipe:1` for progress)
- **PDF:** PyMuPDF (`fitz`) for PDF → images, page ranges, and future splitting
- **Office documents:** LibreOffice in headless mode (`soffice --headless --convert-to`) for doc/docx/odt/rtf ↔ pdf etc.
- **Text-ish formats:** Pandoc (via `pypandoc`) for md/html/txt/epub ↔ docx etc. where it gives better results
- **Packaging:** PyInstaller (one-folder build; bundle FFmpeg; LibreOffice detected or bundled — see Packaging)
- **Tests:** pytest

Primary target platform is **Windows** (`.exe`); keep code cross-platform (macOS/Linux) where it costs nothing.

## Architecture

```
src/umc/
  app.py              # entry point: creates QApplication, main window
  gui/
    main_window.py    # drop zone, format selector, settings panel, progress
    widgets/          # reusable widgets (DropZone, LabeledSlider, FormatPicker, …)
    settings_panels/  # one panel per conversion kind, built from the option schema
    style.qss
  core/
    detect.py         # detect media family from extension + magic bytes
    registry.py       # maps (source family, target format) -> Converter
    options.py        # dataclass-based option schemas (drive the settings UI)
    job.py            # ConversionJob: inputs, target, options, progress & cancel callbacks
    worker.py         # QThread/QRunnable wrapper around jobs
  converters/
    base.py           # Converter interface: supported_inputs, supported_outputs, options_schema, run(job)
    image.py          # image ↔ image
    slideshow.py      # images -> video
    video.py          # video ↔ video
    video_frames.py   # video -> images
    gif.py            # video -> gif
    document.py       # document ↔ document (LibreOffice / Pandoc / PyMuPDF)
    document_images.py# document -> images
  tools/
    ffmpeg.py         # locate bundled ffmpeg/ffprobe, run with progress parsing
    libreoffice.py    # locate soffice, run headless conversions
tests/
  fixtures/           # small sample files for each family
packaging/
  umc.spec            # PyInstaller spec
  bin/                # bundled third-party binaries (not committed; fetched by script)
```

Key rules:
- **The GUI never calls libraries directly** — it builds a `ConversionJob` and hands it to the
  registry. Converters know nothing about Qt.
- **Option schemas drive the UI.** Adding a setting = adding a field to the converter's options
  dataclass (with label, range, default); the settings panel renders it automatically.
- Adding a new format should usually mean touching one converter + its tests, nothing else.
- External tools (ffmpeg, soffice, pandoc) are located through `tools/` helpers that check the
  bundled path first, then `PATH`, and raise a clear, user-facing error if missing.
- Never overwrite an existing output file silently — auto-suffix (`name (1).ext`) or ask.

## Development commands

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m umc                                       # run the app
pytest                                              # run tests
ruff check . && ruff format .                       # lint + format
pyinstaller packaging/umc.spec                      # build the executable into dist/
```

## Packaging

- PyInstaller one-folder build is the default (faster startup than one-file, easier to bundle binaries).
- FFmpeg/ffprobe are bundled under `bin/`. Respect FFmpeg's licence (use an LGPL build unless we
  decide otherwise) and ship the licence text.
- LibreOffice is large; first version detects an installed LibreOffice and shows a helpful message
  if document ↔ office conversions are requested without it. Revisit bundling later.

## Coding conventions

- Type hints everywhere; `ruff` for lint/format; small, pure functions in `converters/`.
- Every converter has tests that run against `tests/fixtures/` and check the output opens/decodes.
- Long operations must report progress (0–100) and honour cancellation.
- Log to a rotating file in the user's app-data folder; the UI shows friendly messages.
- Commit messages: imperative mood, short subject line (`Add video to GIF converter`).

## Roadmap

1. **MVP:** project skeleton, GUI shell (drop zone + format picker + convert), image ↔ image.
2. Video ↔ video, video → GIF, video → images (frames-per-30 slider).
3. Images → video slideshow (seconds-per-image slider).
4. Documents ↔ documents, documents → images.
5. Packaging into a Windows executable, installer/icon.
6. **Later — document splitting:**
   - Split into chunks of *N* pages per document, or by custom ranges (e.g. `1-3, 4-10, 11-`)
   - Split every page into its own document
   - (Possible) merge documents, batch queue, drag-to-reorder inputs, presets for settings
