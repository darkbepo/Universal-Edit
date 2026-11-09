"""Subtitles: SRT/VTT/ASS/TTML/LRC ↔ each other, extract from video, burn into / add to video.

Everything goes through FFmpeg. Inputs are first normalised to UTF-8 (auto-detected, with a
chosen fallback for old files), then shifted in time if asked. Burning uses libass with the
bundled DejaVu Sans font so it looks the same on every computer.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import ffmpeg

OUT_FORMATS = {"srt": "SRT", "vtt": "VTT", "ass": "ASS", "ttml": "TTML", "lrc": "LRC"}
MUXER = {"srt": "srt", "vtt": "webvtt", "ass": "ass", "ttml": "ttml", "lrc": "lrc"}
EXTRACT = ("srt", "vtt", "ass")
CHARSETS = [
    ("windows-1252", "Western European (Windows-1252)"),
    ("iso-8859-1", "Latin-1 (ISO-8859-1)"),
    ("windows-1250", "Central European (Windows-1250)"),
    ("windows-1251", "Cyrillic (Windows-1251)"),
    ("windows-1253", "Greek (Windows-1253)"),
    ("windows-1254", "Turkish (Windows-1254)"),
    ("windows-1256", "Arabic (Windows-1256)"),
    ("shift_jis", "Japanese (Shift-JIS)"),
    ("gb18030", "Chinese Simplified (GB18030)"),
    ("big5", "Chinese Traditional (Big5)"),
    ("euc-kr", "Korean (EUC-KR)"),
]
POSITIONS = {"bottom": 2, "top": 8, "middle": 5}
STYLE_TARGETS = ("ass", "burn:mp4")
# ISO 639-1 → ISO 639-2/B (what Matroska expects; MP4 players accept it too)
ISO639_2 = {
    "da": "dan", "en": "eng", "de": "ger", "fr": "fre", "es": "spa", "it": "ita", "pt": "por",
    "nl": "dut", "sv": "swe", "no": "nor", "nb": "nob", "nn": "nno", "fi": "fin", "is": "ice",
    "pl": "pol", "cs": "cze", "sk": "slo", "hu": "hun", "ro": "rum", "el": "gre", "tr": "tur",
    "ru": "rus", "uk": "ukr", "ar": "ara", "he": "heb", "hi": "hin", "fa": "per", "zh": "chi",
    "ja": "jpn", "ko": "kor", "vi": "vie", "th": "tha", "id": "ind", "hr": "hrv", "sr": "srp",
    "bg": "bul", "et": "est", "lv": "lav", "lt": "lit", "sl": "slv", "ca": "cat", "eu": "baq",
}  # fmt: skip
LANG_RE = re.compile(r"\.([a-z]{2,3})(?:[._-](?:forced|sdh|cc|hi))?$", re.I)


@dataclass
class SubtitleOptions:
    shift: float = opt(
        0.0,
        "Shift timing",
        kind="float",
        min=-3600,
        max=3600,
        decimals=2,
        unit="s",
        help="Positive = subtitles appear later, negative = earlier.",
    )
    charset: str = opt(
        "windows-1252",
        "Text encoding of old files",
        choices=CHARSETS,
        help="UTF-8 is detected automatically; this is used only for files that aren't UTF-8.",
    )
    font_size: int = opt(
        48,
        "Font size",
        kind="slider",
        min=12,
        max=120,
        unit="px",
        targets=STYLE_TARGETS,
        help="At 1080p height; scales with the video.",
    )
    colour: str = opt("#ffffff", "Text colour", kind="color", targets=STYLE_TARGETS)
    outline_colour: str = opt("#000000", "Outline colour", kind="color", targets=STYLE_TARGETS)
    outline: float = opt(
        2.5, "Outline thickness", kind="float", min=0, max=10, decimals=1, targets=STYLE_TARGETS
    )
    position: str = opt(
        "bottom",
        "Position",
        choices=[("bottom", "Bottom"), ("top", "Top"), ("middle", "Middle")],
        targets=STYLE_TARGETS,
    )
    margin: int = opt(
        40,
        "Distance from the edge",
        kind="slider",
        min=0,
        max=300,
        unit="px",
        targets=STYLE_TARGETS,
    )
    restyle_ass: bool = opt(
        False,
        "Also restyle ASS files",
        targets=STYLE_TARGETS,
        help="ASS/SSA subtitles bring their own look; tick to use the settings above instead.",
    )
    crf: int = opt(
        20,
        "Video quality (CRF, lower = better)",
        kind="slider",
        min=0,
        max=40,
        targets=("burn:mp4",),
    )
    language: str = opt(
        "auto",
        "Subtitle language",
        targets=("subtrack:mkv", "subtrack:mp4"),
        help="3-letter code like eng, dan, deu. Auto = from the file name (movie.da.srt).",
    )
    default_track: bool = opt(
        True, "Show the first subtitle track by default", targets=("subtrack:mkv", "subtrack:mp4")
    )


# --------------------------------------------------------------------------- helpers


def decode(data: bytes, fallback: str) -> str:
    """Subtitle bytes → text: BOMs, UTF-8, then the chosen legacy charset."""
    for bom, enc in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom) :].decode(enc, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback, errors="replace")


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def restyle(ass_text: str, o: SubtitleOptions, play_res_y: int = 1080) -> str:
    """Set font, size, colours, outline, position of the ``Default`` style (and the PlayResY)."""
    lines = ass_text.splitlines()
    fmt: list[str] = []
    in_styles = False
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if low.startswith("["):
            in_styles = low in ("[v4+ styles]", "[v4 styles]")
        elif low.startswith("playresy:"):
            lines[i] = f"PlayResY: {play_res_y}"
        elif in_styles and low.startswith("format:"):
            fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
        elif in_styles and low.startswith("style:") and fmt:
            values = [v.strip() for v in line.split(":", 1)[1].split(",")]
            if len(values) != len(fmt) or (values[0].lower() != "default" and not o.restyle_ass):
                continue
            new = {
                "fontname": "DejaVu Sans",
                "fontsize": str(o.font_size),
                "primarycolour": _ass_colour(o.colour),
                "outlinecolour": _ass_colour(o.outline_colour),
                "backcolour": _ass_colour(o.outline_colour, 0x80),
                "outline": f"{o.outline:g}",
                "shadow": "0",
                "borderstyle": "1",
                "alignment": str(POSITIONS[o.position]),
                "marginv": str(o.margin),
            }
            values = [new.get(name, v) for name, v in zip(fmt, values, strict=True)]
            lines[i] = "Style: " + ",".join(values)
    text = "\n".join(lines) + "\n"
    if not re.search(r"(?im)^PlayResY:", text):
        text = re.sub(
            r"(?im)^\[Script Info\][^\n]*\n",
            lambda m: m.group(0) + f"PlayResY: {play_res_y}\nPlayResX: {play_res_y * 16 // 9}\n",
            text,
            count=1,
        )
    return text


def fonts_dir() -> Path | None:
    base = (
        Path(getattr(sys, "_MEIPASS", ""))
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[3] / "packaging"
    )
    d = base / "fonts"
    return d if (d / "DejaVuSans.ttf").is_file() else None


def _normalise(f: DetectedFile, tmp: Path, o: SubtitleOptions) -> Path:
    """UTF-8 copy of a subtitle file with the right extension for FFmpeg's probe."""
    text = decode(f.path.read_bytes(), o.charset)
    out = tmp / f"in-{abs(hash(f.path))}.{f.ext}"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def _convert_sub(
    src: Path, dest: Path, fmt: str, o: SubtitleOptions, job: ConversionJob, name: str
) -> None:
    args = []
    if o.shift:
        args += ["-itsoffset", f"{o.shift:.3f}"]
    args += [
        "-i",
        str(src),
        "-map",
        "0:s:0",
        "-c:s",
        {"vtt": "webvtt", "srt": "subrip", "ttml": "ttml", "lrc": "text", "ass": "ass"}[fmt],
        "-f",
        MUXER[fmt],
        str(dest),
    ]
    if fmt == "lrc":
        args[args.index("-c:s") : args.index("-c:s") + 2] = []
    ffmpeg.run_ffmpeg(
        args,
        total_seconds=0,
        progress=lambda _f: None,
        cancel_event=job.cancel_event,
        error_message=f"Couldn't convert “{name}”.",
    )
    if not dest.exists() or not dest.stat().st_size:
        dest.unlink(missing_ok=True)
        raise ConversionError(f"“{name}” doesn't contain any subtitles FFmpeg can read.")


def _to_ass(
    sub: DetectedFile, tmp: Path, o: SubtitleOptions, job: ConversionJob, styled: bool
) -> Path:
    src = _normalise(sub, tmp, o)
    ass = tmp / "subs.ass"
    _convert_sub(src, ass, "ass", o, job, sub.name)
    if styled and (sub.ext not in ("ass", "ssa") or o.restyle_ass):
        ass.write_text(restyle(ass.read_text(encoding="utf-8"), o), encoding="utf-8")
    return ass


def guess_language(path: Path) -> str:
    m = LANG_RE.search(path.stem)
    if not m:
        return "und"
    code = m.group(1).lower()
    return ISO639_2.get(code, code) if len(code) == 2 else code


# --------------------------------------------------------------------------- converters


class SubtitleConverter(Converter):
    family = MediaFamily.SUBTITLE
    options_cls = SubtitleOptions

    def targets(self, files):
        single = files[0].ext if len(files) == 1 else ""
        return [
            self.target(ext, label, ext, "Subtitles")
            for ext, label in OUT_FORMATS.items()
            if ext != single
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: SubtitleOptions = job.options
        fmt = job.target.ext
        outputs = []
        with tempfile.TemporaryDirectory(prefix="umc-subs-") as tmp:
            for i, f in enumerate(job.files):
                job.check_cancelled()
                job.report(i / len(job.files), f"Converting {f.name}")
                src = _normalise(f, Path(tmp), o)
                dest = unique_path(job.output_dir_for(f.path), f.path.stem, fmt)
                if fmt == "ass":
                    staged = Path(tmp) / "out.ass"
                    _convert_sub(src, staged, "ass", o, job, f.name)
                    text = staged.read_text(encoding="utf-8")
                    if f.ext not in ("ass", "ssa") or o.restyle_ass:
                        text = restyle(text, o)
                    dest.write_text(text, encoding="utf-8")
                else:
                    _convert_sub(src, dest, fmt, o, job, f.name)
                outputs.append(dest)
        job.report(1.0, "Done")
        return outputs


class SubtitleExtractConverter(Converter):
    """Video → its text subtitle tracks, one file per track."""

    family = MediaFamily.VIDEO
    options_cls = SubtitleOptions

    def targets(self, files):
        return [
            self.target(
                f"subs:{e}",
                OUT_FORMATS[e],
                e,
                "Subtitles",
                "Save the subtitle tracks inside the video",
            )
            for e in EXTRACT
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: SubtitleOptions = job.options
        fmt = job.target.ext
        outputs = []
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / len(job.files), f"Reading subtitle tracks of {f.name}")
            streams = ffmpeg.subtitle_streams(f.path)
            text = [s for s in streams if s.is_text]
            if not text:
                raise ConversionError(
                    f"“{f.name}” has no text subtitles."
                    + (
                        " Its subtitles are pictures (Blu-ray/DVD style),"
                        " which can't be turned into text."
                        if streams
                        else ""
                    )
                )
            for s in text:
                tag = s.language or f"track{s.index + 1}"
                dest = unique_path(job.output_dir_for(f.path), f"{f.path.stem}.{tag}", fmt)
                args = []
                if o.shift:
                    args += ["-itsoffset", f"{o.shift:.3f}"]
                args += [
                    "-i",
                    str(f.path),
                    "-map",
                    f"0:s:{s.index}",
                    "-c:s",
                    {"vtt": "webvtt", "srt": "subrip", "ass": "ass"}[fmt],
                    "-f",
                    MUXER[fmt],
                    str(dest),
                ]
                ffmpeg.run_ffmpeg(
                    args,
                    total_seconds=0,
                    progress=lambda _f: None,
                    cancel_event=job.cancel_event,
                    error_message=f"Couldn't extract subtitles from “{f.name}”.",
                )
                outputs.append(dest)
        job.report(1.0, "Done")
        return outputs


class SubtitleVideoConverter(Converter):
    """One video + subtitle file(s): burn them in, or add them as selectable tracks."""

    family = MediaFamily.VIDEO  # unused: offered via any_family for the mixed video + subtitles set
    any_family = True
    options_cls = SubtitleOptions

    @staticmethod
    def _split(files: list[DetectedFile]):
        videos = [f for f in files if f.family == MediaFamily.VIDEO]
        subs = [f for f in files if f.family == MediaFamily.SUBTITLE]
        return videos, subs

    def targets(self, files):
        videos, subs = self._split(files)
        if len(videos) != 1 or not subs or len(videos) + len(subs) != len(files):
            return []
        out = []
        if len(subs) == 1:
            out.append(
                self.target(
                    "burn:mp4",
                    "Burn into video",
                    "mp4",
                    "Subtitles",
                    "Draw the subtitles onto the picture (always visible)",
                )
            )
        out += [
            self.target(
                "subtrack:mkv",
                "Add track (MKV)",
                "mkv",
                "Subtitles",
                "Add as a subtitle track you can switch on/off",
            ),
            self.target(
                "subtrack:mp4",
                "Add track (MP4)",
                "mp4",
                "Subtitles",
                "Add as a subtitle track you can switch on/off",
            ),
        ]
        return out

    def run(self, job: ConversionJob) -> list[Path]:
        o: SubtitleOptions = job.options
        [video], subs = self._split(list(job.files))
        info = ffmpeg.probe(video.path)
        with tempfile.TemporaryDirectory(prefix="umc-burn-") as tmp:
            root = Path(tmp)
            if job.target.id == "burn:mp4":
                return [self._burn(job, o, video, subs[0], info, root)]
            return [self._mux(job, o, video, subs, info, root)]

    def _burn(self, job, o, video, sub, info, root: Path) -> Path:
        job.report(0.02, "Preparing subtitles")
        _to_ass(sub, root, o, job, styled=True)
        fonts = fonts_dir()
        flt = "ass=subs.ass"
        if fonts:
            shutil.copytree(fonts, root / "fonts")
            flt += ":fontsdir=fonts"
        dest = unique_path(job.output_dir_for(video.path), f"{video.path.stem} (subtitled)", "mp4")
        encoder = ffmpeg.pick_encoder(["libx264", "libopenh264", "h264_mf", "mpeg4"]) or "libx264"
        args = ["-i", str(video.path), "-vf", f"{flt},format=yuv420p", "-c:v", encoder]
        if encoder == "libx264":
            args += ["-crf", str(o.crf), "-preset", "medium"]
        args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
        try:
            ffmpeg.run_ffmpeg(
                args,
                total_seconds=info.duration,
                progress=job.sub_progress(0.05, 1.0),
                cancel_event=job.cancel_event,
                error_message=f"Couldn't burn the subtitles into “{video.name}”.",
                cwd=root,
            )
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        job.report(1.0, "Done")
        return dest

    def _mux(self, job, o, video, subs, info, root: Path) -> Path:
        container = job.target.ext
        inputs = ["-i", str(video.path)]
        maps = ["-map", "0:v?", "-map", "0:a?"]
        meta = []
        for n, sub in enumerate(subs):
            job.report(0.05 * n / len(subs), f"Preparing {sub.name}")
            src = _normalise(sub, root, o)
            fmt = "ass" if (container == "mkv" and sub.ext in ("ass", "ssa")) else "srt"
            staged = root / f"track{n}.{fmt}"
            _convert_sub(src, staged, fmt, o, job, sub.name)
            inputs += ["-i", str(staged)]
            maps += ["-map", f"{n + 1}:0"]
            lang = (
                ISO639_2.get(o.language.strip().lower(), o.language.strip().lower())
                if o.language.strip().lower() not in ("", "auto")
                else guess_language(sub.path)
            )
            meta += [
                f"-metadata:s:s:{n}",
                f"language={lang}",
                f"-metadata:s:s:{n}",
                f"title={sub.path.stem}",
            ]
            meta += [f"-disposition:s:{n}", "default" if (n == 0 and o.default_track) else "0"]
        dest = unique_path(
            job.output_dir_for(video.path), f"{video.path.stem} (with subtitles)", container
        )
        codec = ["-c:s", "mov_text"] if container == "mp4" else ["-c:s", "copy"]
        args = [*inputs, *maps, "-c:v", "copy", "-c:a", "copy", *codec, *meta, str(dest)]
        try:
            ffmpeg.run_ffmpeg(
                args,
                total_seconds=info.duration,
                progress=job.sub_progress(0.05, 1.0),
                cancel_event=job.cancel_event,
                error_message=f"Couldn't add the subtitles to “{video.name}”.",
            )
        except ConversionError as exc:
            dest.unlink(missing_ok=True)
            if container == "mp4" and "codec" in exc.details.lower():
                raise ConversionError(
                    f"The video in “{video.name}” can't go into MP4 without re-encoding."
                    " Use “Add track (MKV)”.",
                    details=exc.details,
                ) from exc
            raise
        job.report(1.0, "Done")
        return dest
