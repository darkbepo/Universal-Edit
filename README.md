# Universal Media Converter

A minimal desktop app that converts documents, images and videos. Drop files in, pick an
output format, (optionally) tweak settings, press **Convert**.

| From | To |
|---|---|
| **Images** (PNG, JPG, WEBP, AVIF, HEIC, GIF, BMP, TIFF, ICO, …) | any other image format · a slideshow video (MP4/MKV/WEBM/MOV/AVI) or animated GIF · a PDF |
| **Videos** (MP4, MKV, MOV, AVI, WEBM, FLV, WMV, …) | any other video format · GIF · image frames (choose how many per 30 frames) · audio only (MP3, M4A, WAV, FLAC, OGG, OPUS) |
| **Documents** (PDF, DOCX, DOC, ODT, RTF, TXT, MD, HTML, EPUB, XLSX, PPTX, …) | other document formats · images (one per page) |

## Web companion

There's also a browser version in [`web/`](web/README.md) that converts images, videos and
documents entirely on your own device — nothing is uploaded. Old Office formats (DOC/XLS/PPT)
still need the desktop app.

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
app explains why and offers a download button — no restart needed after installing.

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
