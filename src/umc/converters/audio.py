"""Video → audio only (extract the soundtrack)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from umc.converters import codecs
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
}
LOSSY = tuple(f"audio:{k}" for k in ("mp3", "m4a", "ogg", "opus"))


@dataclass
class AudioOptions:
    bitrate: int = opt(
        192,
        "Bitrate",
        targets=LOSSY,
        choices=[(b, f"{b} kbps") for b in (64, 96, 128, 160, 192, 256, 320)],
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


class AudioExtractConverter(Converter):
    family = MediaFamily.VIDEO
    options_cls = AudioOptions

    def targets(self, files):
        return [
            self.target(f"audio:{ext}", label, ext, "Audio only", "Extract the soundtrack")
            for ext, (label, _) in AUDIO_TARGETS.items()
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: AudioOptions = job.options
        ext = job.target.ext
        codec = AUDIO_TARGETS[ext][1]
        enc = codecs.audio_encoder(codec)
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            msg = f"Extracting audio from {f.name} ({i + 1}/{n})"
            prog = job.sub_progress(i / n, (i + 1) / n, msg)
            info = ffmpeg.probe(f.path)
            if not info.has_audio:
                raise ConversionError(f"“{f.name}” has no audio track.")
            seek, duration = trim_args(o.start, o.end, info.duration)
            args = [*seek, "-i", str(f.path), "-vn", "-sn", "-map", "0:a:0", "-c:a", enc]
            if f"audio:{ext}" in LOSSY:
                args += ["-b:a", f"{o.bitrate}k"]
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
                    error_message=f"Couldn't extract audio from “{f.name}”.",
                )
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
