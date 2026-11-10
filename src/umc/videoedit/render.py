"""Turn a video project into one FFmpeg ``filter_complex``. Pure Python, no Qt.

Export runs the plan at full size; the preview runs it small on a ``Window`` slice
(``lead`` / ``duration`` drop the start and keep a few seconds).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageOps

from umc.converters.imaging import flatten, open_image
from umc.converters.video import atempo_chain
from umc.core.errors import ConversionError
from umc.videoedit.project import (
    AudioClip,
    MainItem,
    PictureClip,
    Project,
    Source,
    VideoClip,
    clip_duration,
    layout,
    length,
    output_fps,
    output_size,
)

SAMPLE_RATE = 48000
_AFORMAT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
_SILENCE = f"anullsrc=r={SAMPLE_RATE}:cl=stereo"


@dataclass
class RenderPlan:
    args: list[str]  # inputs + -/filter_complex <graph file>; the caller adds -map, codecs, output
    video_label: str | None
    audio_label: str | None
    total: float
    cwd: Path  # run FFmpeg here: picture inputs and the graph file are relative to it
    graph: str  # the filter graph text (also written to the graph file)


def _f(x: float) -> str:
    return f"{x:.3f}"


def _colour(bg: str) -> str:
    """``#rrggbb`` → ``0xrrggbb`` (FFmpeg's colour syntax, nothing special in a graph)."""
    return "0x" + bg.lstrip("#") if bg.startswith("#") else bg


class _Inputs:
    def __init__(self) -> None:
        self.args: list[str] = []
        self.count = 0

    def add(self, *args: str) -> int:
        self.args += args
        self.count += 1
        return self.count - 1


def _picture_png(source: Source, tmp: Path, bg: str) -> str:
    """The picture upright and flattened on ``bg``, as a path relative to ``tmp``.

    The name follows the source, the background and the file's size and time, so a new
    background or a replaced file gets a new PNG. Written under a temporary name and moved
    into place, so a build running at the same time never reads half a file.
    """
    stat = source.path.stat()
    key = f"{source.id}|{source.path}|{bg}|{stat.st_mtime_ns}|{stat.st_size}"
    name = f"pics/{hashlib.sha1(key.encode()).hexdigest()[:12]}.png"
    path = tmp / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img = ImageOps.exif_transpose(open_image(source.path))
        part = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.part")
        try:
            flatten(img, bg).save(part, format="PNG")
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)
    return name


def _write_graph(tmp: Path, graph: str) -> str:
    """Write the graph next to the pictures; returns its name relative to ``tmp``."""
    tmp.mkdir(parents=True, exist_ok=True)
    name = f"graph_{uuid.uuid4().hex[:8]}.txt"
    (tmp / name).write_text(graph, encoding="utf-8")
    return name


def _even_size(size: tuple[int, int]) -> tuple[int, int]:
    return tuple(max(2, n - (n % 2)) for n in size)  # type: ignore[return-value]


def _geometry(item: MainItem) -> list[str]:
    filters: list[str] = []
    if isinstance(item, VideoClip):
        left, top = item.crop_left / 100, item.crop_top / 100
        right, bottom = item.crop_right / 100, item.crop_bottom / 100
        if left or top or right or bottom:
            w = f"iw-iw*{_f(left)}-iw*{_f(right)}"
            h = f"ih-ih*{_f(top)}-ih*{_f(bottom)}"
            filters.append(f"crop={w}:{h}:iw*{_f(left)}:ih*{_f(top)}")
    filters += {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(
        item.rotate, []
    )
    if item.flip_h:
        filters.append("hflip")
    if item.flip_v:
        filters.append("vflip")
    if item.brightness or item.contrast or item.saturation:
        filters.append(
            f"eq=brightness={_f(item.brightness / 200)}:contrast={_f(1 + item.contrast / 100)}"
            f":saturation={_f(1 + item.saturation / 100)}"
        )
    return filters


def _fit(fit: str, w: int, h: int, bg: str) -> list[str]:
    if fit == "crop":
        return [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]
    if fit == "stretch":
        return [f"scale={w}:{h}"]
    return [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}",
    ]


def _video_chain(item: MainItem, k: int, i: int, w: int, h: int, fps: float, bg: str) -> str:
    dur = clip_duration(item)
    speed = item.speed if isinstance(item, VideoClip) else 1.0
    filters = [f"setpts=(PTS-STARTPTS)/{_f(speed)}", *_geometry(item), *_fit(item.fit, w, h, bg)]
    filters += [f"setsar=1,fps={_f(fps)},format=yuv420p"]
    filters += [f"tpad=stop_mode=clone:stop_duration={_f(dur)},trim=duration={_f(dur)}"]
    if item.fade_in > 0:
        filters.append(f"fade=t=in:d={_f(item.fade_in)}:color={bg}")
    if item.fade_out > 0:
        filters.append(f"fade=t=out:st={_f(dur - item.fade_out)}:d={_f(item.fade_out)}:color={bg}")
    filters.append("settb=AVTB")
    return f"[{k}:v]" + ",".join(filters) + f"[v{i}]"


def _audio_chain(item: MainItem, k: int | None, i: int) -> str:
    dur = clip_duration(item)
    if k is not None and isinstance(item, VideoClip):
        head = [f"[{k}:a]asetpts=PTS-STARTPTS", *atempo_chain(item.speed)]
        head.append(f"volume={item.volume / 100:.3f}")
    else:
        head = [_SILENCE]
    filters = [*head, _AFORMAT, "apad", f"atrim=duration={_f(dur)}"]
    if item.fade_in > 0:
        filters.append(f"afade=t=in:d={_f(item.fade_in)}")
    if item.fade_out > 0:
        filters.append(f"afade=t=out:st={_f(dur - item.fade_out)}:d={_f(item.fade_out)}")
    return ",".join(filters) + f"[a{i}]"


def _has_sound(project: Project, item: MainItem) -> bool:
    if not isinstance(item, VideoClip) or item.mute:
        return False
    return project.source(item.source).has_audio


def _music_chain(clip: AudioClip, k: int, label: str) -> str:
    dur = clip_duration(clip)
    filters = [f"[{k}:a]asetpts=PTS-STARTPTS", _AFORMAT]
    if clip.loop_until > 0:
        # -stream_loop with -ss/-t only plays the slice once (-t caps all loops together),
        # so the slice is repeated with aloop (exact at 48 kHz) and cut to the loop end.
        samples = round((clip.out - clip.in_) * SAMPLE_RATE)
        filters.append(f"aloop=loop=-1:size={samples},atrim=duration={_f(dur)}")
    filters.append(f"volume={clip.volume / 100:.3f}")
    if clip.fade_in > 0:
        filters.append(f"afade=t=in:d={_f(clip.fade_in)}")
    if clip.fade_out > 0:
        filters.append(f"afade=t=out:st={_f(dur - clip.fade_out)}:d={_f(clip.fade_out)}")
    if clip.start < 0:
        filters.append(f"atrim=start={_f(-clip.start)},asetpts=PTS-STARTPTS")
    else:
        filters.append(f"adelay={round(clip.start * 1000)}:all=1")
    return ",".join(filters) + label


def _text_filter(project: Project, lead: float) -> str:
    """Hook for Task 6 (text overlays). Returns ``,filter…`` or nothing."""
    return ""


def build_render(
    project: Project,
    tmp: Path,
    *,
    size: tuple[int, int] | None = None,
    video: bool = True,
    audio: bool = True,
    lead: float = 0.0,
    duration: float | None = None,
) -> RenderPlan:
    w, h = _even_size(size) if size else output_size(project)  # yuv420p needs even sizes
    fps = output_fps(project)
    bg = _colour(project.settings.background)
    spans = layout(project)
    end = lead + duration if duration is not None else length(project)
    total = duration if duration is not None else length(project) - lead
    if total <= 0:
        raise ConversionError("The project is empty — add a clip, picture, text or music first.")
    main_end = spans[project.main[-1].id].end if project.main else 0.0

    inputs = _Inputs()
    graph: list[str] = []

    # Main row: one input per item, one chain per item, joined pairwise.
    vin: list[int | None] = []
    for item in project.main:
        source = project.source(item.source)
        if isinstance(item, PictureClip):
            if not video:
                vin.append(None)
                continue
            png = _picture_png(source, tmp, project.settings.background)
            dur = _f(item.duration)
            vin.append(inputs.add("-loop", "1", "-framerate", _f(fps), "-t", dur, "-i", png))
        elif video or (audio and _has_sound(project, item)):
            vin.append(
                inputs.add(
                    "-ss", _f(item.in_), "-t", _f(item.out - item.in_), "-i", str(source.path)
                )
            )
        else:
            vin.append(None)

    video_label = audio_label = None
    if video:
        if project.main:
            for i, item in enumerate(project.main):
                graph.append(_video_chain(item, vin[i], i, w, h, fps, bg))
            acc = "[v0]"
            for i, item in enumerate(project.main[1:], start=1):
                tr = item.transition_in
                if tr.kind != "none":
                    join = (
                        f"xfade=transition={tr.kind}:duration={_f(tr.duration)}"
                        f":offset={_f(spans[item.id].start)}"
                    )
                else:
                    join = "concat=n=2:v=1:a=0"
                graph.append(f"{acc}[v{i}]{join}[vj{i}]")
                acc = f"[vj{i}]"
            tail = []
            if end > main_end + 1e-6:
                tail.append(f"tpad=stop_mode=add:stop_duration={_f(end - main_end)}:color={bg}")
            graph.append(acc + ",".join(tail or ["null"]) + "[vmain]")
        else:
            graph.append(
                f"color=c={bg}:s={w}x{h}:r={_f(fps)}:d={_f(end)},format=yuv420p,settb=AVTB[vmain]"
            )
        final = []
        if lead > 0 or duration is not None:
            final.append(f"trim=start={_f(lead)}:duration={_f(total)},setpts=PTS-STARTPTS")
        chain = ",".join(final or ["null"]) + _text_filter(project, lead)
        graph.append(f"[vmain]{chain}[vout]")
        video_label = "[vout]"

    if audio:
        if project.main:
            for i, item in enumerate(project.main):
                k = vin[i] if _has_sound(project, item) else None
                graph.append(_audio_chain(item, k, i))
            acc = "[a0]"
            for i, item in enumerate(project.main[1:], start=1):
                tr = item.transition_in
                if tr.kind != "none":
                    join = f"acrossfade=d={_f(tr.duration)}:c1=tri:c2=tri"
                else:
                    join = "concat=n=2:v=0:a=1"
                graph.append(f"{acc}[a{i}]{join}[aj{i}]")
                acc = f"[aj{i}]"
            graph.append(f"{acc}apad=whole_dur={_f(end)}[amain]")
        else:
            graph.append(f"{_SILENCE},atrim=duration={_f(end)}[amain]")
        music = [c for row in project.music_rows for c in row]
        labels = []
        for j, clip in enumerate(music):
            source = project.source(clip.source)
            k = inputs.add(
                "-ss", _f(clip.in_), "-t", _f(clip.out - clip.in_), "-i", str(source.path)
            )
            graph.append(_music_chain(clip, k, f"[m{j}]"))
            labels.append(f"[m{j}]")
        mix = []
        if labels:
            mix.append(f"amix=inputs={len(labels) + 1}:normalize=0:duration=longest")
        # Always limited, so a preview window sounds like the full render. level=0: the
        # limiter's auto level would raise everything by 1/limit (~5 %).
        mix += ["alimiter=limit=0.95:level=0", f"atrim=duration={_f(end)}"]
        graph.append(f"[amain]{''.join(labels)}{','.join(mix)}[amix]")
        final = []
        if lead > 0 or duration is not None:
            final.append(f"atrim=start={_f(lead)}:duration={_f(total)},asetpts=PTS-STARTPTS")
        graph.append("[amix]" + ",".join(final or ["anull"]) + "[aout]")
        audio_label = "[aout]"

    # A file, not an argument: Windows limits a command line to 32,767 characters.
    text = ";\n".join(graph)
    args = [*inputs.args, "-/filter_complex", _write_graph(tmp, text)]
    return RenderPlan(args, video_label, audio_label, total, tmp, text)
