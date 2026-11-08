"""Video → animated GIF (two-pass palette in a single FFmpeg filter graph)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.video import trim_args
from umc.core.detect import MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ffmpeg

WIDTHS = [
    ("original", "Original"),
    (240, "240 px"),
    (320, "320 px"),
    (480, "480 px"),
    (640, "640 px"),
    (800, "800 px"),
    (1080, "1080 px"),
    ("custom", "Custom…"),
]
DITHERS = [
    ("sierra2_4a", "Sierra Lite (balanced)"),
    ("floyd_steinberg", "Floyd–Steinberg"),
    ("bayer", "Bayer (pattern, small files)"),
    ("sierra2", "Sierra-2"),
    ("none", "None (banding, smallest)"),
]


@dataclass
class GifOptions:
    fps: int = opt(
        15,
        "Frames per second",
        kind="slider",
        min=1,
        max=50,
        unit="fps",
        help="More = smoother but bigger.",
    )
    width: object = opt(480, "Width", choices=WIDTHS, help="Height follows the aspect ratio.")
    custom_width: int = opt(
        480, "Custom width", min=16, max=4096, unit="px", show_if=lambda v: v["width"] == "custom"
    )
    start: str = opt("", "Start at", help="Seconds or mm:ss. Empty = beginning.")
    end: str = opt("", "End at", help="Seconds or mm:ss. Empty = until the end.")
    speed: float = opt(
        1.0,
        "Playback speed",
        choices=[
            (0.5, "0.5×"),
            (0.75, "0.75×"),
            (1.0, "1×"),
            (1.5, "1.5×"),
            (2.0, "2×"),
            (3.0, "3×"),
        ],
    )
    loop: int = opt(
        0,
        "Loop",
        choices=[
            (0, "Forever"),
            (-1, "Play once"),
            (1, "Repeat 1×"),
            (2, "Repeat 2×"),
            (3, "Repeat 3×"),
            (5, "Repeat 5×"),
        ],
    )
    optimise_palette: bool = opt(
        True,
        "Optimised colour palette",
        help="Builds a palette from the clip itself — much better colours.",
    )
    palette_mode: str = opt(
        "full",
        "Palette from",
        choices=[("full", "Whole clip"), ("diff", "Moving parts (better for screen recordings)")],
        show_if=lambda v: v["optimise_palette"],
    )
    max_colors: int = opt(
        256, "Max colours", kind="slider", min=2, max=256, show_if=lambda v: v["optimise_palette"]
    )
    dither: str = opt(
        "sierra2_4a", "Dithering", choices=DITHERS, show_if=lambda v: v["optimise_palette"]
    )
    bayer_scale: int = opt(
        3,
        "Bayer scale",
        kind="slider",
        min=0,
        max=5,
        show_if=lambda v: v["optimise_palette"] and v["dither"] == "bayer",
    )


class VideoToGifConverter(Converter):
    family = MediaFamily.VIDEO
    options_cls = GifOptions

    def targets(self, files):
        return [self.target("video:gif", "GIF", "gif", "Animation", "Animated GIF")]

    def run(self, job: ConversionJob) -> list[Path]:
        o: GifOptions = job.options
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            msg = f"Making GIF from {f.name} ({i + 1}/{n})"
            prog = job.sub_progress(i / n, (i + 1) / n, msg)
            info = ffmpeg.probe(f.path)
            if not info.has_video:
                raise ConversionError(f"“{f.name}” has no video stream.")
            seek, duration = trim_args(o.start, o.end, info.duration)
            width = o.custom_width if o.width == "custom" else o.width
            base = []
            if o.speed != 1.0:
                base.append(f"setpts=PTS/{o.speed}")
            base.append(f"fps={o.fps}")
            if width != "original":
                base.append(f"scale={int(width)}:-1:flags=lanczos")
            chain = ",".join(base)
            if o.optimise_palette:
                use = f"paletteuse=dither={o.dither}"
                if o.dither == "bayer":
                    use += f":bayer_scale={o.bayer_scale}"
                if o.palette_mode == "diff":
                    use += ":diff_mode=rectangle"
                graph = (
                    f"[0:v]{chain},split[a][b];"
                    f"[a]palettegen=max_colors={o.max_colors}:stats_mode={o.palette_mode}[p];"
                    f"[b][p]{use}"
                )
                vargs = ["-filter_complex", graph]
            else:
                vargs = ["-vf", chain]
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, "gif")
            args = [*seek, "-i", str(f.path), *vargs, "-an", "-loop", str(o.loop), str(dest)]
            try:
                ffmpeg.run_ffmpeg(
                    args,
                    total_seconds=duration / o.speed,
                    progress=prog,
                    cancel_event=job.cancel_event,
                    error_message=f"Couldn't make a GIF from “{f.name}”.",
                )
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
