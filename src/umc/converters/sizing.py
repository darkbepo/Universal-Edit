"""Helpers for "make it fit under N MB"."""

from __future__ import annotations

from umc.core.errors import ConversionError

MB = 1024 * 1024
# Encoders whose FFmpeg wrapper supports -pass 1/2 with -passlogfile.
TWO_PASS_ENCODERS = {"libx264", "libvpx", "libvpx-vp9"}
CONTAINER_OVERHEAD = 0.97  # leave ~3 % for container/muxing overhead


def video_kbps_for(target_mb: float, seconds: float, audio_kbps: float) -> int:
    """Video bitrate that makes ``seconds`` of video (+ audio) land just under ``target_mb``."""
    if seconds <= 0:
        raise ConversionError("Couldn't read the video's length, so the target size can't be used.")
    total_kbps = target_mb * MB * 8 / 1000 / seconds * CONTAINER_OVERHEAD
    video_kbps = int(total_kbps - audio_kbps)
    if video_kbps < 50:
        need = (50 + audio_kbps) * 1000 * seconds / 8 / MB / CONTAINER_OVERHEAD
        raise ConversionError(
            f"{target_mb:g} MB is too small for {seconds:.0f} seconds of video — it needs at least "
            f"about {need:.1f} MB. Trim it, remove the audio, or choose a bigger size."
        )
    return video_kbps


def audio_kbps_for(target_mb: float, seconds: float) -> int:
    if seconds <= 0:
        raise ConversionError("Couldn't read the audio's length, so the target size can't be used.")
    kbps = int(target_mb * MB * 8 / 1000 / seconds * CONTAINER_OVERHEAD)
    if kbps < 24:
        raise ConversionError(
            f"{target_mb:g} MB is too small for {seconds:.0f} seconds of audio. "
            "Trim it or choose a bigger size."
        )
    return min(kbps, 320)
