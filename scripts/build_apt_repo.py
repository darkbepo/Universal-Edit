"""Make a signed *flat* apt repository for the .deb, published as GitHub release assets.

    python scripts/build_apt_repo.py dist/universal-edit_*.deb --out dist/apt --key KEYID

apt reads it from ``https://github.com/darkbepo/Universal-Edit/releases/download/apt`` — a
release of its own ("apt", a pre-release) that build-linux.yml updates after each version's
install tests pass, so ``sudo apt upgrade`` finds it (the README has the setup commands) and
never sees a half-published version. It holds the newest .deb plus:

* ``Packages`` / ``Packages.gz`` — the package's control fields + its file name, size, hashes;
* ``Release`` (hashes of those), ``InRelease`` (clear-signed) and ``Release.gpg`` (detached);
* ``universal-edit-archive-keyring.gpg`` — the public key users install once.

Signing uses the gpg key ``--key`` names (CI imports it from the APT_SIGNING_KEY secret);
without ``--key`` only the unsigned index is written (for tests).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

KEYRING = "universal-edit-archive-keyring.gpg"
BASE_URL = "https://github.com/darkbepo/Universal-Edit/releases/download/apt"
HASHES = (("MD5Sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"))


def _digests(data: bytes) -> dict[str, str]:
    return {name: hashlib.new(algo, data).hexdigest() for name, algo in HASHES}


def package_stanza(deb: Path) -> str:
    """The deb's control fields plus where to find it, for ``Packages``."""
    control = subprocess.run(
        ["dpkg-deb", "-f", str(deb)], check=True, capture_output=True, text=True
    ).stdout.rstrip("\n")
    data = deb.read_bytes()
    d = _digests(data)
    return (
        f"{control}\n"
        f"Filename: ./{deb.name}\n"
        f"Size: {len(data)}\n"
        f"MD5sum: {d['MD5Sum']}\n"
        f"SHA1: {d['SHA1']}\n"
        f"SHA256: {d['SHA256']}\n"
    )


def release_file(files: dict[str, bytes], now: datetime | None = None) -> str:
    """``Release``: what the repository is and the hashes of its index files."""
    now = now or datetime.now(UTC)
    lines = [
        "Origin: Universal Edit",
        "Label: Universal Edit",
        "Suite: stable",
        "Architectures: amd64",
        f"Date: {now.strftime('%a, %d %b %Y %H:%M:%S UTC')}",
    ]
    for name, _ in HASHES:
        lines.append(f"{name}:")
        for fname, data in sorted(files.items()):
            lines.append(f" {_digests(data)[name]} {len(data):>16} {fname}")
    return "\n".join(lines) + "\n"


def _gpg(*args: str) -> None:
    subprocess.run(["gpg", "--batch", "--yes", *args], check=True)


def build(debs: list[Path], out: Path, key: str | None = None) -> list[Path]:
    """Write the repository files into ``out``; returns them (to upload with the .deb)."""
    out.mkdir(parents=True, exist_ok=True)
    packages = "\n".join(package_stanza(d) for d in sorted(debs)).encode()
    index = {"Packages": packages, "Packages.gz": gzip.compress(packages, mtime=0)}
    written = []
    for name, data in index.items():
        (out / name).write_bytes(data)
        written.append(out / name)
    release = out / "Release"
    release.write_text(release_file(index), encoding="utf-8")
    written.append(release)
    if key:
        _gpg("--local-user", key, "--clearsign", "--output", str(out / "InRelease"), str(release))
        _gpg("--local-user", key, "--armor", "--detach-sign", "--output", str(out / "Release.gpg"),
             str(release))  # fmt: skip
        _gpg("--output", str(out / KEYRING), "--export", key)
        written += [out / "InRelease", out / "Release.gpg", out / KEYRING]
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("debs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--key", help="gpg key id / fingerprint to sign with")
    args = parser.parse_args()
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb is needed (Debian / Ubuntu)")
    for path in build(args.debs, args.out, args.key):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
