"""Audio editor projects: the tracks saved separately (port: web/src/audioedit/project.ts).

A ``.umcaudio`` file is a ZIP: ``project.json`` (format, version, sample rate, the chosen track
and each track's name, volume, left / right, mute, solo, start and file) and one 32-bit float
WAV per track in ``tracks/`` — lossless, so reopening gives the same samples. Both apps read
and write the same layout; unknown keys are ignored, a newer ``version`` is refused.
"""

from __future__ import annotations

import io
import json
import zipfile

from umc.audioedit import tracks as tr
from umc.audioedit.document import AudioDoc
from umc.audioedit.wav import read_wav, write_wav
from umc.core.errors import ConversionError

EXT = "umcaudio"
FORMAT = "universal-edit-audio-project"
VERSION = 1
MAX_TRACKS = 64
MAX_JSON = 1_000_000


class ProjectError(ConversionError):
    """Why a project can't be opened (shown as it is)."""


def to_bytes(tracks: list[tr.Track], active: int = 0) -> bytes:
    if not tracks:
        raise ProjectError("There are no tracks to save.")
    rate = tracks[0].doc.rate
    entries = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, t in enumerate(tracks):
            name = f"tracks/{i + 1}.wav"
            z.writestr(name, write_wav(rate, t.doc.samples, 32))
            entries.append(
                {
                    "name": t.name,
                    "file": name,
                    "gain_db": t.gain_db,
                    "pan": t.pan,
                    "mute": t.mute,
                    "solo": t.solo,
                    "offset": t.offset,
                }
            )
        meta = {
            "format": FORMAT,
            "version": VERSION,
            "rate": rate,
            "active": max(0, min(active, len(tracks) - 1)),
            "tracks": entries,
        }
        z.writestr("project.json", json.dumps(meta, indent=1, ensure_ascii=False))
    return buf.getvalue()


def _num(v: object, lo: float, hi: float, default: float) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or v != v:
        return default
    return max(lo, min(hi, float(v)))


def from_bytes(data: bytes) -> tuple[list[tr.Track], int]:
    """The tracks and the chosen one. Raises ProjectError in plain words."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        info = z.getinfo("project.json")
        if info.file_size > MAX_JSON:
            raise ProjectError("This project's description is too big.")
        meta = json.loads(z.read(info).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProjectError("This isn't an audio editor project (or it is damaged).") from e
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise ProjectError("This isn't an audio editor project.")
    if not isinstance(meta.get("version"), int) or meta["version"] > VERSION:
        raise ProjectError("This project was made by a newer version of Universal Edit.")
    entries = meta.get("tracks")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_TRACKS:
        raise ProjectError("This project has no tracks (or too many).")
    sizes = sum(
        z.getinfo(e["file"]).file_size
        for e in entries
        if isinstance(e, dict) and e.get("file") in z.namelist()
    )
    if sizes > _max_samples() * 4 + len(entries) * 1024:  # float32 WAVs: checked before unpacking
        raise ProjectError("This project is too long for the editor.")
    rate = None
    out: list[tr.Track] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not isinstance(e.get("file"), str):
            raise ProjectError("This project's track list is damaged.")
        try:
            wav_rate, samples = read_wav(z.read(e["file"]))
        except KeyError as ex:
            raise ProjectError(f"Track {i + 1}'s sound is missing from the project.") from ex
        rate = wav_rate if rate is None else rate
        if wav_rate != rate:
            raise ProjectError("The project's tracks have different sample rates.")
        name = (
            e.get("name")
            if isinstance(e.get("name"), str) and e["name"].strip()
            else f"Track {i + 1}"
        )
        out.append(
            tr.Track(
                AudioDoc(wav_rate, samples),
                name,
                gain_db=_num(e.get("gain_db"), tr.MIN_GAIN_DB, tr.MAX_GAIN_DB, 0.0),
                pan=_num(e.get("pan"), -1.0, 1.0, 0.0),
                mute=e.get("mute") is True,
                solo=e.get("solo") is True,
                offset=int(_num(e.get("offset"), 0, 2**40, 0)),
            )
        )
    if sum(t.doc.samples.size for t in out) > _max_samples():
        raise ProjectError("This project is too long for the editor.")
    active = meta.get("active")
    active = active if isinstance(active, int) and 0 <= active < len(out) else 0
    return out, active


def _max_samples() -> int:
    from umc.audioedit.io import MAX_SAMPLES

    return MAX_SAMPLES
