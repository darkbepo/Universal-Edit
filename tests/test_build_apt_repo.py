"""scripts/build_apt_repo.py: the flat apt repository published with each release."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")


@pytest.fixture(scope="module")
def repo():
    spec = importlib.util.spec_from_file_location(
        "build_apt_repo", ROOT / "scripts" / "build_apt_repo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tiny_deb(tmp: Path, version: str = "1.2.3") -> Path:
    root = tmp / "pkg"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: universal-edit\nVersion: {version}\nArchitecture: amd64\n"
        "Maintainer: x <x@example.invalid>\nDescription: test\n test package\n"
    )
    deb = tmp / f"universal-edit_{version}_amd64.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "--build", str(root), str(deb)],
        check=True,
        capture_output=True,
    )
    return deb


def test_release_lists_every_index_file_with_its_hashes(repo):
    files = {"Packages": b"abc", "Packages.gz": b"xyz"}
    text = repo.release_file(files, datetime(2026, 11, 16, 3, 4, 5, tzinfo=UTC))
    assert "Date: Mon, 16 Nov 2026 03:04:05 UTC\n" in text
    sha = hashlib.sha256(b"abc").hexdigest()
    assert f" {sha}                3 Packages\n" in text
    for section in ("MD5Sum:", "SHA1:", "SHA256:"):
        assert section in text


@needs_dpkg
def test_packages_entry_points_at_the_deb(tmp_path, repo):
    deb = _tiny_deb(tmp_path)
    stanza = repo.package_stanza(deb)
    assert stanza.startswith("Package: universal-edit\nVersion: 1.2.3\n")
    assert f"Filename: ./{deb.name}\n" in stanza
    assert f"Size: {deb.stat().st_size}\n" in stanza
    assert f"SHA256: {hashlib.sha256(deb.read_bytes()).hexdigest()}\n" in stanza


@needs_dpkg
def test_build_without_a_key_writes_the_unsigned_index(tmp_path, repo):
    deb = _tiny_deb(tmp_path)
    out = tmp_path / "apt"
    written = repo.build([deb], out)
    assert sorted(p.name for p in written) == ["Packages", "Packages.gz", "Release"]
    assert gzip.decompress((out / "Packages.gz").read_bytes()) == (out / "Packages").read_bytes()
    assert "Packages.gz" in (out / "Release").read_text()


@needs_dpkg
@pytest.mark.skipif(shutil.which("gpg") is None, reason="needs gpg")
def test_signed_index_verifies_with_the_published_key(tmp_path, repo, monkeypatch):
    home = tmp_path / "g"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    if subprocess.run(["gpgconf", "--create-socketdir"], capture_output=True).returncode:
        pytest.skip("gpg can't make its socket folder here")
    made = subprocess.run(
        ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "T <t@example.invalid>",
         "ed25519", "sign", "never"],
        capture_output=True,
    )  # fmt: skip
    if made.returncode:
        pytest.skip("gpg can't make a key here")
    out = tmp_path / "apt"
    repo.build([_tiny_deb(tmp_path)], out, key="t@example.invalid")
    keyring = out / repo.KEYRING
    for args in (
        [str(out / "InRelease")],
        [str(out / "Release.gpg"), str(out / "Release")],
    ):
        check = subprocess.run(
            ["gpgv", "--keyring", str(keyring), *args],
            capture_output=True,
            env={**os.environ, "GNUPGHOME": str(home)},
        )
        assert check.returncode == 0, check.stderr
