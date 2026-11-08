"""Download FFmpeg + ffprobe into packaging/bin so they get bundled into the executable.

Usage:  python scripts/fetch_ffmpeg.py [--lgpl]

Defaults to a GPL build, which includes x264/x265 (the best H.264/H.265 encoders). The project
is personal / open source (see CLAUDE.md → Licensing), so GPL is fine. ``--lgpl`` fetches an
LGPL build without them (the app then falls back to other H.264/H.265 encoders).
"""

from __future__ import annotations

import argparse
import io
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "packaging" / "bin"
BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lgpl", action="store_true", help="LGPL build (no x264/x265)")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Automatic download is only set up for Windows builds.")
        print("On macOS/Linux install FFmpeg with your package manager (brew/apt) instead.")
        return 0
    arch = "winarm64" if platform.machine().lower() in ("arm64", "aarch64") else "win64"
    name = f"ffmpeg-master-latest-{arch}-{'lgpl' if args.lgpl else 'gpl'}.zip"
    print(f"Downloading {name} …")
    with urllib.request.urlopen(BTBN + name, timeout=300) as resp:
        data = resp.read()
    BIN.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            base = Path(member).name
            if base in ("ffmpeg.exe", "ffprobe.exe") or base == "LICENSE.txt":
                target = BIN / ("FFMPEG-LICENSE.txt" if base == "LICENSE.txt" else base)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                print(f"  → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
