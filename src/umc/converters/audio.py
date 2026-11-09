"""Audio ↔ audio, and video → audio only (extract the soundtrack)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from umc.converters import codecs, sizing
from umc.converters.base import Converter
from umc.converters.video import trim_args
from umc.core.detect import MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ffmpeg

# target ext → (label, codec)
AUDIO_TARGETS = {
    "mp3": ("MP3", "mp3"),
    "m4a": ("M4A (AAC)", "aac"),
    "wav": ("WAV", "pcm"),
    "flac": ("FLAC", "flac"),
    "ogg": ("OGG", "vorbis"),
    "opus": ("OPUS", "opus"),
    "aiff": ("AIFF", "pcm_be"),
    "wma": ("WMA", "wma"),
    "ac3": ("AC-3", "ac3"),
}
LOSSY_EXTS = ("mp3", "m4a", "ogg", "opus", "wma", "ac3")
# Option targets: video → audio ids ("audio:mp3") and audio → audio ids ("mp3").
LOSSY = tuple(f"audio:{k}" for k in LOSSY_EXTS) + LOSSY_EXTS


@dataclass
class AudioOptions:
    bitrate: int = opt(
        192,
        "Bitrate",
        targets=LOSSY,
        choices=[(b, f"{b} kbps") for b in (64, 96, 128, 160, 192, 256, 320)],
    )
    target_size_mb: float = opt(
        0.0,
        "Target file size",
        kind="float",
        min=0,
        max=100000,
        decimals=1,
        unit="MB",
        targets=LOSSY,
        help="0 = off. Picks the bitrate that lands just under this size (overrides Bitrate).",
    )
    sample_rate: object = opt(
        "original",
        "Sample rate",
        choices=[
            ("original", "Original"),
            (22050, "22.05 kHz"),
            (44100, "44.1 kHz"),
            (48000, "48 kHz"),
            (96000, "96 kHz"),
        ],
    )
    channels: object = opt(
        "original", "Channels", choices=[("original", "Original"), (1, "Mono"), (2, "Stereo")]
    )
    volume: int = opt(100, "Volume", kind="slider", min=0, max=300, unit="%")
    normalize: bool = opt(False, "Normalise loudness", help="EBU R128 loudness normalisation.")
    start: str = opt("", "Start at", help="Seconds or mm:ss. Empty = beginning.")
    end: str = opt("", "End at", help="Seconds or mm:ss. Empty = until the end.")


class _AudioConverter(Converter):
    options_cls = AudioOptions
    verb = "Converting"

    def run(self, job: ConversionJob) -> list[Path]:
        o: AudioOptions = job.options
        ext = job.target.ext
        codec = AUDIO_TARGETS[ext][1]
        enc = codecs.audio_encoder(codec)
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            msg = f"{self.verb} {f.name} ({i + 1}/{n})"
            prog = job.sub_progress(i / n, (i + 1) / n, msg)
            info = ffmpeg.probe(f.path)
            if not info.has_audio:
                raise ConversionError(f"“{f.name}” has no audio track.")
            seek, duration = trim_args(o.start, o.end, info.duration)
            # -vn also drops embedded cover art, which most audio containers can't hold.
            args = [*seek, "-i", str(f.path), "-vn", "-sn", "-map", "0:a:0", "-c:a", enc]
            args += ["-map_metadata", "0"]
            if ext in LOSSY_EXTS:
                kbps = o.bitrate
                if o.target_size_mb > 0:
                    kbps = sizing.audio_kbps_for(o.target_size_mb, duration)
                args += ["-b:a", f"{kbps}k"]
            if o.sample_rate != "original":
                args += ["-ar", str(o.sample_rate)]
            if o.channels != "original":
                args += ["-ac", str(o.channels)]
            af = []
            if o.normalize:
                af.append("loudnorm")
            if o.volume != 100:
                af.append(f"volume={o.volume / 100:.2f}")
            if af:
                args += ["-af", ",".join(af)]
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, ext)
            args.append(str(dest))
            try:
                ffmpeg.run_ffmpeg(
                    args,
                    total_seconds=duration,
                    progress=prog,
                    cancel_event=job.cancel_event,
                    error_message=f"Couldn't convert the audio of “{f.name}”.",
                )
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs


class AudioExtractConverter(_AudioConverter):
    """Video → audio only."""

    family = MediaFamily.VIDEO
    verb = "Extracting audio from"

    def targets(self, files):
        if not ffmpeg.all_have([f.path for f in files], lambda s: s.has_audio):
            return []  # a silent video: nothing to extract
        return [
            self.target(f"audio:{ext}", label, ext, "Audio only", "Extract the soundtrack")
            for ext, (label, _) in AUDIO_TARGETS.items()
        ]


class AudioConverter(_AudioConverter):
    """Audio ↔ audio."""

    family = MediaFamily.AUDIO

    def targets(self, files):
        return [self.target(ext, label, ext, "Audio") for ext, (label, _) in AUDIO_TARGETS.items()]
