"""Shared fixtures. Sample media is generated on the fly so no binaries live in git."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from umc.core import options as opts
from umc.core.detect import detect
from umc.core.job import ConversionJob
from umc.core.registry import targets_for

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("UMC_NO_AUDIO", "1")

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
needs_soffice = pytest.mark.skipif(
    shutil.which("soffice") is None and shutil.which("libreoffice") is None,
    reason="LibreOffice not installed",
)
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    for i, color in enumerate(["red", "green", "blue"]):
        img = Image.new("RGBA", (321, 241), color)
        img.putpixel((0, 0), (0, 0, 0, 0))  # some transparency
        img.save(d / f"img{i}.png")
    Image.new("RGB", (100, 200), "orange").save(d / "tall.jpg")
    frames = [Image.new("RGB", (64, 64), c) for c in ("red", "yellow", "blue")]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    (d / "doc.md").write_text("# Title\n\nHello **world**.\n\n- one\n- two\n", encoding="utf-8")
    (d / "notes.txt").write_text("Plain text line 1\nLine 2\n", encoding="utf-8")
    (d / "data.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    if shutil.which("ffmpeg"):
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=2:size=320x240:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=2",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                str(d / "clip.mp4"),
            ],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "sine=frequency=440:duration=3", "-ac", "2", str(d / "tone.wav")],
            check=True,
        )  # fmt: skip
    import pymupdf

    with pymupdf.open() as pdf:
        for n in range(3):
            page = pdf.new_page()
            page.insert_text((72, 72), f"Page {n + 1}")
        pdf.save(d / "three.pdf")
    return d


@pytest.fixture(scope="session")
def vmedia(tmp_path_factory) -> Path:
    """Small known clips for the video editor tests."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("vmedia")

    def ff(*args: str) -> None:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)

    def lavfi(spec: str) -> list[str]:
        return ["-f", "lavfi", "-i", spec]

    sine = lavfi("sine=frequency=440:duration=2")
    ff(*lavfi("color=red:size=320x240:rate=30:duration=2"), *sine, "-shortest",
       "-pix_fmt", "yuv420p", str(d / "red.mp4"))  # fmt: skip
    ff(*lavfi("color=blue:size=320x240:rate=30:duration=2"), "-pix_fmt", "yuv420p",
       str(d / "blue.mp4"))  # fmt: skip
    ff(*lavfi("color=green:size=240x320:rate=30:duration=1"),
       *lavfi("sine=frequency=330:duration=1"), "-shortest", "-pix_fmt", "yuv420p",
       str(d / "green_tall.mp4"))  # fmt: skip
    ff("-display_rotation", "90", "-i", str(d / "red.mp4"), "-c", "copy", str(d / "rot.mp4"))
    Image.new("RGB", (200, 100), "orange").save(d / "pic.png")
    ff(*lavfi("sine=frequency=880:duration=4"), "-ac", "2", str(d / "music.wav"))
    return d


def convert(paths, target_id: str, out_dir: Path, **option_values):
    files = [detect(p) for p in paths]
    targets = {t.id: t for t in targets_for(files)}
    assert target_id in targets, f"{target_id} not offered; got {sorted(targets)}"
    target = targets[target_id]
    cls = target.converter.options_cls
    job = ConversionJob(
        files=files,
        target=target,
        options=opts.build(cls, {**opts.defaults(cls), **option_values}),
        output_dir=out_dir,
    )
    return target.converter.run(job)


@pytest.fixture(scope="session", autouse=True)
def _release_clipboard_at_the_end():
    """The app does this on quit: without it, a picture the image editor tests copied crashes
    the test run's exit (Python-made clipboard data handed over after Python shut down)."""
    yield
    try:
        from umc.gui.clipboard_owner import release_clipboard
    except ImportError:  # no Qt
        return
    release_clipboard()
