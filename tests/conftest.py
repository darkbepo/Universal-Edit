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
