"""Images → slideshow video (or animated GIF)."""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from umc.converters import codecs
from umc.converters.base import Converter
from umc.converters.imaging import fit_into, open_image, open_image_lazy
from umc.core.detect import MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ffmpeg

CONTAINERS = ["mp4", "mkv", "webm", "mov", "avi"]
PRESET_HEIGHTS = {"480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}
TRANSITIONS = [
    ("none", "None (hard cut)"),
    ("fade", "Crossfade"),
    ("fadeblack", "Fade through black"),
    ("fadewhite", "Fade through white"),
    ("dissolve", "Dissolve"),
    ("wipeleft", "Wipe left"),
    ("slideleft", "Slide left"),
    ("circleopen", "Circle open"),
    ("radial", "Radial"),
    ("zoomin", "Zoom in"),
    ("pixelize", "Pixelize"),
]

_VIDEO_ONLY = tuple(f"slideshow:{c}" for c in CONTAINERS)


def _vcodecs():
    return [("auto", "Auto (best for format)")] + [
        (k, codecs.VIDEO_CODEC_LABELS[k]) for k in ("h264", "h265", "vp9", "av1", "mpeg4")
    ]


@dataclass
class SlideshowOptions:
    seconds_per_image: float = opt(
        2.0, "Seconds per image", kind="slider", min=0.1, max=10.0, step=0.1, decimals=1, unit="s"
    )
    order: str = opt(
        "selected",
        "Image order",
        choices=[
            ("selected", "As listed (drag to reorder)"),
            ("name", "By file name"),
            ("date", "By date modified"),
            ("reverse", "Reverse of list"),
            ("random", "Shuffle"),
        ],
    )
    resolution: str = opt(
        "first",
        "Resolution",
        choices=[
            ("first", "Match first image"),
            ("largest", "Match largest image"),
            ("480p", "480p"),
            ("720p", "720p (HD)"),
            ("1080p", "1080p (Full HD)"),
            ("1440p", "1440p"),
            ("4k", "4K"),
            ("custom", "Custom…"),
        ],
    )
    orientation: str = opt(
        "landscape",
        "Orientation",
        choices=[
            ("landscape", "Landscape (16:9)"),
            ("portrait", "Portrait (9:16)"),
            ("square", "Square (1:1)"),
        ],
        show_if=lambda v: v["resolution"] in PRESET_HEIGHTS,
    )
    width: int = opt(
        1920, "Width", min=16, max=8192, unit="px", show_if=lambda v: v["resolution"] == "custom"
    )
    height: int = opt(
        1080, "Height", min=16, max=8192, unit="px", show_if=lambda v: v["resolution"] == "custom"
    )
    fit: str = opt(
        "letterbox",
        "Fit images",
        choices=[
            ("letterbox", "Fit (add bars)"),
            ("crop", "Fill (crop edges)"),
            ("stretch", "Stretch"),
        ],
    )
    background: str = opt("#000000", "Bar / background colour", kind="color")
    transition: str = opt("none", "Transition", choices=TRANSITIONS, targets=_VIDEO_ONLY)
    transition_duration: float = opt(
        0.5,
        "Transition length",
        kind="slider",
        min=0.1,
        max=3.0,
        step=0.1,
        decimals=1,
        unit="s",
        targets=_VIDEO_ONLY,
        show_if=lambda v: v["transition"] != "none",
    )
    fps: int = opt(30, "Frame rate", kind="slider", min=1, max=60, unit="fps", targets=_VIDEO_ONLY)
    video_codec: str = opt("auto", "Video codec", choices=_vcodecs(), targets=_VIDEO_ONLY)
    crf: int = opt(
        20,
        "Quality (CRF)",
        kind="slider",
        min=0,
        max=51,
        targets=_VIDEO_ONLY,
        help="Lower = better quality, bigger file.",
    )
    audio_file: str = opt(
        "",
        "Background music (optional)",
        kind="file",
        targets=_VIDEO_ONLY,
        file_filter="Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus)",
    )
    audio_fade_out: bool = opt(
        True,
        "Fade music out at the end",
        targets=_VIDEO_ONLY,
        show_if=lambda v: bool(v["audio_file"]),
    )
    loop: int = opt(
        0,
        "Loop",
        targets=("slideshow:gif",),
        choices=[(0, "Forever"), (-1, "Play once"), (1, "Repeat 1×"), (3, "Repeat 3×")],
    )


def order_files(paths: list[Path], order: str) -> list[Path]:
    if order == "name":
        return sorted(paths, key=lambda p: p.name.lower())
    if order == "date":
        return sorted(paths, key=lambda p: p.stat().st_mtime)
    if order == "reverse":
        return list(reversed(paths))
    if order == "random":
        shuffled = list(paths)
        random.shuffle(shuffled)
        return shuffled
    return list(paths)


def oriented_size(path: Path) -> tuple[int, int]:
    """Image size after applying EXIF rotation, without decoding the pixels."""
    with open_image_lazy(path) as im:
        w, h = im.size
        if im.getexif().get(0x0112) in (5, 6, 7, 8):
            w, h = h, w
    return w, h


def output_size(o: SlideshowOptions, sizes: list[tuple[int, int]]) -> tuple[int, int]:
    if o.resolution == "custom":
        w, h = o.width, o.height
    elif o.resolution in PRESET_HEIGHTS:
        short = PRESET_HEIGHTS[o.resolution]
        if o.orientation == "portrait":
            w, h = short, round(short * 16 / 9)
        elif o.orientation == "square":
            w, h = short, short
        else:
            w, h = round(short * 16 / 9), short
    elif o.resolution == "largest":
        w, h = max(sizes, key=lambda s: s[0] * s[1])
    else:
        w, h = sizes[0]
    return max(2, w - w % 2), max(2, h - h % 2)  # even sizes for 4:2:0 video


class SlideshowConverter(Converter):
    family = MediaFamily.IMAGE
    options_cls = SlideshowOptions

    def targets(self, files):
        result = [
            self.target(
                f"slideshow:{c}",
                f"{c.upper()}",
                c,
                "Slideshow video",
                "Turn the images into a video",
            )
            for c in CONTAINERS
        ]
        if len(files) > 1:
            result.append(
                self.target(
                    "slideshow:gif", "GIF", "gif", "Slideshow video", "Animated GIF slideshow"
                )
            )
        return result

    def run(self, job: ConversionJob) -> list[Path]:
        o: SlideshowOptions = job.options
        paths = order_files([f.path for f in job.files], o.order)
        job.report(0.0, "Preparing images")
        with tempfile.TemporaryDirectory(prefix="umc-slides-") as tmp:
            frames = self._prepare(paths, o, Path(tmp), job)
            first = paths[0]
            stem = first.stem if len(paths) == 1 else f"{first.stem} slideshow"
            dest = unique_path(job.output_dir_for(first), stem, job.target.ext)
            try:
                if job.target.ext == "gif":
                    self._gif(frames, o, dest)
                else:
                    self._video(frames, o, dest, job)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
        job.report(1.0, "Done")
        return [dest]

    def _prepare(self, paths, o, tmp: Path, job) -> list[Path]:
        """Normalise every image to the same size as PNG (handles HEIC, EXIF rotation, alpha)."""
        if o.resolution == "first":
            sizes = [oriented_size(paths[0])]
        elif o.resolution == "largest":
            sizes = [oriented_size(p) for p in paths]
        else:
            sizes = []
        size = output_size(o, sizes)
        out = []
        for i, p in enumerate(paths):
            job.check_cancelled()
            img = ImageOps.exif_transpose(open_image(p))
            framed = fit_into(img, size, o.fit, o.background)
            dest = tmp / f"img{i:05d}.png"
            framed.save(dest, compress_level=1)
            out.append(dest)
            job.report(0.3 * (i + 1) / len(paths), f"Preparing images ({i + 1}/{len(paths)})")
        return out

    def _gif(self, frames: list[Path], o: SlideshowOptions, dest: Path) -> None:
        imgs = [Image.open(f).convert("RGB") for f in frames]
        kw = {"loop": o.loop} if o.loop >= 0 else {}  # no loop key = play once
        imgs[0].save(
            dest,
            save_all=True,
            append_images=imgs[1:],
            optimize=True,
            duration=round(o.seconds_per_image * 1000),
            **kw,
        )

    def _video(self, frames: list[Path], o: SlideshowOptions, dest: Path, job) -> None:
        container = dest.suffix.lstrip(".")
        codec = codecs.resolve_video_codec(container, o.video_codec)
        enc = codecs.video_encoder(codec)
        d = o.seconds_per_image
        n = len(frames)
        args: list[str] = []
        use_transition = o.transition != "none" and n > 1
        td = min(o.transition_duration, d * 0.9) if use_transition else 0.0

        if use_transition:
            clip = d + td
            for f in frames:
                args += ["-loop", "1", "-framerate", str(o.fps), "-t", f"{clip:.3f}", "-i", str(f)]
            parts = [f"[{i}:v]fps={o.fps},settb=AVTB,format=yuv420p[s{i}]" for i in range(n)]
            prev = "s0"
            for i in range(1, n):
                label = f"x{i}"
                parts.append(
                    f"[{prev}][s{i}]xfade=transition={o.transition}:"
                    f"duration={td:.3f}:offset={i * d:.3f}[{label}]"
                )
                prev = label
            graph = ";".join(parts)
            total = n * d + td
            vmap = f"[{prev}]"
        else:
            listing = Path(frames[0]).parent / "list.txt"
            lines = []
            for f in frames:
                lines += [f"file '{f.as_posix()}'", f"duration {d:.3f}"]
            lines.append(f"file '{frames[-1].as_posix()}'")  # concat demuxer needs the last twice
            listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
            args += ["-f", "concat", "-safe", "0", "-i", str(listing)]
            graph = f"[0:v]fps={o.fps},format=yuv420p[v]"
            total = n * d
            vmap = "[v]"

        audio_index = None
        if o.audio_file:
            audio = Path(o.audio_file)
            if not audio.is_file():
                raise ConversionError(f"The music file “{audio.name}” wasn't found.")
            audio_index = n if use_transition else 1
            args += ["-i", str(audio)]
            afilter = f"[{audio_index}:a]atrim=0:{total:.3f}"
            if o.audio_fade_out and total > 2:
                fade = min(2.0, total / 4)
                afilter += f",afade=t=out:st={total - fade:.3f}:d={fade:.3f}"
            graph += f";{afilter}[a]"

        args += ["-filter_complex", graph, "-map", vmap]
        if audio_index is not None:
            _, default_audio, _, _ = codecs.CONTAINERS[container]
            args += ["-map", "[a]", "-c:a", codecs.audio_encoder(default_audio), "-b:a", "192k"]
        args += ["-c:v", enc, *codecs.video_quality_args(enc, o.crf, "medium", None)]
        args += codecs.pixel_format_args(enc)
        args += ["-r", str(o.fps), "-t", f"{total:.3f}"]
        if container in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
        args.append(str(dest))
        ffmpeg.run_ffmpeg(
            args,
            total_seconds=total,
            progress=job.sub_progress(0.3, 1.0, "Rendering video"),
            cancel_event=job.cancel_event,
            error_message="Couldn't create the slideshow video.",
        )
