"""Export a video project to a file (any container / codec, or an animated GIF). No Qt.

Long projects are rendered in groups of ``GROUP_SIZE`` main items first (near-lossless
intermediates), then joined — one FFmpeg graph with hundreds of inputs is slow and fragile.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from umc.converters import codecs
from umc.converters.gif import WIDTHS, GifOptions, VideoToGifConverter
from umc.core.detect import detect
from umc.core.errors import ConversionCancelled, ConversionError
from umc.core.job import ConversionJob
from umc.core.options import opt
from umc.tools import ffmpeg
from umc.videoedit.project import (
    MainItem,
    Project,
    Source,
    Transition,
    VideoClip,
    layout,
    output_fps,
    output_size,
)
from umc.videoedit.render import build_render

log = logging.getLogger(__name__)

GROUP_SIZE = 16
EXPORT_CONTAINERS = ("mp4", "mkv", "webm", "mov", "avi", "gif")
ERROR = "Couldn't export the video."

_HEIGHTS = {"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}


def _intermediate_args() -> list[str]:
    """Near-lossless H.264 for group / GIF intermediates (with the encoder fallbacks)."""
    enc = codecs.video_encoder("h264")
    quality = codecs.video_quality_args(enc, 12, "veryfast", None)
    return ["-c:v", enc, *quality, *codecs.pixel_format_args(enc)]


def _discard(path: Path) -> None:
    """Delete ``path`` if possible; FFmpeg may still hold it open just after a cancel."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Couldn't delete %s", path, exc_info=True)


def _not_gif(v: dict) -> bool:
    return v.get("container") != "gif"


def _gif(v: dict) -> bool:
    return v.get("container") == "gif"


@dataclass
class ExportOptions:
    container: str = opt(
        "mp4",
        "Format",
        choices=(
            ("mp4", "MP4"),
            ("mkv", "MKV"),
            ("webm", "WebM"),
            ("mov", "MOV"),
            ("avi", "AVI"),
            ("gif", "Animated GIF"),
        ),
    )
    video_codec: str = opt(
        "auto",
        "Video codec",
        choices=(
            ("auto", "Auto"),
            *((c, codecs.VIDEO_CODEC_LABELS[c]) for c in ("h264", "h265", "vp9", "av1", "mpeg4")),
        ),
        show_if=_not_gif,
    )
    audio_codec: str = opt(
        "auto",
        "Audio codec",
        choices=(
            ("auto", "Auto"),
            *((c, codecs.AUDIO_CODEC_LABELS[c]) for c in ("aac", "mp3", "opus", "flac")),
        ),
        show_if=lambda v: _not_gif(v) and not v.get("remove_audio"),
    )
    crf: int = opt(
        20,
        "Quality (CRF)",
        kind="slider",
        min=0,
        max=51,
        help="Lower = better quality, bigger file.",
        show_if=lambda v: _not_gif(v) and not v.get("video_bitrate"),
    )
    preset: str = opt("medium", "Encoder speed", choices=codecs.X264_PRESETS, show_if=_not_gif)
    video_bitrate: int = opt(
        0,
        "Video bitrate",
        kind="int",
        min=0,
        max=200000,
        unit="kbps",
        help="0 = use the quality setting.",
        show_if=_not_gif,
    )
    audio_bitrate: int = opt(
        192,
        "Audio bitrate",
        kind="int",
        min=32,
        max=512,
        unit="kbps",
        show_if=lambda v: _not_gif(v) and not v.get("remove_audio"),
    )
    resolution: str = opt(
        "project",
        "Resolution",
        choices=(
            ("project", "Project size"),
            ("480p", "480p"),
            ("720p", "720p"),
            ("1080p", "1080p"),
            ("4k", "4K"),
        ),
        help="Scales the project size to this height.",
    )
    remove_audio: bool = opt(False, "Remove audio", show_if=_not_gif)
    gif_fps: int = opt(
        15, "GIF frames per second", kind="slider", min=1, max=50, unit="fps", show_if=_gif
    )
    gif_width: int = opt(480, "GIF width", kind="int", min=16, max=4096, unit="px", show_if=_gif)


def export_size(project: Project, resolution: str) -> tuple[int, int]:
    """The project size, or scaled to a preset height (width kept even)."""
    w, h = output_size(project)
    height = _HEIGHTS.get(resolution)
    if height is None:
        return w, h
    width = round(w * height / h)
    return max(2, width - width % 2), height


def _pinned(project: Project, size: tuple[int, int]) -> Project:
    """Fix size and frame rate, so replacing the first video with an intermediate keeps them."""
    settings = replace(
        project.settings,
        resolution="custom",
        width=size[0],
        height=size[1],
        fps=f"{output_fps(project):g}",
    )
    return replace(project, settings=settings)


def _levels(count: int) -> int:
    levels = 0
    while count > GROUP_SIZE:
        count = math.ceil(count / GROUP_SIZE)
        levels += 1
    return levels


def _group_length(project: Project, items: tuple[MainItem, ...]) -> float:
    spans = layout(replace(project, main=items, text_rows=((),), music_rows=((),)))
    return max(s.end for s in spans.values())


def render_groups(
    project: Project,
    tmp: Path,
    progress: Callable[[float], None],
    cancel_event: threading.Event,
) -> Project:
    """Render runs of ``GROUP_SIZE`` main items to intermediates until the row is short enough.

    Each group is rendered without text and music, its first item without its transition;
    that transition joins the group clips instead, so the timing stays the same.
    """
    tmp = Path(tmp).absolute()
    project = _pinned(project, output_size(project))
    total_levels = _levels(len(project.main))
    level = 0
    previous: list[Path] = []
    while len(project.main) > GROUP_SIZE:
        main = project.main
        runs = [main[i : i + GROUP_SIZE] for i in range(0, len(main), GROUP_SIZE)]
        firsts = [run[0].transition_in for run in runs]
        runs = [(replace(run[0], transition_in=Transition()), *run[1:]) for run in runs]
        lengths = [_group_length(project, run) for run in runs]
        level_total = sum(lengths)
        done = 0.0
        w, h = output_size(project)
        fps = output_fps(project)
        sources: list[Source] = []
        clips: list[VideoClip] = []
        for k, (run, dur) in enumerate(zip(runs, lengths, strict=True)):
            if cancel_event.is_set():
                raise ConversionCancelled()
            sub = replace(project, main=run, text_rows=((),), music_rows=((),))
            plan = build_render(sub, tmp)
            path = tmp / f"group_{level}_{k}.mkv"
            args = [*plan.args, "-map", plan.video_label, *_intermediate_args()]
            args += ["-map", plan.audio_label, "-c:a", "pcm_s16le", "-t", f"{plan.total:.3f}"]
            base = (level + done / level_total) / total_levels
            width = dur / level_total / total_levels

            def report(f: float, base: float = base, width: float = width) -> None:
                progress(base + width * max(0.0, min(1.0, f)))

            ffmpeg.run_ffmpeg(
                [*args, str(path)],
                total_seconds=plan.total,
                progress=report,
                cancel_event=cancel_event,
                error_message=ERROR,
                cwd=plan.cwd,
            )
            done += dur
            sid = f"group_{level}_{k}"
            sources.append(Source(sid, path, "video", dur, w, h, fps, True))
            clips.append(
                VideoClip(sid, sid, 0.0, dur, fit="stretch", volume=100, transition_in=firsts[k])
            )
        for old in previous:  # the new level was rendered from these; free the disk
            _discard(old)
        previous = [s.path for s in sources]
        project = replace(project, sources=(*project.sources, *sources), main=tuple(clips))
        level += 1
    return project


def _codec_args(options: ExportOptions, plan_total: float, has_audio: bool) -> list[str]:
    container = options.container
    vcodec = codecs.resolve_video_codec(container, options.video_codec)
    venc = codecs.video_encoder(vcodec)
    args = ["-c:v", venc]
    bitrate = options.video_bitrate or None
    args += codecs.video_quality_args(venc, options.crf, options.preset, bitrate)
    args += codecs.pixel_format_args(venc)
    if has_audio:
        acodec = codecs.resolve_audio_codec(container, options.audio_codec)
        aenc = codecs.audio_encoder(acodec)
        args += ["-c:a", aenc]
        if acodec != "flac":
            args += ["-b:a", f"{options.audio_bitrate}k"]
        if aenc == "opus" or acodec == "flac":
            args += ["-strict", "-2"]  # FFmpeg's own Opus encoder; FLAC in MP4
    else:
        args.append("-an")
    if container in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    return [*args, "-t", f"{plan_total:.3f}"]


def _gif_options(options: ExportOptions) -> GifOptions:
    presets = {v for v, _ in WIDTHS if isinstance(v, int)}
    if options.gif_width in presets:
        return GifOptions(fps=options.gif_fps, width=options.gif_width)
    return GifOptions(fps=options.gif_fps, width="custom", custom_width=options.gif_width)


def _make_gif(
    source: Path,
    options: ExportOptions,
    out_dir: Path,
    report: Callable[[float, str], None],
    cancel_event: threading.Event,
) -> Path:
    converter = VideoToGifConverter()
    files = [detect(source)]
    job = ConversionJob(
        files=files,
        target=converter.targets(files)[0],
        options=_gif_options(options),
        output_dir=out_dir,
        on_progress=lambda p, _m: report(p / 100, "Making the GIF…"),
        cancel_event=cancel_event,
    )
    outputs = converter.run(job)
    if len(outputs) != 1:
        raise ConversionError(ERROR)
    return outputs[0]


def run_export(
    project: Project,
    options: ExportOptions,
    dest: Path,
    *,
    on_progress: Callable[[float, str], None],
    cancel_event: threading.Event,
) -> Path:
    """Write ``project`` to ``dest`` via ``<stem>.part<ext>``; nothing is left on failure."""
    dest = Path(dest).absolute()
    part = dest.with_name(f"{dest.stem}.part{dest.suffix}")
    gif = options.container == "gif"
    if dest.suffix.lower() != f".{options.container}":
        raise ConversionError(f"The file name must end in .{options.container}.")
    if not gif:  # check the codec choice before rendering anything
        codecs.resolve_video_codec(options.container, options.video_codec)
        if not options.remove_audio:
            codecs.resolve_audio_codec(options.container, options.audio_codec)

    def stage(start: float, end: float, message: str) -> Callable[[float], None]:
        def cb(f: float) -> None:
            on_progress((start + (end - start) * max(0.0, min(1.0, f))) * 100, message)

        return cb

    try:
        with tempfile.TemporaryDirectory(prefix="umc-vedit-", ignore_cleanup_errors=True) as d:
            tmp = Path(d)
            project = _pinned(project, export_size(project, options.resolution))
            begin = 0.0
            if len(project.main) > GROUP_SIZE:
                project = render_groups(
                    project, tmp, stage(0.0, 0.4, "Preparing the video…"), cancel_event
                )
                begin = 0.4
            if cancel_event.is_set():
                raise ConversionCancelled()
            if gif:
                plan = build_render(project, tmp, audio=False)
                source = tmp / "gif_source.mkv"
                args = [*plan.args, "-map", plan.video_label, *_intermediate_args()]
                args += ["-t", f"{plan.total:.3f}", str(source)]
                middle = begin + (1 - begin) / 2
                ffmpeg.run_ffmpeg(
                    args,
                    total_seconds=plan.total,
                    progress=stage(begin, middle, "Exporting…"),
                    cancel_event=cancel_event,
                    error_message=ERROR,
                    cwd=plan.cwd,
                )
                gif_step = stage(middle, 1.0, "Making the GIF…")
                made = _make_gif(
                    source, options, tmp / "gif", lambda f, _m: gif_step(f), cancel_event
                )
                shutil.move(made, part)
            else:
                audio = not options.remove_audio
                plan = build_render(project, tmp, audio=audio)
                args = [*plan.args, "-map", plan.video_label]
                if plan.audio_label:
                    args += ["-map", plan.audio_label]
                args += _codec_args(options, plan.total, plan.audio_label is not None)
                ffmpeg.run_ffmpeg(
                    [*args, str(part)],
                    total_seconds=plan.total,
                    progress=stage(begin, 1.0, "Exporting…"),
                    cancel_event=cancel_event,
                    error_message=ERROR,
                    cwd=plan.cwd,
                )
        try:
            os.replace(part, dest)
        except OSError as exc:
            raise ConversionError(
                f"Couldn't save over “{dest.name}”. Is it open in another program?",
                details=str(exc),
            ) from exc
    except ConversionError as exc:
        _discard(part)
        log.warning("Export failed: %s %s", exc, exc.details)
        raise
    except (ConversionCancelled, KeyboardInterrupt):
        _discard(part)
        raise
    except Exception as exc:
        _discard(part)
        log.exception("Export failed")
        raise ConversionError(ERROR) from exc
    on_progress(100.0, "Done")
    return dest
