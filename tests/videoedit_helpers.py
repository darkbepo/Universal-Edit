"""Helpers for the video editor render tests: render a project and measure the result."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from umc.tools import ffmpeg
from umc.videoedit.project import Project
from umc.videoedit.render import build_render


def render_to(project: Project, dest: Path, tmp: Path, **kw) -> Path:
    """Render ``project`` to ``dest`` (H.264 + AAC) with the editor's filter graph."""
    plan = build_render(project, tmp, **kw)
    args = list(plan.args)
    if plan.video_label:
        args += ["-map", plan.video_label, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
        args += ["-pix_fmt", "yuv420p"]
    if plan.audio_label:
        args += ["-map", plan.audio_label, "-c:a", "aac", "-b:a", "192k"]
    args.append(str(dest))
    ffmpeg.run_ffmpeg(
        args,
        total_seconds=plan.total,
        progress=lambda _p: None,
        cancel_event=threading.Event(),
        cwd=plan.cwd,
    )
    return dest


def frame_at(path: Path, t: float) -> Image.Image:
    cmd = [ffmpeg.ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path)]
    cmd += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(out)).convert("RGB")


def rms(path: Path, start: float, end: float) -> float:
    """RMS level (0–1) of the sound between ``start`` and ``end`` seconds."""
    cmd = [ffmpeg.ffmpeg_path(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    samples = np.frombuffer(out, dtype=np.int16).astype(np.float64) / 32768.0
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def durations(path: Path) -> tuple[float, float]:
    """(video, audio) stream durations in seconds; 0 for a missing stream."""
    cmd = [ffmpeg.ffprobe_path(), "-v", "error", "-print_format", "json", "-show_streams"]
    data = json.loads(subprocess.run([*cmd, str(path)], capture_output=True, check=True).stdout)
    result = {"video": 0.0, "audio": 0.0}
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind in result and not result[kind]:
            result[kind] = float(s.get("duration") or 0)
    return result["video"], result["audio"]
