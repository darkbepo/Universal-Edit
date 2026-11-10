"""Preview rendering for the video editor: stills, playback streams, thumbnails, waveforms.

Everything is rendered small by the same filter graph as the export (``build_render``) on
a ``window`` of the timeline. Pure Python, no Qt.
"""

from __future__ import annotations

import contextlib
import logging
import math
import re
import subprocess
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import ImageOps

from umc.converters.imaging import open_image
from umc.tools import ffmpeg, process
from umc.videoedit.export import GROUP_SIZE
from umc.videoedit.project import Project, Source, layout, length, output_fps, output_size
from umc.videoedit.render import SAMPLE_RATE, RenderPlan, build_render
from umc.videoedit.window import window

log = logging.getLogger(__name__)

PREVIEW_HEIGHT = 360
CHUNK_SECONDS = 30.0
AUDIO_BLOCK = 4800  # samples per audio() block (0.1 s)
_AUDIO_FRAME = 4  # bytes per stereo s16le sample
ERROR = "Couldn't show the preview."


def preview_size(project: Project, height: int = PREVIEW_HEIGHT) -> tuple[int, int]:
    """The project size scaled to ``height`` (never up), both sides even."""
    w, h = output_size(project)
    if h <= height:
        return w, h
    width = round(w * height / h)
    return max(2, width - width % 2), max(2, height - height % 2)


def _background(project: Project, size: tuple[int, int]) -> bytes:
    bg = project.settings.background.lstrip("#")
    try:
        rgb = bytes.fromhex(bg[:6])
    except ValueError:
        rgb = b"\x00\x00\x00"
    return rgb * (size[0] * size[1])


def _plan_files(plan: RenderPlan) -> list[Path]:
    """The graph and ``.ass`` files this build wrote (the cached pictures stay)."""
    files = []
    if "-/filter_complex" in plan.args:
        files.append(plan.cwd / plan.args[plan.args.index("-/filter_complex") + 1])
    files += [plan.cwd / name for name in re.findall(r"ass=(text_[0-9a-f]+\.ass)", plan.graph)]
    return files


def _delete(files: list[Path]) -> None:
    for f in files:
        with contextlib.suppress(OSError):
            f.unlink(missing_ok=True)


def _read(proc: subprocess.Popen, n: int) -> bytes:
    "Up to `n` bytes of stdout; nothing once the pipe was closed by `close()`."
    try:
        return proc.stdout.read(n) if proc.stdout else b""
    except (ValueError, OSError):
        return b""


def _plan(
    project: Project, start: float, end: float, tmp: Path, size: tuple[int, int], *, video: bool
) -> RenderPlan:
    win = window(project, start, end)
    return build_render(
        win.project,
        tmp,
        size=size,
        video=video,
        audio=not video,
        lead=win.lead,
        duration=win.duration,
    )


def still(project: Project, t: float, tmp: Path, height: int = PREVIEW_HEIGHT) -> bytes:
    """One RGB24 frame (``w*h*3`` bytes) at timeline time ``t``."""
    size = preview_size(project, height)
    if t >= length(project) or t < 0:
        return _background(project, size)
    plan = _plan(project, t, t + 1 / output_fps(project), tmp, size, video=True)
    try:
        args = [*plan.args, "-map", plan.video_label or "", "-frames:v", "1"]
        args += ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        data = ffmpeg.ffmpeg_bytes(args, cwd=plan.cwd, error_message=ERROR)
    finally:
        _delete(_plan_files(plan))
    need = size[0] * size[1] * 3
    if len(data) < need:
        log.warning("Preview still at %.3f s was short (%d bytes)", t, len(data))
        return _background(project, size)
    return data[:need]


class StillCache:
    """The last ``max_frames`` stills, keyed by (project version, frame number)."""

    def __init__(self, max_frames: int = 200) -> None:
        self.max_frames = max_frames
        self._items: OrderedDict[tuple[int, int], bytes] = OrderedDict()

    def get(self, version: int, frame_no: int) -> bytes | None:
        key = (version, frame_no)
        data = self._items.get(key)
        if data is not None:
            self._items.move_to_end(key)
        return data

    def put(self, version: int, frame_no: int, data: bytes) -> None:
        key = (version, frame_no)
        self._items[key] = data
        self._items.move_to_end(key)
        while len(self._items) > self.max_frames:
            self._items.popitem(last=False)


@dataclass
class _Chunk:
    first: int  # frame index from the start time
    end: int  # one past the last frame
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)
    files: dict[str, list[Path]] = field(default_factory=dict)


class PlaybackStreams:
    """Frames and sound from ``t`` to the end, rendered chunk by chunk ahead of playback."""

    def __init__(self, project: Project, t: float, tmp: Path, height: int = PREVIEW_HEIGHT) -> None:
        self.project = project
        self.start = max(0.0, t)
        self.tmp = Path(tmp)
        self._fps = output_fps(project)
        self._size = preview_size(project, height)
        self._lock = threading.Lock()
        self._closed = False
        self._chunks = self._split()

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def _split(self) -> list[_Chunk]:
        total = length(self.project)
        frames_total = max(0, math.ceil((total - self.start) * self._fps - 1e-6))
        spans = layout(self.project)
        main_spans = [spans[item.id] for item in self.project.main]
        chunks: list[_Chunk] = []
        k = 0
        while k < frames_total:
            t = self.start + k / self._fps
            end = min(t + CHUNK_SECONDS, total)
            after = [s for s in main_spans if s.end > t + 1e-9]
            if len(after) > GROUP_SIZE:
                end = min(end, after[GROUP_SIZE].start)
            n = max(1, math.ceil((end - t) * self._fps - 1e-6))
            chunks.append(_Chunk(k, min(frames_total, k + n)))
            k = chunks[-1].end
        return chunks

    def _samples(self, frame: int) -> int:
        return round(frame * SAMPLE_RATE / self._fps)

    def _open(self, chunk: _Chunk, kind: str) -> subprocess.Popen | None:
        """Start (once) the chunk's video or audio process."""
        with self._lock:
            if self._closed:
                return None
            if kind in chunk.procs:
                return chunk.procs[kind]
            video = kind == "video"
            if video:
                start = self.start + chunk.first / self._fps
                end = self.start + chunk.end / self._fps
            else:
                start = self.start + self._samples(chunk.first) / SAMPLE_RATE
                end = self.start + self._samples(chunk.end) / SAMPLE_RATE
            plan = _plan(self.project, start, end, self.tmp, self._size, video=video)
            args = [*plan.args]
            if video:
                args += ["-map", plan.video_label or "", "-frames:v", str(chunk.end - chunk.first)]
                args += ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
            else:
                n = self._samples(chunk.end) - self._samples(chunk.first)
                args += ["-map", plan.audio_label or "", "-t", f"{n / SAMPLE_RATE:.6f}"]
                args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE)]
                args += ["-ac", "2", "pipe:1"]
            chunk.files[kind] = _plan_files(plan)
            try:
                proc = ffmpeg.ffmpeg_popen(args, cwd=plan.cwd)
            except Exception:
                _delete(chunk.files.pop(kind))
                raise
            chunk.procs[kind] = proc
            return proc

    def _finish(self, chunk: _Chunk, kind: str) -> None:
        with self._lock:
            proc = chunk.procs.get(kind)
            files = chunk.files.pop(kind, [])
        if proc is not None and proc.poll() is None:
            process._kill(proc)  # it may have a little more than we read
        _delete(files)

    def _prefetch(self, index: int, kind: str) -> None:
        if index + 1 < len(self._chunks):
            self._open(self._chunks[index + 1], kind)

    def frames(self) -> Iterator[tuple[float, bytes]]:
        """(timeline time, RGB24 frame) from the start time to the end, without gaps."""
        need = self._size[0] * self._size[1] * 3
        for i, chunk in enumerate(self._chunks):
            proc = self._open(chunk, "video")
            if proc is None:
                return
            count = chunk.end - chunk.first
            last: bytes | None = None
            dry = False
            try:
                for j in range(count):
                    if j == count // 2:
                        self._prefetch(i, "video")
                    data = b"" if dry else _read(proc, need)
                    if len(data) < need:
                        if self._closed:
                            return
                        dry = True
                        # Repeat the last frame (or show the background) so the timing stays.
                        data = last if last is not None else _background(self.project, self._size)
                    last = data
                    yield self.start + (chunk.first + j) / self._fps, data
            finally:
                self._finish(chunk, "video")
            if self._closed:
                return

    def audio(self) -> Iterator[bytes]:
        """s16le 48 kHz stereo from the start time to the end, in blocks of 0.1 s."""
        for i, chunk in enumerate(self._chunks):
            proc = self._open(chunk, "audio")
            if proc is None:
                return
            remaining = (self._samples(chunk.end) - self._samples(chunk.first)) * _AUDIO_FRAME
            half = remaining // 2
            prefetched = False
            dry = False
            try:
                while remaining > 0:
                    if not prefetched and remaining <= half:
                        self._prefetch(i, "audio")
                        prefetched = True
                    size = min(AUDIO_BLOCK * _AUDIO_FRAME, remaining)
                    data = b"" if dry else _read(proc, size)
                    if len(data) < size:
                        if self._closed:
                            return
                        dry = True
                        data += bytes(size - len(data))  # pad with silence to stay in step
                    remaining -= size
                    yield data
            finally:
                self._finish(chunk, "audio")
            if self._closed:
                return

    def close(self) -> None:
        """Stop every process and delete the build files; safe to call more than once."""
        with self._lock:
            self._closed = True
            procs = [p for c in self._chunks for p in c.procs.values()]
            files = [f for c in self._chunks for fs in c.files.values() for f in fs]
            for c in self._chunks:
                c.files.clear()
        for p in procs:
            if p.poll() is None:
                process._kill(p)
            with contextlib.suppress(Exception):
                p.stdout.close()
        _delete(files)


def thumbnails(source: Source, count: int, height: int, tmp: Path) -> list[Path]:
    """``count`` JPEG thumbnails spread over a video (one for a picture)."""
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    stem = f"thumb_{uuid.uuid4().hex[:8]}"
    if source.kind == "image":
        img = ImageOps.exif_transpose(open_image(source.path)).convert("RGB")
        width = max(1, round(img.width * height / max(1, img.height)))
        out = tmp / f"{stem}.jpg"
        img.resize((width, height)).save(out, format="JPEG", quality=85)
        return [out]
    if source.kind != "video" or count <= 0:
        return []
    rate = count / max(source.duration, 0.001)
    args = ["-i", str(source.path), "-an", "-vf", f"fps={rate:.6f},scale=-2:{height}"]
    args += ["-frames:v", str(count), "-q:v", "5", "-y", f"{stem}_%03d.jpg"]
    ffmpeg.ffmpeg_bytes(args, cwd=tmp, timeout=120.0, error_message=ERROR)
    return sorted(tmp.glob(f"{stem}_*.jpg"))


def waveform(source: Source, buckets: int) -> list[float]:
    """Peak level (0 to 1) per bucket of the source's sound; ``[]`` without sound."""
    if not source.has_audio or source.kind == "image" or buckets <= 0:
        return []
    args = ["-i", str(source.path), "-vn", "-ac", "1", "-ar", "8000"]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    data = ffmpeg.ffmpeg_bytes(args, timeout=120.0, error_message=ERROR)
    samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype=np.int16)
    if samples.size == 0:
        return []
    peaks = [
        float(np.abs(part.astype(np.int32)).max()) / 32768.0 if part.size else 0.0
        for part in np.array_split(samples, buckets)
    ]
    return [min(1.0, p) for p in peaks]
