"""WAV read / write for the audio editor (port: web/src/audioedit/wav.ts).

Reads PCM 8/16/24/32-bit, float 32/64 and WAVE_FORMAT_EXTENSIBLE; writes 32-bit float (what the
editor hands to FFmpeg) or 16-bit PCM. Samples are planar float32, shape (channels, frames)."""

from __future__ import annotations

import struct

import numpy as np

from umc.core.errors import ConversionError

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE


def _bad(why: str) -> ConversionError:
    return ConversionError("This isn't a WAV file the editor can read.", details=why)


def read_wav(data: bytes) -> tuple[int, np.ndarray]:
    """(sample rate, float32 samples of shape (channels, frames))."""
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise _bad("no RIFF/WAVE header")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start = pos + 8
        if cid == b"fmt ":
            fmt = data[start : start + size]
        elif cid == b"data":
            if size == 0xFFFFFFFF or start + size > len(data):  # streamed / RF64: up to the end
                size = len(data) - start
            body = data[start : start + size]
            break
        pos = start + size + (size & 1)
    if fmt is None or body is None or len(fmt) < 16:
        raise _bad("missing fmt or data chunk")
    tag, channels, rate = struct.unpack_from("<HHI", fmt, 0)
    bits = struct.unpack_from("<H", fmt, 14)[0]
    if tag == EXTENSIBLE and len(fmt) >= 26:
        tag = struct.unpack_from("<H", fmt, 24)[0]  # first 2 bytes of the sub-format GUID
    if channels < 1 or rate < 1:
        raise _bad("no channels")
    width = bits // 8
    frames = len(body) // (width * channels) if width else 0
    raw = body[: frames * width * channels]
    if tag == FLOAT and bits in (32, 64):
        x = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif tag == PCM and bits == 8:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == PCM and bits == 16:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif tag == PCM and bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    elif tag == PCM and bits == 32:
        x = (np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0).astype(np.float32)
    else:
        raise _bad(f"format {tag}, {bits} bits")
    return rate, np.ascontiguousarray(x.reshape(frames, channels).T)


def write_wav(rate: int, samples: np.ndarray, bits: int = 32) -> bytes:
    """WAV bytes: 32 = float, 16 = PCM (clipped to -1…1)."""
    channels, frames = samples.shape
    if bits == 32:
        tag, body = FLOAT, np.ascontiguousarray(samples.T, dtype="<f4").tobytes()
    elif bits == 16:
        v = np.clip(np.round(samples.T * 32767.0), -32768, 32767).astype("<i2")
        tag, body = PCM, np.ascontiguousarray(v).tobytes()
    else:
        raise ValueError(bits)
    width = bits // 8
    fmt = struct.pack(
        "<HHIIHH", tag, channels, rate, rate * channels * width, channels * width, bits
    )
    head = b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(body)) + b"WAVE"
    return (
        head
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(body))
        + body
    )
