"""Video → image sequence."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.video import trim_args
from umc.core.detect import MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ffmpeg

FRAME_FORMATS = {"png": "PNG", "jpg": "JPG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF"}


@dataclass
class FramesOptions:
    frames_per_30: int = opt(
        10,
        "Frames to keep per 30 frames",
        kind="slider",
        min=1,
        max=30,
        help="30 = every frame, 10 = every 3rd frame, 1 = one frame "
        "per 30. At 30 fps, 10 gives 10 images per second.",
    )
    quality: int = opt(
        92,
        "Quality",
        kind="slider",
        min=1,
        max=100,
        unit="%",
        targets=("frames:jpg", "frames:webp"),
    )
    scale: object = opt(
        "original",
        "Size",
        choices=[
            ("original", "Original"),
            (2160, "2160p"),
            (1440, "1440p"),
            (1080, "1080p"),
            (720, "720p"),
            (480, "480p"),
            (360, "360p"),
            ("percent", "Percentage…"),
        ],
    )
    scale_percent: int = opt(
        50,
        "Scale",
        kind="slider",
        min=5,
        max=200,
        unit="%",
        show_if=lambda v: v["scale"] == "percent",
    )
    start: str = opt("", "Start at", help="Seconds or mm:ss. Empty = beginning.")
    end: str = opt("", "End at", help="Seconds or mm:ss. Empty = until the end.")
    name_pattern: str = opt(
        "{name}_{n:05d}", "File names", help="{name} = video name, {n} = image number."
    )
    start_number: int = opt(1, "First number", min=0, max=1_000_000)


def select_expr(per_30: int) -> str:
    """FFmpeg ``select`` expression keeping ``per_30`` evenly spaced frames out of each 30."""
    if per_30 >= 30:
        return ""
    m = "mod(n\\,30)"
    return f"select='eq(floor(({m}+1)*{per_30}/30)-floor({m}*{per_30}/30)\\,1)'"


def ffmpeg_pattern(pattern: str, stem: str) -> str:
    """Turn ``"{name}_{n:05d}"`` into an FFmpeg image2 pattern (``stem_%05d``)."""
    safe_stem = stem.replace("%", "%%")
    out = pattern.replace("{name}", safe_stem)
    import re

    m = re.search(r"\{n(?::0?(\d+)d)?\}", out)
    if not m:
        out += "_{n:05d}"
        m = re.search(r"\{n(?::0?(\d+)d)?\}", out)
    width = m.group(1)
    token = f"%0{width}d" if width else "%d"
    return out[: m.start()] + token + out[m.end() :]


class VideoToFramesConverter(Converter):
    family = MediaFamily.VIDEO
    options_cls = FramesOptions

    def targets(self, files):
        return [
            self.target(
                f"frames:{ext}",
                label,
                ext,
                "Images (frame sequence)",
                f"Export frames as {label} images into a folder",
            )
            for ext, label in FRAME_FORMATS.items()
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: FramesOptions = job.options
        ext = job.target.ext
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            msg = f"Extracting frames from {f.name} ({i + 1}/{n})"
            prog = job.sub_progress(i / n, (i + 1) / n, msg)
            info = ffmpeg.probe(f.path)
            if not info.has_video:
                raise ConversionError(f"“{f.name}” has no video stream.")
            seek, duration = trim_args(o.start, o.end, info.duration)
            folder = unique_path(job.output_dir_for(f.path), f"{f.path.stem}_frames", "")
            folder.mkdir(parents=True)

            vf = []
            sel = select_expr(o.frames_per_30)
            if sel:
                vf.append(sel)
            if o.scale == "percent":
                vf.append(f"scale=trunc(iw*{o.scale_percent / 100:.4f}/2)*2:-2")
            elif o.scale != "original":
                vf.append(f"scale=-2:{int(o.scale)}")
            args = [*seek, "-i", str(f.path), "-an", "-sn"]
            if vf:
                args += ["-vf", ",".join(vf)]
            args += ["-fps_mode", "vfr", "-start_number", str(o.start_number)]
            if ext == "jpg":
                args += ["-q:v", str(round(31 - (o.quality - 1) * 29 / 99))]
            elif ext == "webp":
                args += ["-quality", str(o.quality)]
            elif ext == "png":
                args += ["-compression_level", "6"]
            args.append(str(folder / f"{ffmpeg_pattern(o.name_pattern, f.path.stem)}.{ext}"))
            try:
                ffmpeg.run_ffmpeg(
                    args,
                    total_seconds=duration,
                    progress=prog,
                    cancel_event=job.cancel_event,
                    error_message=f"Couldn't extract frames from “{f.name}”.",
                )
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            outputs.append(folder)
        job.report(1.0, "Done")
        return outputs
