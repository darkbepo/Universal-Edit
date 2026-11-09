import pytest
from PIL import Image

from tests.conftest import convert, needs_ffmpeg


@pytest.mark.parametrize("fmt", ["jpg", "png", "webp", "bmp", "tiff", "gif", "ico", "avif", "heic"])
def test_image_to_image(media, tmp_path, fmt):
    from umc.converters.imaging import open_image

    [out] = convert([media / "img0.png"], fmt, tmp_path)
    assert out.suffix == f".{fmt}"
    img = open_image(out)
    assert img.size[0] > 0


@pytest.mark.parametrize("fmt", ["qoi", "avif", "heic"])
def test_palette_gif_to_rgb_only_formats_keeps_transparency(tmp_path, fmt):
    # QOI / AVIF / HEIC encoders only take RGB(A); a palette GIF used to fail for QOI.
    from umc.converters.imaging import open_image

    src = tmp_path / "pal.gif"
    img = Image.new("P", (40, 30), 1)
    img.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    img.putpixel((0, 0), 0)
    img.save(src, transparency=0)
    [out] = convert([src], fmt, tmp_path / "out")
    back = open_image(out).convert("RGBA")
    assert back.getpixel((0, 0))[3] == 0 and back.getpixel((20, 15))[:3][0] > 200


def test_png_to_jpg_flattens_transparency_on_background(media, tmp_path):
    [out] = convert([media / "img0.png"], "jpg", tmp_path, background="#00ff00")
    px = Image.open(out).convert("RGB").getpixel((0, 0))
    assert px[1] > 200 and px[0] < 60  # transparent corner became green


def test_resize_percent(media, tmp_path):
    [out] = convert([media / "img0.png"], "png", tmp_path, resize="percent", scale_percent=50)
    assert Image.open(out).size == (160, 120)


def test_resize_fit(media, tmp_path):
    [out] = convert([media / "tall.jpg"], "png", tmp_path, resize="fit", width=50, height=50)
    assert Image.open(out).size == (25, 50)


def test_animated_gif_to_webp_keeps_frames(media, tmp_path):
    [out] = convert([media / "anim.gif"], "webp", tmp_path)
    assert getattr(Image.open(out), "n_frames", 1) == 3


def test_existing_output_is_not_overwritten(media, tmp_path):
    a = convert([media / "img0.png"], "jpg", tmp_path)[0]
    b = convert([media / "img0.png"], "jpg", tmp_path)[0]
    assert a != b and a.exists() and b.exists()


def test_images_to_single_pdf(media, tmp_path):
    import pymupdf

    [out] = convert(
        [media / f"img{i}.png" for i in range(3)], "images:pdf", tmp_path, page_size="a4"
    )
    with pymupdf.open(out) as doc:
        assert doc.page_count == 3


@needs_ffmpeg
def test_slideshow_duration(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [out] = convert(
        [media / f"img{i}.png" for i in range(3)],
        "slideshow:mp4",
        tmp_path,
        seconds_per_image=0.5,
        resolution="720p",
    )
    info = probe(out)
    assert abs(info.duration - 1.5) < 0.15
    assert (info.width, info.height) == (1280, 720)


@needs_ffmpeg
def test_slideshow_with_crossfade(media, tmp_path):
    from umc.tools.ffmpeg import probe

    [out] = convert(
        [media / f"img{i}.png" for i in range(3)],
        "slideshow:webm",
        tmp_path,
        seconds_per_image=0.6,
        transition="fade",
        transition_duration=0.3,
    )
    assert abs(probe(out).duration - (3 * 0.6 + 0.3)) < 0.2


def test_slideshow_gif(media, tmp_path):
    [out] = convert(
        [media / f"img{i}.png" for i in range(3)], "slideshow:gif", tmp_path, seconds_per_image=0.2
    )
    im = Image.open(out)
    assert im.n_frames == 3
    assert im.info["duration"] == 200
