# Universal Media Converter

## ▶ [Open the converter in your browser](https://darkbepo.github.io/Universal-Media-Converter/)

**https://darkbepo.github.io/Universal-Media-Converter/** — nothing to install, works on Windows,
Mac, Linux and phones. Your files **never leave your device**: everything is converted right in
your browser, nothing is uploaded.

Drop files in, pick an output format, (optionally) tweak settings, press **Convert**.

| From | To |
|---|---|
| **Images** (PNG, JPG, WEBP, AVIF, HEIC, GIF, BMP, TIFF, ICO, …) | any other image format · a slideshow video or animated GIF · a PDF |
| **Videos** (MP4, MKV, MOV, AVI, WEBM, …) | other video formats · GIF · image frames (choose how many per 30 frames) · audio only (MP3, M4A, WAV, FLAC, OGG, OPUS) |
| **Documents** (PDF, DOCX, ODT, RTF, TXT, Markdown, HTML, EPUB, CSV, XLSX, PPTX, …) | other document formats · images (one per page) · split into several PDFs |

## Desktop app (for the heavy stuff)

Prefer a program on your PC, or need something the browser can't do? The **Windows desktop app**
adds old Office formats (DOC, XLS, PPT and OpenDocument spreadsheets/presentations), VP9 / H.265 /
AV1 video and very large files. It installs LibreOffice and Pandoc for you with one click when a
conversion needs them.

Get it from **Actions → Build Windows executable** → latest successful run → download
`UniversalMediaConverter-windows`, extract, and run `UniversalMediaConverter.exe`.

The browser version's source is in [`web/`](web/README.md).

## Running from source

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python scripts/fetch_ffmpeg.py   # Windows; elsewhere install ffmpeg with brew/apt
python -m umc
```

Office formats (DOC/DOCX/ODT/XLSX/PPTX…) need [LibreOffice](https://www.libreoffice.org/download/download/)
and Markdown/EPUB/HTML need [Pandoc](https://pandoc.org/installing.html). If one is missing, the
app offers to install it for you with one click — no restart needed.

## Building the Windows executable

On GitHub: **Actions → Build Windows executable → Run workflow**, then download the zip from
the finished run. Locally on Windows: `pyinstaller packaging/umc.spec` → `dist/UniversalMediaConverter/`.

## License

Copyright (C) 2026 darkbepo

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The bundled FFmpeg (GPL build) and PyMuPDF (AGPL-3.0) are why the app uses this licence.

See [CLAUDE.md](CLAUDE.md) for architecture and contributor notes.
