"""Build the editor add-on zips (see src/umc/core/addons.py) and record their fingerprints.

    python scripts/build_addons.py [--out dist/addons]

Each zip holds ``addon.json``, the add-on's own modules under ``umc/…`` (from src/) and its
third-party packages under ``site/`` (from this Python's site-packages — build with the same
Python as the .exe). Writes ``src/umc/addon_hashes.json`` (git-ignored), which the PyInstaller
spec bundles: the app installs only zips with exactly these SHA-256 fingerprints.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from umc import __version__  # noqa: E402
from umc.core.addons import ADDONS, MANIFEST, sha256_of  # noqa: E402

SKIP_DIRS = {"__pycache__", "tests"}


def _files_of_module(dotted: str) -> list[tuple[Path, str]]:
    """(source file, path in the zip) for an own module or package under src/."""
    base = SRC.joinpath(*dotted.split("."))
    if base.is_dir():
        return [
            (p, p.relative_to(SRC).as_posix())
            for p in sorted(base.rglob("*"))
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(SRC).parts) and p.suffix != ".pyc"
        ]
    file = base.with_suffix(".py")
    if not file.is_file():
        raise SystemExit(f"{dotted}: no such module in src/")
    return [(file, file.relative_to(SRC).as_posix())]


def _files_of_package(name: str) -> list[tuple[Path, str]]:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit(f"{name}: not installed (pip install it first)")
    folder = Path(next(iter(spec.submodule_search_locations)))
    return [
        (p, "site/" + p.relative_to(folder.parent).as_posix())
        for p in sorted(folder.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]


def build(out: Path, only: list[str] | None = None, hashes_file: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for addon in ADDONS.values():
        if only and addon.id not in only:
            continue
        name = f"UniversalEdit-addon-{addon.id}-{__version__}.zip"
        dest = out / name
        files = [f for m in addon.modules for f in _files_of_module(m)]
        files += [f for pkg in addon.packages for f in _files_of_package(pkg)]
        manifest = {"id": addon.id, "version": __version__, "title": addon.title}
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
            for src, arc in files:
                zf.write(src, arc)
        hashes[addon.id] = {"file": name, "sha256": sha256_of(dest), "size": dest.stat().st_size}
        print(f"{name}: {dest.stat().st_size / 1e6:.1f} MB, {len(files)} files")
    target = hashes_file or SRC / "umc" / "addon_hashes.json"
    target.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "addons")
    build(parser.parse_args().out)


if __name__ == "__main__":
    main()
