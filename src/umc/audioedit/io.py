"""Open audio files for the editor and save the result.

Opening: FFmpeg decodes each file to 32-bit float WAV (mono stays mono, anything wider becomes
stereo); several files are joined end to end at the first one's sample rate and channels.
Saving: a float WAV goes through the audio converter, so every audio format and its encoder
choice keep working."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from umc.audioedit import ops
from umc.audioedit.document import AudioDoc
from umc.audioedit.wav import read_wav, write_wav
from umc.converters.audio import AUDIO_TARGETS, LOSSY_EXTS, AudioConverter, AudioOptions
from umc.core import options as opts
from umc.core.detect import detect
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob
from umc.tools import ffmpeg

# ~45 minutes of 44.1 kHz stereo (float32: ~960 MB). Longer files are refused, not swapped.
MAX_SAMPLES = 240_000_000
SAVE_FORMATS: list[tuple[str, str]] = [(ext, label) for ext, (label, _c) in AUDIO_TARGETS.items()]
BITRATES = (64, 96, 128, 160, 192, 256, 320)
DEFAULT_BITRATE = 192

Progress = Callable[[float, str], None]


def _noop(_f: float, _m: str) -> None:
    pass


def decode(
    path: Path,
    rate: int | None = None,
    channels: int | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[float], None] = lambda _f: None,
) -> tuple[int, np.ndarray]:
    """(rate, samples) of ``path``'s first audio track (also a video's soundtrack)."""
    path = Path(path)
    info = ffmpeg.probe(path)
    if not info.has_audio:
        raise ConversionError(f"“{path.name}” has no sound.")
    args = ["-i", str(path), "-vn", "-sn", "-dn", "-map", "0:a:0", "-c:a", "pcm_f32le"]
    if rate:
        args += ["-ar", str(rate)]
    if channels:
        args += ["-ac", str(channels)]
    else:
        args += ["-af", "aformat=channel_layouts=mono|stereo"]
    with tempfile.TemporaryDirectory(prefix="umc-audioedit-") as tmp:
        out = Path(tmp) / "decoded.wav"
        ffmpeg.run_ffmpeg(
            [*args, "-f", "wav", str(out)],
            total_seconds=info.duration,
            progress=progress,
            cancel_event=cancel_event or threading.Event(),
            error_message=f"Couldn't read the sound in “{path.name}”.",
        )
        if out.stat().st_size > MAX_SAMPLES * 4 + 4096:
            raise too_long()
        return read_wav(out.read_bytes())


def too_long() -> ConversionError:
    return ConversionError(
        "This is too long for the audio editor (about 45 minutes of stereo sound at most). "
        "Trim it in Convert first (Start at / End at)."
    )


def open_audio(
    paths: Sequence[Path],
    cancel_event: threading.Event | None = None,
    progress: Progress = _noop,
) -> AudioDoc:
    """One document from ``paths``, joined in order."""
    paths = [Path(p) for p in paths]
    if not paths:
        raise ConversionError("No audio files to open.")
    parts: list[np.ndarray] = []
    rate = channels = None
    total = 0
    n = len(paths)
    for i, p in enumerate(paths):
        msg = f"Opening “{p.name}”…"

        def prog(f: float, i: int = i, msg: str = msg) -> None:
            progress((i + max(0.0, min(1.0, f))) / n, msg)

        prog(0)
        r, x = decode(p, rate, channels, cancel_event, prog)
        rate, channels = rate or r, channels or x.shape[0]
        total += x.size
        if total > MAX_SAMPLES:
            raise too_long()
        parts.append(ops.fit_channels(x, channels))
    assert rate is not None
    return AudioDoc(rate, ops.join(parts) if len(parts) > 1 else parts[0])


def save_audio(
    doc: AudioDoc,
    dest: Path,
    fmt: str,
    bitrate: int = DEFAULT_BITRATE,
    on_progress: Progress | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Write the sound to ``dest`` as ``fmt`` (replacing it — the user already confirmed).
    ``dest`` is left untouched if anything fails."""
    dest = Path(dest)
    if fmt not in AUDIO_TARGETS:
        raise ConversionError(f"Can't save as {fmt.upper()}.")
    if doc.frames == 0:
        raise ConversionError("There is no sound to save.")
    samples = np.clip(doc.samples, -1.0, 1.0)
    # The temp folder sits next to dest: os.replace can't move files across drives on Windows.
    with tempfile.TemporaryDirectory(prefix=".umc-audio-", dir=dest.parent) as tmp:
        src = Path(tmp) / f"{dest.stem or 'audio'}.wav"
        src.write_bytes(write_wav(doc.rate, samples, 32))
        converter = AudioConverter()
        files = [detect(src)]
        target = next(t for t in converter.targets(files) if t.id == fmt)
        values = opts.defaults(AudioOptions)
        if fmt in LOSSY_EXTS:
            values["bitrate"] = bitrate
        job = ConversionJob(
            files=files,
            target=target,
            options=opts.build(AudioOptions, values),
            output_dir=Path(tmp) / "out",
        )
        if on_progress is not None:
            job.on_progress = on_progress
        if cancel_event is not None:
            job.cancel_event = cancel_event
        try:
            [out] = converter.run(job)
            os.replace(out, dest)
        except OSError as exc:
            raise ConversionError(
                f"Couldn't write “{dest.name}”. Is it open in another program?", details=repr(exc)
            ) from exc
    return dest
