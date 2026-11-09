"""Download FFmpeg + ffprobe into packaging/bin so they get bundled into the executable.

Usage:  python scripts/fetch_ffmpeg.py [--lgpl] [--static]

Defaults to a GPL build, which includes x264/x265 (the best H.264/H.265 encoders). The project
is personal / open source (see CLAUDE.md → Licensing), so GPL is fine. ``--lgpl`` fetches an
LGPL build without them (the app then falls back to other H.264/H.265 encoders).

Defaults to the *shared* build: ffmpeg.exe and ffprobe.exe are small and share one set of
FFmpeg DLLs, instead of each carrying a full ~160 MB copy of FFmpeg (the static build).
Same FFmpeg, same speed. ``--static`` fetches the two self-contained exes instead.
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
    parser.add_argument("--static", action="store_true", help="self-contained exes (bigger)")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Automatic download is only set up for Windows builds.")
        print("On macOS/Linux install FFmpeg with your package manager (brew/apt) instead.")
        return 0
    arch = "winarm64" if platform.machine().lower() in ("arm64", "aarch64") else "win64"
    flavour = ("lgpl" if args.lgpl else "gpl") + ("" if args.static else "-shared")
    name = f"ffmpeg-master-latest-{arch}-{flavour}.zip"
    print(f"Downloading {name} ...")
    with urllib.request.urlopen(BTBN + name, timeout=300) as resp:
        data = resp.read()
    # Start clean, so switching between static and shared never leaves stale exes or DLLs.
    if BIN.is_dir():
        for old in BIN.iterdir():
            if (
                old.name in ("ffmpeg.exe", "ffprobe.exe", "FFMPEG-LICENSE.txt")
                or old.suffix == ".dll"
            ):
                old.unlink()
    BIN.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            p = Path(member)
            base = p.name
            in_bin = p.parent.name == "bin"
            # ffplay (a video player) and its SDL DLL aren't needed by the app.
            wanted = base in ("ffmpeg.exe", "ffprobe.exe", "LICENSE.txt") or (
                in_bin and base.endswith(".dll") and not base.lower().startswith("sdl")
            )
            if wanted:
                target = BIN / ("FFMPEG-LICENSE.txt" if base == "LICENSE.txt" else base)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                total += target.stat().st_size
                print(f"  -> {target}")
    print(f"{total / 1e6:.0f} MB in {BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
