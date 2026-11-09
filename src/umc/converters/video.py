"""Video ↔ video."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters import codecs, sizing
from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt, parse_time
from umc.tools import ffmpeg

VIDEO_TARGETS = ["mp4", "mkv", "mov", "webm", "avi", "m4v", "flv", "wmv", "ts", "mpg", "3gp", "ogv"]

RESOLUTIONS = [
    ("original", "Original"),
    (2160, "4K (2160p)"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
    ("custom", "Custom…"),
]
FPS_CHOICES = [
    ("original", "Original"),
    (24, "24"),
    (25, "25"),
    (30, "30"),
    (50, "50"),
    (60, "60"),
    ("custom", "Custom…"),
]
SPEEDS = [
    (0.25, "0.25×"),
    (0.5, "0.5×"),
    (0.75, "0.75×"),
    (1.0, "1× (normal)"),
    (1.25, "1.25×"),
    (1.5, "1.5×"),
    (2.0, "2×"),
    (3.0, "3×"),
    (4.0, "4×"),
]
ROTATIONS = [(0, "None"), (90, "90° clockwise"), (270, "90° counter-clockwise"), (180, "180°")]


def _vcodec_choices():
    return [("auto", "Auto (best for format)"), ("copy", "Copy — no re-encode (fastest)")] + [
        (k, v) for k, v in codecs.VIDEO_CODEC_LABELS.items()
    ]


def _acodec_choices():
    return [("auto", "Auto"), ("copy", "Copy — no re-encode")] + [
        (k, v) for k, v in codecs.AUDIO_CODEC_LABELS.items() if k != "pcm_be"
    ]


def _encodes_video(v) -> bool:
    return v["video_codec"] != "copy"


@dataclass
class VideoOptions:
    video_codec: str = opt("auto", "Video codec", choices=_vcodec_choices(), group="Video")
    target_size_mb: float = opt(
        0.0,
        "Target file size",
        kind="float",
        min=0,
        max=100000,
        decimals=1,
        unit="MB",
        group="Video",
        help="0 = off. Encodes (in two passes where possible) to land just under this size — "
        "e.g. 25 for Discord or 10 for email. Overrides the quality setting.",
        show_if=_encodes_video,
    )
    rate_control: str = opt(
        "crf",
        "Rate control",
        choices=[("crf", "Constant quality"), ("bitrate", "Target bitrate")],
        show_if=_encodes_video,
        group="Video",
    )
    crf: int = opt(
        23,
        "Quality (CRF)",
        kind="slider",
        min=0,
        max=51,
        group="Video",
        help="Lower = better quality and bigger files. 18 ≈ visually lossless, "
        "23 = default, 28+ = small.",
        show_if=lambda v: (
            _encodes_video(v) and v["rate_control"] == "crf" and not v.get("target_size_mb")
        ),
    )
    video_bitrate: int = opt(
        5000,
        "Video bitrate",
        min=100,
        max=200000,
        unit="kbps",
        group="Video",
        show_if=lambda v: _encodes_video(v) and v["rate_control"] == "bitrate",
    )
    preset: str = opt(
        "medium",
        "Encoding speed",
        choices=[(p, p) for p in codecs.X264_PRESETS],
        help="Slower = smaller file at the same quality.",
        group="Video",
        show_if=_encodes_video,
    )
    resolution: object = opt(
        "original", "Resolution", choices=RESOLUTIONS, group="Video", show_if=_encodes_video
    )
    width: int = opt(
        1280,
        "Width",
        min=16,
        max=16384,
        unit="px",
        group="Video",
        help="0 = keep aspect ratio from height",
        show_if=lambda v: _encodes_video(v) and v["resolution"] == "custom",
    )
    height: int = opt(
        0,
        "Height",
        min=0,
        max=16384,
        unit="px",
        group="Video",
        help="0 = keep aspect ratio from width",
        show_if=lambda v: _encodes_video(v) and v["resolution"] == "custom",
    )
    fps: object = opt(
        "original", "Frame rate", choices=FPS_CHOICES, group="Video", show_if=_encodes_video
    )
    custom_fps: float = opt(
        30.0,
        "Custom frame rate",
        min=1,
        max=240,
        decimals=3,
        unit="fps",
        group="Video",
        show_if=lambda v: _encodes_video(v) and v["fps"] == "custom",
    )
    rotate: int = opt(0, "Rotate", choices=ROTATIONS, group="Video", show_if=_encodes_video)
    flip_horizontal: bool = opt(False, "Mirror horizontally", group="Video", show_if=_encodes_video)
    flip_vertical: bool = opt(False, "Flip vertically", group="Video", show_if=_encodes_video)
    speed: float = opt(1.0, "Playback speed", choices=SPEEDS, group="Video", show_if=_encodes_video)

    remove_audio: bool = opt(False, "Remove audio", group="Audio")
    audio_codec: str = opt(
        "auto",
        "Audio codec",
        choices=_acodec_choices(),
        group="Audio",
        show_if=lambda v: not v["remove_audio"],
    )
    audio_bitrate: int = opt(
        192,
        "Audio bitrate",
        group="Audio",
        choices=[(b, f"{b} kbps") for b in (64, 96, 128, 160, 192, 256, 320)],
        show_if=lambda v: not v["remove_audio"] and v["audio_codec"] not in ("copy", "flac", "pcm"),
    )
    volume: int = opt(
        100,
        "Volume",
        kind="slider",
        min=0,
        max=300,
        unit="%",
        group="Audio",
        show_if=lambda v: not v["remove_audio"] and v["audio_codec"] != "copy",
    )

    start: str = opt("", "Start at", help="Seconds or mm:ss. Empty = beginning.", group="Trim")
    end: str = opt("", "End at", help="Seconds or mm:ss. Empty = until the end.", group="Trim")
    fast_start: bool = opt(
        True,
        "Optimise for web streaming (fast start)",
        targets=("mp4", "m4v", "mov"),
        group="Other",
    )


def trim_args(start_text: str, end_text: str, duration: float) -> tuple[list[str], float]:
    """Return input-side seek args and the resulting duration (before speed change)."""
    start = parse_time(start_text)
    end = parse_time(end_text)
    if duration and start >= duration:
        raise ConversionError("The start time is after the end of the video.")
    if end and end <= start:
        raise ConversionError("The end time must be after the start time.")
    args = []
    if start:
        args += ["-ss", f"{start:.3f}"]
    if end:
        args += ["-t", f"{end - start:.3f}"]
    stop = min(end, duration) if (end and duration) else (end or duration)
    return args, max(0.0, stop - start)


def atempo_chain(speed: float) -> list[str]:
    """atempo only accepts 0.5–2.0, so chain filters for other speeds."""
    filters = []
    s = speed
    while s > 2.0:
        filters.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        filters.append("atempo=0.5")
        s /= 0.5
    filters.append(f"atempo={s:.4f}")
    return filters


class VideoConverter(Converter):
    family = MediaFamily.VIDEO
    options_cls = VideoOptions

    def targets(self, files: list[DetectedFile]):
        return [self.target(c, c.upper(), c, "Video") for c in VIDEO_TARGETS]

    def run(self, job: ConversionJob) -> list[Path]:
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            msg = f"Converting {f.name} ({i + 1}/{n})"
            job.report(i / n, msg)
            outputs.append(self.convert_one(f.path, job, job.sub_progress(i / n, (i + 1) / n, msg)))
        job.report(1.0, "Done")
        return outputs

    def convert_one(self, src: Path, job: ConversionJob, progress) -> Path:
        o: VideoOptions = job.options
        container = job.target.id
        info = ffmpeg.probe(src)
        if not info.has_video:
            raise ConversionError(f"“{src.name}” has no video stream.")
        seek, duration = trim_args(o.start, o.end, info.duration)
        copy_video = o.video_codec == "copy"
        two_pass = False

        args = [*seek, "-i", str(src), "-map", "0:v:0"]
        if info.has_audio and not o.remove_audio:
            args += ["-map", "0:a:0?"]
        args += ["-map_metadata", "0", "-sn"]

        # --- video
        vf: list[str] = []
        if copy_video:
            args += ["-c:v", "copy"]
        else:
            codec = codecs.resolve_video_codec(container, o.video_codec)
            enc = codecs.video_encoder(codec)
            vf += self._video_filters(o)
            if not codecs.pixel_format_args(enc)[-1].startswith("yuv422"):
                vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")  # 4:2:0 needs even sizes
            bitrate = o.video_bitrate if o.rate_control == "bitrate" else None
            if o.target_size_mb > 0:
                has_audio = info.has_audio and not o.remove_audio
                audio_kbps = (
                    0 if not has_audio else (128 if o.audio_codec == "copy" else o.audio_bitrate)
                )
                bitrate = sizing.video_kbps_for(o.target_size_mb, duration / o.speed, audio_kbps)
                two_pass = enc in sizing.TWO_PASS_ENCODERS
            args += ["-c:v", enc, *codecs.video_quality_args(enc, o.crf, o.preset, bitrate)]
            if o.target_size_mb > 0 and not two_pass:
                args += ["-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k"]
            args += codecs.pixel_format_args(enc)
            if o.fps != "original":
                fps = o.custom_fps if o.fps == "custom" else float(o.fps)
                args += ["-r", f"{fps:g}"]
        if vf:
            args += ["-vf", ",".join(vf)]

        # --- audio
        if o.remove_audio or not info.has_audio:
            args += ["-an"]
        elif o.audio_codec == "copy":
            args += ["-c:a", "copy"]
        else:
            acodec = codecs.resolve_audio_codec(container, o.audio_codec)
            args += ["-c:a", codecs.audio_encoder(acodec)]
            if acodec not in ("flac", "pcm"):
                args += ["-b:a", f"{o.audio_bitrate}k"]
            af = []
            if not copy_video and o.speed != 1.0:
                af += atempo_chain(o.speed)
            if o.volume != 100:
                af.append(f"volume={o.volume / 100:.2f}")
            if af:
                args += ["-af", ",".join(af)]

        if container in ("mp4", "m4v", "mov") and o.fast_start:
            args += ["-movflags", "+faststart"]

        dest = unique_path(job.output_dir_for(src), src.stem, container)
        total = duration / (o.speed if not copy_video else 1.0)
        error = f"Couldn't convert “{src.name}”."
        try:
            if two_pass:
                with tempfile.TemporaryDirectory(prefix="umc-2pass-") as tmp:
                    # Only the video stream is two-pass: a plain -pass also applies to the audio
                    # encoder, and newer FFmpeg then fails looking for a pass-1 log for audio.
                    log = str(Path(tmp) / "pass")
                    first = [
                        *args,
                        "-pass:v",
                        "1",
                        "-passlogfile:v",
                        log,
                        "-an",
                        "-f",
                        "null",
                        os.devnull,
                    ]
                    ffmpeg.run_ffmpeg(
                        first,
                        total_seconds=total,
                        progress=lambda f: progress(f / 2),
                        cancel_event=job.cancel_event,
                        error_message=error,
                    )
                    second = [*args, "-pass:v", "2", "-passlogfile:v", log, str(dest)]
                    ffmpeg.run_ffmpeg(
                        second,
                        total_seconds=total,
                        progress=lambda f: progress(0.5 + f / 2),
                        cancel_event=job.cancel_event,
                        error_message=error,
                    )
            else:
                ffmpeg.run_ffmpeg(
                    [*args, str(dest)],
                    total_seconds=total,
                    progress=progress,
                    cancel_event=job.cancel_event,
                    error_message=error,
                )
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return dest

    @staticmethod
    def _video_filters(o: VideoOptions) -> list[str]:
        vf = []
        if o.resolution == "custom":
            w = o.width or -2
            h = o.height or -2
            if w == -2 and h == -2:
                raise ConversionError("Enter a width or a height for the custom resolution.")
            vf.append(f"scale={w if w == -2 else w - w % 2}:{h if h == -2 else h - h % 2}")
        elif o.resolution != "original":
            vf.append(f"scale=-2:{int(o.resolution)}")
        if o.rotate == 90:
            vf.append("transpose=1")
        elif o.rotate == 270:
            vf.append("transpose=2")
        elif o.rotate == 180:
            vf.append("transpose=1,transpose=1")
        if o.flip_horizontal:
            vf.append("hflip")
        if o.flip_vertical:
            vf.append("vflip")
        if o.speed != 1.0:
            vf.append(f"setpts=PTS/{o.speed}")
        return vf
