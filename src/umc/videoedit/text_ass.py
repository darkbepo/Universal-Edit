"""Text items as an ASS subtitle file, burned in by FFmpeg's libass. Pure Python, no Qt."""

from __future__ import annotations

from collections.abc import Sequence

from umc.converters.subtitles import _ass_colour
from umc.editor.fonts import find_font
from umc.videoedit.project import TextItem

# anchor → (numpad alignment, column 0..2, row 0..2)
_ANCHORS = {
    "top-left": (7, 0, 0),
    "top": (8, 1, 0),
    "top-right": (9, 2, 0),
    "left": (4, 0, 1),
    "center": (5, 1, 1),
    "right": (6, 2, 1),
    "bottom-left": (1, 0, 2),
    "bottom": (2, 1, 2),
    "bottom-right": (3, 2, 2),
}
_BASE = (0.05, 0.50, 0.95)
_JOINER = "⁠"
_FALLBACK_FONT = "DejaVu Sans"


def font_name(t: TextItem) -> str:
    """The family to use: the item's, if the system (or the bundled set) has it."""
    if t.font and (find_font(t.font, t.bold, t.italic) or t.font == _FALLBACK_FONT):
        return t.font
    return _FALLBACK_FONT


def _fades(t: TextItem) -> tuple[float, float]:
    """Fade-in / fade-out, scaled down together when they don't fit in the item."""
    fin, fout = max(t.fade_in, 0.0), max(t.fade_out, 0.0)
    span = t.end - t.start
    if fin + fout > span > 0:
        scale = span / (fin + fout)
        fin, fout = fin * scale, fout * scale
    return fin, fout


def _time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def escape(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # A lone backslash outside {} is literal in libass (a doubled one shows twice). The word
    # joiner (zero width) stops a following n / N / h / { / } from being read as a command.
    text = text.replace("\\", "\\" + _JOINER).replace("{", "\\{").replace("}", "\\}")
    return text.replace("\n", "\\N")


def _style(n: int, t: TextItem, h: int) -> str:
    bold = -1 if t.bold else 0
    italic = -1 if t.italic else 0
    primary = _ass_colour(t.colour, 0)
    if t.box:
        alpha = round(255 * (1 - t.box_opacity / 100))
        box = _ass_colour(t.box_colour, alpha)
        # libass draws the BorderStyle=3 box in OutlineColour; BackColour is the shadow.
        outline, back, border = box, box, 3
    else:
        outline, back, border = _ass_colour(t.outline_colour, 0), _ass_colour("#000000", 255), 1
    fields = [
        f"T{n}",
        font_name(t),
        str(round(t.size / 100 * h)),
        primary,
        primary,
        outline,
        back,
        str(bold),
        str(italic),
        "0",
        "0",
        "100",
        "100",
        "0",
        "0",
        str(border),
        f"{t.outline:g}",
        "0",
        str(_ANCHORS[t.anchor][0]),
        "0",
        "0",
        "0",
        "1",
    ]
    return "Style: " + ",".join(fields)


def _fade_tag(start: float, end: float, fade_in: float, fade_out: float) -> str:
    """The fade override for an event shown from ``max(start, 0)`` to ``end``."""
    if start >= 0:
        if fade_in <= 0 and fade_out <= 0:
            return ""
        return f"\\fad({round(fade_in * 1000)},{round(fade_out * 1000)})"
    done = -start  # seconds of the fade-in already played at time 0
    if fade_in > 0 and done < fade_in:
        a0 = round(255 * (1 - done / fade_in))
        t2 = round((fade_in - done) * 1000)
    else:
        a0, t2 = 0, 0
    dur = round(end * 1000)
    t3 = round((end - fade_out) * 1000) if fade_out > 0 else dur
    if a0 == 0 and fade_out <= 0:
        return ""
    return f"\\fade({a0},0,255,0,{t2},{max(t3, 0)},{dur})"


def build_ass(items: Sequence[TextItem], size: tuple[int, int]) -> str:
    w, h = size
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,"
        "Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
    ]
    events = []
    for n, t in enumerate(items):
        if t.end <= 0 or t.end <= t.start:
            continue
        lines.append(_style(n, t, h))
        an, col, row = _ANCHORS[t.anchor]
        x = round(w * (_BASE[col] + t.x_offset / 100))
        y = round(h * (_BASE[row] + t.y_offset / 100))
        tags = f"{{\\an{an}\\pos({x},{y}){_fade_tag(t.start, t.end, *_fades(t))}}}"
        events.append(
            f"Dialogue: 0,{_time(max(t.start, 0.0))},{_time(t.end)},T{n},,0,0,0,,"
            f"{tags}{escape(t.text)}"
        )
    lines += [
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        *events,
        "",
    ]
    return "\n".join(lines)
