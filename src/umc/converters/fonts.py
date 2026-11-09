"""Fonts: TTF ↔ OTF ↔ WOFF ↔ WOFF2 (fontTools), with optional subsetting.

WOFF/WOFF2 are just compressed wrappers, so wrapping/unwrapping keeps every table. TTF ↔ OTF
changes the outline format: TrueType (quadratic) ↔ CFF (cubic), using fontTools' own recipes —
cu2qu for CFF → TrueType (tiny approximation), and an exact quadratic → cubic conversion the other
way. Hinting can't survive an outline change and is dropped then.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.core.detect import MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt

FORMATS = {"ttf": "TTF", "otf": "OTF", "woff": "WOFF", "woff2": "WOFF2"}

# Google Fonts' "latin" and "latin-ext" unicode ranges (what most web fonts are cut to).
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
)
LATIN_EXT = LATIN + (
    ",U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1D00-1DBF,U+1E00-1E9F,"
    "U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
)
CYRILLIC = LATIN + ",U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"
GREEK = LATIN + ",U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF"
SUBSETS = {"latin": LATIN, "latin-ext": LATIN_EXT, "cyrillic": CYRILLIC, "greek": GREEK}


@dataclass
class FontOptions:
    subset: str = opt(
        "all",
        "Characters to keep",
        choices=[
            ("all", "All (no subsetting)"),
            ("latin", "Latin (English, Western Europe)"),
            ("latin-ext", "Latin extended (all European Latin)"),
            ("cyrillic", "Latin + Cyrillic"),
            ("greek", "Latin + Greek"),
            ("custom", "Only the characters I type…"),
        ],
        help="Smaller files for the web: drop the characters you don't need.",
    )
    custom_text: str = opt(
        "",
        "Characters",
        help="Type or paste every character to keep, e.g. a heading or a logo text.",
        show_if=lambda v: v["subset"] == "custom",
    )
    keep_hinting: bool = opt(
        True,
        "Keep hinting",
        help="Hinting makes small text crisper on Windows. Off = smaller file.",
    )
    all_features: bool = opt(
        True,
        "Keep all OpenType features",
        help="Ligatures, stylistic sets, small caps… Off keeps only the common ones (smaller).",
        show_if=lambda v: v["subset"] != "all",
    )


# --------------------------------------------------------------------------- outline conversion


def _is_variable(font) -> bool:
    return "fvar" in font


def otf_to_ttf(font) -> None:
    """CFF outlines → TrueType outlines (fontTools Snippets/otf2ttf.py)."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    if "CFF2" in font or _is_variable(font):
        raise ConversionError(
            "Variable OpenType (CFF2) fonts can't be turned into TTF. WOFF / WOFF2 work."
        )
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyphs = {}
    for name in order:
        tt_pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(tt_pen, max_err=1.0, reverse_direction=True))
        glyphs[name] = tt_pen.glyph()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    hmtx = font["hmtx"]
    for name, g in glyf.glyphs.items():
        if hasattr(g, "xMin"):
            hmtx[name] = (hmtx[name][0], g.xMin)
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000
    for attr in ("maxZones",):
        setattr(maxp, attr, 1)
    for attr in (
        "maxTwilightPoints",
        "maxStorage",
        "maxFunctionDefs",
        "maxInstructionDefs",
        "maxStackElements",
        "maxSizeOfInstructions",
    ):
        setattr(maxp, attr, 0)
    maxp.maxComponentElements = max(
        (len(getattr(g, "components", []) or []) for g in glyphs.values()), default=0
    )
    maxp.compile(font)
    post = font["post"]
    post.formatType, post.extraNames, post.mapping, post.glyphOrder = 2.0, [], {}, order
    try:
        post.compile(font)
    except OverflowError:
        post.formatType = 3.0
    font["head"].glyphDataFormat = 0
    font.sfntVersion = "\x00\x01\x00\x00"


def ttf_to_otf(font) -> None:
    """TrueType outlines → CFF (quadratic curves are exactly representable as cubic)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.reverseContourPen import ReverseContourPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    if _is_variable(font):
        raise ConversionError(
            "Variable TrueType fonts can't be turned into OTF. WOFF / WOFF2 work."
        )
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    char_strings = {}
    for name in order:
        pen = T2CharStringPen(glyph_set[name].width, glyph_set)
        glyph_set[name].draw(ReverseContourPen(pen))
        char_strings[name] = pen.getCharString()
    name_table = font["name"]
    ps_name = (name_table.getDebugName(6) or "Font").replace(" ", "")
    info = {
        "FullName": name_table.getDebugName(4) or ps_name,
        "FamilyName": name_table.getDebugName(1) or ps_name,
        "Weight": name_table.getDebugName(2) or "Regular",
    }
    for tag in ("glyf", "loca", "fpgm", "prep", "cvt ", "gasp", "hdmx", "LTSH", "VDMX", "cvar"):
        if tag in font:
            del font[tag]
    fb = FontBuilder(font=font, isTTF=False)
    fb.setupCFF(ps_name, info, char_strings, {})
    maxp = font["maxp"]
    maxp.tableVersion = 0x00005000
    post = font["post"]
    post.formatType = 3.0
    font.sfntVersion = "OTTO"


def outlines(font) -> str:
    return "cff" if ("CFF " in font or "CFF2" in font) else "tt"


def subset_font(font, o: FontOptions, keep_all_glyphs: bool = False) -> None:
    from fontTools import subset

    options = subset.Options()
    options.layout_features = (
        ["*"] if (o.all_features or keep_all_glyphs) else options.layout_features
    )
    options.hinting = o.keep_hinting
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.glyph_names = True
    subsetter = subset.Subsetter(options=options)
    if keep_all_glyphs:  # only strip hinting
        options.layout_closure = True
        subsetter.populate(glyphs=font.getGlyphOrder())
    elif o.subset == "custom":
        text = o.custom_text
        if not text.strip():
            raise ConversionError(
                "Type the characters to keep (or choose another “Characters to keep”)."
            )
        subsetter.populate(text=text + " ")
    else:
        subsetter.populate(unicodes=subset.parse_unicodes(SUBSETS[o.subset]))
    subsetter.subset(font)


def convert_font(src: Path, dest: Path, fmt: str, o: FontOptions) -> None:
    import struct

    from fontTools.ttLib import TTFont, TTLibError

    try:
        font = TTFont(src, recalcTimestamp=False)
        font.ensureDecompiled()
        if o.subset != "all":
            subset_font(font, o)
        elif not o.keep_hinting:
            subset_font(font, o, keep_all_glyphs=True)
        if fmt == "ttf" and outlines(font) == "cff":
            otf_to_ttf(font)
        elif fmt == "otf" and outlines(font) == "tt":
            ttf_to_otf(font)
        font.flavor = fmt if fmt in ("woff", "woff2") else None
        font.save(dest)
    except ConversionError:
        dest.unlink(missing_ok=True)
        raise
    except (TTLibError, OSError, ValueError, KeyError, AssertionError, struct.error) as exc:
        dest.unlink(missing_ok=True)
        raise ConversionError(
            f"Couldn't read “{src.name}”. Is it a valid font?", details=repr(exc)
        ) from exc
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


class FontConverter(Converter):
    family = MediaFamily.FONT
    options_cls = FontOptions

    def targets(self, files):
        single = files[0].ext if len(files) == 1 else ""
        return [self.target(e, label, e, "Font") for e, label in FORMATS.items() if e != single]

    def run(self, job: ConversionJob) -> list[Path]:
        o: FontOptions = job.options
        outputs = []
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / len(job.files), f"Converting {f.name}")
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, job.target.ext)
            convert_font(f.path, dest, job.target.ext, o)
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
