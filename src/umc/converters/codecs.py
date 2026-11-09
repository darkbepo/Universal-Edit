"""Codec ↔ FFmpeg encoder mapping and rate-control arguments.

We prefer the best software encoder, but fall back to whatever the bundled FFmpeg build
offers (e.g. LGPL builds have no libx264, so H.264 falls back to Media Foundation/NVENC/…).
"""

from __future__ import annotations

from umc.core.errors import ConversionError
from umc.tools import ffmpeg

VIDEO_ENCODERS: dict[str, list[str]] = {
    "h264": [
        "libx264",
        "libopenh264",
        "h264_mf",
        "h264_nvenc",
        "h264_qsv",
        "h264_amf",
        "h264_videotoolbox",
    ],
    "h265": ["libx265", "hevc_mf", "hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_videotoolbox"],
    "vp9": ["libvpx-vp9"],
    "vp8": ["libvpx"],
    "av1": ["libsvtav1", "libaom-av1", "av1_nvenc", "av1_qsv", "av1_amf"],
    "mpeg4": ["mpeg4"],
    "mpeg2": ["mpeg2video"],
    "wmv2": ["wmv2"],
    "theora": ["libtheora"],
    "prores": ["prores_ks", "prores"],
    "flv1": ["flv"],
}

AUDIO_ENCODERS: dict[str, list[str]] = {
    "aac": ["aac", "aac_mf"],
    "mp3": ["libmp3lame", "mp3_mf"],
    "opus": ["libopus", "opus"],
    "vorbis": ["libvorbis", "vorbis"],
    "flac": ["flac"],
    "pcm": ["pcm_s16le"],
    "pcm_be": ["pcm_s16be"],
    "wma": ["wmav2"],
    "ac3": ["ac3"],
    "mp2": ["mp2"],
}

VIDEO_CODEC_LABELS = {
    "h264": "H.264 (most compatible)",
    "h265": "H.265 / HEVC (smaller)",
    "vp9": "VP9",
    "vp8": "VP8",
    "av1": "AV1 (smallest, slow)",
    "mpeg4": "MPEG-4 Part 2",
    "mpeg2": "MPEG-2",
    "wmv2": "WMV",
    "theora": "Theora",
    "prores": "ProRes (editing)",
    "flv1": "FLV1",
}
AUDIO_CODEC_LABELS = {
    "aac": "AAC",
    "mp3": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "flac": "FLAC (lossless)",
    "pcm": "PCM (uncompressed)",
    "pcm_be": "PCM big-endian (AIFF)",
    "wma": "WMA",
    "ac3": "AC-3",
    "mp2": "MP2",
}

# container → (default video, default audio, allowed video, allowed audio)
CONTAINERS: dict[str, tuple[str, str, set[str], set[str]]] = {
    "mp4": (
        "h264",
        "aac",
        {"h264", "h265", "av1", "vp9", "mpeg4"},
        {"aac", "mp3", "opus", "ac3", "flac"},
    ),
    "m4v": ("h264", "aac", {"h264", "h265", "mpeg4"}, {"aac", "ac3"}),
    "mov": ("h264", "aac", {"h264", "h265", "prores", "mpeg4"}, {"aac", "pcm", "mp3", "ac3"}),
    "mkv": ("h264", "aac", set(VIDEO_ENCODERS) - {"flv1", "wmv2"}, set(AUDIO_ENCODERS) - {"wma"}),
    "webm": ("vp9", "opus", {"vp9", "vp8", "av1"}, {"opus", "vorbis"}),
    "avi": ("mpeg4", "mp3", {"mpeg4", "h264", "mpeg2"}, {"mp3", "ac3", "pcm", "mp2"}),
    "flv": ("h264", "aac", {"h264", "flv1"}, {"aac", "mp3"}),
    "wmv": ("wmv2", "wma", {"wmv2"}, {"wma"}),
    "mpg": ("mpeg2", "mp2", {"mpeg2"}, {"mp2", "ac3"}),
    "ts": ("h264", "aac", {"h264", "h265", "mpeg2"}, {"aac", "mp3", "ac3", "mp2"}),
    "3gp": ("h264", "aac", {"h264", "mpeg4"}, {"aac"}),
    "ogv": ("theora", "vorbis", {"theora"}, {"vorbis", "opus"}),
}

X264_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]


def resolve_video_codec(container: str, choice: str) -> str:
    default, _, allowed, _ = CONTAINERS[container]
    codec = default if choice == "auto" else choice
    if codec not in allowed:
        raise ConversionError(
            f"{VIDEO_CODEC_LABELS.get(codec, codec)} can't be saved as .{container}. "
            f"Choose “Auto” or one of: {', '.join(VIDEO_CODEC_LABELS[c] for c in sorted(allowed))}."
        )
    return codec


def resolve_audio_codec(container: str, choice: str) -> str:
    _, default, _, allowed = CONTAINERS[container]
    codec = default if choice == "auto" else choice
    if codec not in allowed:
        raise ConversionError(
            f"{AUDIO_CODEC_LABELS.get(codec, codec)} audio can't be saved in .{container}. "
            "Choose “Auto”."
        )
    return codec


def video_encoder(codec: str) -> str:
    enc = ffmpeg.pick_encoder(VIDEO_ENCODERS[codec])
    if enc is None:
        raise ConversionError(
            f"This copy of FFmpeg has no encoder for {VIDEO_CODEC_LABELS.get(codec, codec)}. "
            "Choose another codec."
        )
    return enc


def audio_encoder(codec: str) -> str:
    enc = ffmpeg.pick_encoder(AUDIO_ENCODERS[codec])
    if enc is None:
        raise ConversionError(
            f"This copy of FFmpeg can't encode {AUDIO_CODEC_LABELS[codec]} audio."
        )
    return enc


def video_quality_args(encoder: str, crf: int, preset: str, bitrate_kbps: int | None) -> list[str]:
    """Encoder-specific rate control. ``crf`` is on the x264 scale (0 best … 51 worst)."""
    if bitrate_kbps:
        args = ["-b:v", f"{bitrate_kbps}k"]
        if encoder in ("libx264", "libx265"):
            args += ["-preset", preset]
        return args
    if encoder in ("libx264", "libx265"):
        args = ["-crf", str(crf), "-preset", preset]
        if encoder == "libx265":
            args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]
        return args
    if encoder == "libvpx-vp9":
        return [
            "-crf",
            str(min(63, crf + 8)),
            "-b:v",
            "0",
            "-row-mt",
            "1",
            "-deadline",
            "good",
            "-cpu-used",
            _speed(preset, 0, 5),
        ]
    if encoder == "libvpx":
        return ["-crf", str(min(63, max(4, crf + 8))), "-b:v", "10M", "-cpu-used", "4"]
    if encoder == "libsvtav1":
        return ["-crf", str(min(63, crf + 12)), "-preset", _speed(preset, 4, 12)]
    if encoder == "libaom-av1":
        return [
            "-crf",
            str(min(63, crf + 12)),
            "-b:v",
            "0",
            "-cpu-used",
            _speed(preset, 3, 8),
            "-row-mt",
            "1",
        ]
    if encoder.endswith("_nvenc"):
        return ["-rc", "vbr", "-cq", str(crf), "-b:v", "0", "-preset", "p5"]
    if encoder.endswith("_qsv"):
        return ["-global_quality", str(crf)]
    if encoder.endswith("_amf"):
        return ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    if encoder.endswith("_videotoolbox"):
        return ["-q:v", str(max(1, min(100, 100 - crf * 2)))]
    if encoder.endswith("_mf") or encoder == "libopenh264":
        # No constant-quality mode: approximate from CRF with a bitrate.
        kbps = int(20000 * (0.85**crf))
        return ["-b:v", f"{max(300, kbps)}k"]
    if encoder in ("mpeg4", "mpeg2video", "wmv2", "flv", "libtheora"):
        q = 2 + round(crf * 29 / 51)
        return ["-q:v", str(q)]
    if encoder.startswith("prores"):
        return ["-profile:v", "3"]
    return []


def _speed(preset: str, slow: int, fast: int) -> str:
    """Map an x264 preset name onto an encoder's numeric speed range."""
    idx = X264_PRESETS.index(preset) if preset in X264_PRESETS else 5
    # ultrafast (0) → fast end, veryslow (8) → slow end
    return str(round(fast - (fast - slow) * idx / (len(X264_PRESETS) - 1)))


def pixel_format_args(encoder: str) -> list[str]:
    if encoder.startswith("prores"):
        return ["-pix_fmt", "yuv422p10le"]
    if encoder.endswith(("_nvenc", "_qsv", "_amf", "_mf", "_videotoolbox")):
        return (
            ["-pix_fmt", "nv12"] if encoder.endswith(("_qsv", "_mf")) else ["-pix_fmt", "yuv420p"]
        )
    return ["-pix_fmt", "yuv420p"]
