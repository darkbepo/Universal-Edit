"""Download the subtitle font that ships with the app into packaging/fonts.

Usage:  python scripts/fetch_font.py

DejaVu Sans (Bitstream Vera licence + public domain) from the dejavu-fonts-ttf npm package —
the same file the web version uses — checked against a pinned SHA-256.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "packaging" / "fonts"
URL = "https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz"
TTF_SHA256 = "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954"
FILES = {
    "package/ttf/DejaVuSans.ttf": TTF_SHA256,
    "package/LICENSE": None,
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / "DejaVuSans.ttf"
    if (
        target.is_file()
        and hashlib.sha256(target.read_bytes()).hexdigest() == FILES["package/ttf/DejaVuSans.ttf"]
    ):
        print("DejaVuSans.ttf: already there")
        return 0
    with urllib.request.urlopen(URL, timeout=120) as resp:
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for name, sha in FILES.items():
            blob = tf.extractfile(name).read()
            if sha and hashlib.sha256(blob).hexdigest() != sha:
                print(f"{name}: checksum mismatch", file=sys.stderr)
                return 1
            out = DEST / ("DejaVuSans.ttf" if name.endswith(".ttf") else "DejaVu-LICENSE.txt")
            out.write_bytes(blob)
            print(f"{out.name}: {len(blob) >> 10} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
