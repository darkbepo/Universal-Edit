"""Download the OCR language data that ships with the app into packaging/tessdata.

Usage:  python scripts/fetch_tessdata.py [codes...]   (default: eng)

Files are verified against the SHA-256 digests in umc/tools/tessdata.py.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from umc.tools.tessdata import DIGESTS, URL  # noqa: E402

DEST = ROOT / "packaging" / "tessdata"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for code in sys.argv[1:] or ["eng"]:
        target = DEST / f"{code}.traineddata"
        sha, _size = DIGESTS[code]
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha:
            print(f"{code}: already there")
            continue
        with urllib.request.urlopen(URL.format(code=code), timeout=120) as resp:
            data = resp.read()
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"{code}: checksum mismatch", file=sys.stderr)
            return 1
        target.write_bytes(data)
        print(f"{code}: {len(data) >> 10} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
