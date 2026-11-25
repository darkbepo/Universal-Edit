"""Write tests/fixtures/fileinfo_cases.json: file names the desktop's Properties accepts or
refuses (``properties.fileinfo.clean_name``). The website's port (web/src/properties/fileinfo.ts)
checks the same cases. Regenerate only on purpose:
    python scripts/make_fileinfo_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

from umc.properties.fileinfo import FileError, clean_name

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fileinfo_cases.json"

CASES = [
    ("holiday", "photo.jpg"), ("holiday.jpg", "photo.jpg"), ("Holiday.JPG", "photo.jpg"),
    ("  spaced name  ", "photo.jpg"), ("holiday.png", "photo.jpg"), ("", "photo.jpg"),
    ("   ", "photo.jpg"), ("a/b", "photo.jpg"), ('bad<>:"|?*', "photo.jpg"), ("a\tb", "x.txt"),
    ("a\nb\rc", "x.txt"), ("..", "x.txt"), ("name.", "x.txt"), ("con", "x.txt"),
    ("LPT1.txt", "x.txt"), ("com10", "x.txt"), ("notes", "notes"), ("notes.txt", "notes"),
    (".gitignore", ".gitignore"), ("other", ".gitignore"), ("my file.tar gz", "a.txt"),
    ("x" * 252, "a.txt"), ("x" * 260, "a.txt"), ("café ☃", "a.txt"),
    (" nbsp ", "a.txt"), ("v1.2", "a.txt"), ("v1.2.txt", "a.txt"),
]  # fmt: skip


def main() -> None:
    out = []
    for text, old in CASES:
        try:
            out.append([text, old, clean_name(text, Path(old)), None])
        except FileError as e:
            out.append([text, old, None, str(e)])
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
