import os
import subprocess

import pymupdf
import pytest
from PIL import Image

from tests.conftest import convert, needs_ffmpeg
from umc.core.errors import ConversionError

MB = 1024 * 1024


@pytest.fixture(scope="module")
def noisy_video(tmp_path_factory):
    """10 s of hard-to-compress video with sound."""
    path = tmp_path_factory.mktemp("v") / "noisy.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=size=640x360:rate=30:duration=10,noise=alls=40:allf=t", "-f", "lavfi", "-i",
         "sine=duration=10", "-shortest", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_video_lands_under_target(noisy_video, tmp_path):
    assert noisy_video.stat().st_size > 3 * MB
    [out] = convert([noisy_video], "mp4", tmp_path, target_size_mb=1.0, preset="ultrafast")
    size = out.stat().st_size
    assert 0.6 * MB < size <= 1.0 * MB


@needs_ffmpeg
def test_video_target_too_small_explains(noisy_video, tmp_path):
    with pytest.raises(ConversionError, match="too small"):
        convert([noisy_video], "mp4", tmp_path, target_size_mb=0.05)


@needs_ffmpeg
def test_audio_lands_under_target(media, tmp_path):
    [out] = convert([media / "tone.wav"], "mp3", tmp_path, target_size_mb=0.03)
    assert out.stat().st_size <= 0.03 * MB


def test_image_lands_under_target(tmp_path):
    src = tmp_path / "noise.png"
    Image.frombytes("RGB", (1200, 900), os.urandom(1200 * 900 * 3)).save(src)
    [out] = convert([src], "jpg", tmp_path / "out", target_size_kb=100)
    assert out.stat().st_size <= 100 * 1024
    assert Image.open(out).size[0] <= 1200


def test_pdf_lands_under_target(tmp_path):
    img = tmp_path / "p.png"
    Image.frombytes("RGB", (2400, 1800), os.urandom(2400 * 1800 * 3)).save(img)
    src = tmp_path / "photo.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(img))
        doc.save(src)
    [out] = convert([src], "pdf:compress", tmp_path / "out", level="lossless", target_size_mb=0.3)
    assert out.stat().st_size <= 0.3 * MB
