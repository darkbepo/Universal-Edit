"""scripts/build_deb.py lays out a Debian package correctly (without running dpkg-deb)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build_deb():
    spec = importlib.util.spec_from_file_location("build_deb", ROOT / "scripts" / "build_deb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_app(folder: Path, marker: bool = True) -> Path:
    (folder / "_internal" / "umc").mkdir(parents=True)
    (folder / "UniversalEdit").write_text("binary")
    if marker:
        (folder / "_internal" / "umc" / "editors_bundled").write_text("x")
    return folder


def test_stage_lays_out_the_package(tmp_path, build_deb):
    app = _fake_app(tmp_path / "app")
    root = tmp_path / "root"
    build_deb.stage(app, root, "amd64")
    assert (root / "opt" / "universal-edit" / "UniversalEdit").is_file()
    link = root / "usr" / "bin" / "universal-edit"
    assert link.is_symlink() and str(link.readlink()) == "/opt/universal-edit/UniversalEdit"
    apps = root / "usr" / "share" / "applications"
    assert "Exec=universal-edit --view %f" in (apps / "universal-edit.desktop").read_text()
    assert "--convert %F" in (apps / "universal-edit-convert.desktop").read_text()
    icons = root / "usr" / "share" / "icons" / "hicolor"
    assert (icons / "scalable" / "apps" / "universal-edit.svg").is_file()
    assert (icons / "256x256" / "apps" / "universal-edit.png").is_file()
    assert (root / "usr" / "share" / "doc" / "universal-edit" / "copyright").is_file()
    control = (root / "DEBIAN" / "control").read_text()
    from umc import __version__

    assert f"Version: {__version__}\n" in control and "Architecture: amd64\n" in control
    assert "Depends: ffmpeg, " in control and "libxcb-cursor0" in control
    assert (root / "DEBIAN" / "postinst").stat().st_mode & 0o111


def test_description_is_valid_for_dpkg(build_deb):
    # continuation lines start with a space; an empty line is " ."
    first, *rest = build_deb.DESCRIPTION.splitlines()
    assert first and len(first) <= 80
    assert all(line.startswith(" ") and line.strip() for line in rest)


def test_refuses_a_build_without_the_editors(tmp_path, build_deb):
    app = _fake_app(tmp_path / "app", marker=False)
    with pytest.raises(SystemExit, match="editors_bundled"):
        build_deb.stage(app, tmp_path / "root", "amd64")
