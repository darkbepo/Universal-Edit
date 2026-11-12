"""FFmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from umc.core.errors import ConversionError, ToolMissingError
from umc.tools import process

DOWNLOAD_URL = "https://ffmpeg.org/download.html"


def ffmpeg_path() -> str:
    path = process.find_executable(["ffmpeg"])
    if path is None:
        raise ToolMissingError("FFmpeg", DOWNLOAD_URL, "convert video")
    return path


def ffprobe_path() -> str:
    path = process.find_executable(["ffprobe"])
    if path is None:
        raise ToolMissingError("FFmpeg (ffprobe)", DOWNLOAD_URL, "read video files")
    return path


def is_available() -> bool:
    return process.find_executable(["ffmpeg"]) is not None


@dataclass(frozen=True)
class MediaInfo:
    duration: float  # seconds (0 if unknown)
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frame_count: int = 0
    rotation: int = 0  # display rotation in degrees (0, 90, 180, 270), from the container


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def _rotation(stream: dict | None) -> int:
    """Display rotation of a video stream (phone clips), normalised to 0/90/180/270."""
    if not stream:
        return 0
    value = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            value = sd["rotation"]
            break
    if value is None:
        value = (stream.get("tags") or {}).get("rotate")
    try:
        return int(round(float(value))) % 360 if value is not None else 0
    except (TypeError, ValueError):
        return 0


def probe(path: Path) -> MediaInfo:
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            creationflags=process._creationflags(),
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Couldn't read “{path.name}”. Is it a valid media file?") from exc
    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0) or float(
        (video or {}).get("duration") or 0
    )
    fps = _parse_rate((video or {}).get("avg_frame_rate")) or _parse_rate(
        (video or {}).get("r_frame_rate")
    )
    frames = int((video or {}).get("nb_frames") or 0) or int(duration * fps)
    return MediaInfo(
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        fps=fps,
        frame_count=frames,
        rotation=_rotation(video),
    )


@dataclass(frozen=True)
class StreamSummary:
    has_video: bool
    has_audio: bool
    text_subtitles: int
    picture_subtitles: int


_summaries: dict[tuple[str, int, int], StreamSummary | None] = {}


def stream_summary(path: Path) -> StreamSummary | None:
    """Which kinds of streams a file has, for deciding which targets to offer.

    One quick ffprobe call, cached per file (path, size, mtime). ``None`` when it can't be read
    (no ffprobe, damaged file): callers should then offer everything and let the conversion
    explain the problem.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key in _summaries:
        return _summaries[key]
    summary = None
    try:
        cmd = [ffprobe_path(), "-v", "error", "-print_format", "json", "-show_streams", str(path)]
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            creationflags=process._creationflags(),
        ).stdout
        streams = json.loads(out).get("streams", [])
        subs = [s.get("codec_name", "") for s in streams if s.get("codec_type") == "subtitle"]
        text = sum(1 for c in subs if c in TEXT_SUBTITLE_CODECS)
        summary = StreamSummary(
            has_video=any(
                s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
                for s in streams
            ),
            has_audio=any(s.get("codec_type") == "audio" for s in streams),
            text_subtitles=text,
            picture_subtitles=len(subs) - text,
        )
    except (subprocess.SubprocessError, json.JSONDecodeError, ToolMissingError, OSError):
        summary = None
    _summaries[key] = summary
    return summary


def all_have(paths: Sequence[Path], has: Callable[[StreamSummary], bool]) -> bool:
    """True unless some file is known (read successfully) to lack what ``has`` checks for."""
    for p in paths:
        s = stream_summary(p)
        if s is not None and not has(s):
            return False
    return True


@cache
def encoders() -> frozenset[str]:
    try:
        out = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=process._creationflags(),
        ).stdout
    except (subprocess.SubprocessError, ToolMissingError):
        return frozenset()
    names = set()
    started = False
    for line in out.splitlines():
        if line.strip().startswith("------"):
            started = True
            continue
        parts = line.split()
        if started and len(parts) >= 2:
            names.add(parts[1])
    return frozenset(names)


def pick_encoder(candidates: Sequence[str]) -> str | None:
    available = encoders()
    return next((c for c in candidates if c in available), None)


def run_ffmpeg(
    args: Sequence[str],
    *,
    total_seconds: float,
    progress: Callable[[float], None],
    cancel_event: threading.Event,
    error_message: str = "FFmpeg couldn't convert this file.",
    cwd: Path | None = None,
) -> None:
    """Run ffmpeg with ``args`` (output path included), reporting 0..1 progress.

    ``cwd`` lets filter arguments use plain relative names (no path escaping in filtergraphs).
    """
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *args,
    ]

    def on_line(line: str) -> None:
        key, _, value = line.partition("=")
        if key in ("out_time_us", "out_time_ms") and value.strip().lstrip("-").isdigit():
            if total_seconds > 0:
                progress(int(value) / 1_000_000 / total_seconds)
        elif key == "progress" and value.strip() == "end":
            progress(1.0)

    try:
        process.run(
            cmd,
            cancel_event=cancel_event,
            on_stdout_line=on_line,
            error_message=error_message,
            cwd=cwd,
        )
    except ConversionError as exc:
        hint = _friendly_hint(exc.details)
        if hint:
            raise ConversionError(f"{error_message} {hint}", details=exc.details) from exc
        raise


def _friendly_hint(stderr: str) -> str:
    s = stderr.lower()
    if "unknown encoder" in s or "encoder not found" in s:
        return "The chosen codec isn't available in this FFmpeg build — try “Auto”."
    if "could not find tag for codec" in s or "not supported in container" in s:
        return "That codec can't be stored in this container — try “Auto”."
    if "invalid data found" in s:
        return "The source file looks damaged or isn't a real video."
    if "no space left" in s:
        return "The disk is full."
    return ""


TEXT_SUBTITLE_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "webvtt",
    "mov_text",
    "text",
    "microdvd",
    "subviewer",
    "sami",
    "realtext",
    "jacosub",
    "mpl2",
    "pjs",
    "vplayer",
    "stl",
    "ttml",
}


@dataclass
class SubtitleStream:
    index: int  # among the file's subtitle streams (for -map 0:s:N)
    codec: str
    language: str
    title: str

    @property
    def is_text(self) -> bool:
        return self.codec in TEXT_SUBTITLE_CODECS


def subtitle_streams(path: Path) -> list[SubtitleStream]:
    """Subtitle tracks inside a video file."""
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-select_streams",
        "s",
        "-print_format",
        "json",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            creationflags=process._creationflags(),
        ).stdout
        streams = json.loads(out).get("streams", [])
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Couldn't read “{path.name}”. Is it a valid video file?") from exc
    return [
        SubtitleStream(
            index=i,
            codec=s.get("codec_name", ""),
            language=(s.get("tags") or {}).get("language", ""),
            title=(s.get("tags") or {}).get("title", ""),
        )
        for i, s in enumerate(streams)
    ]


def probe_json(path: Path) -> dict:
    """ffprobe's full answer (format + streams) for the Properties panel."""
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=60,
            creationflags=process._creationflags(),
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Couldn't read “{path.name}”.") from exc
    return data if isinstance(data, dict) else {}
