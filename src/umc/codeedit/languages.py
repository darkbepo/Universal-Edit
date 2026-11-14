"""Which languages the code editor knows: extensions, whole file names, comment marks and the
names of the colouring engines (Pygments on the desktop, CodeMirror on the website).

``web/src/codeedit/languages.json`` is ``export()`` (``python -m umc.codeedit.languages``); a test
checks they match. ``core/detect.py`` builds the ``code`` family from this table."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import PurePath


@dataclass(frozen=True)
class Language:
    id: str
    name: str  # shown in the status bar
    exts: tuple[str, ...]  # canonical, lowercase, no dot
    filenames: tuple[str, ...]  # whole lowercase names ("dockerfile", ".gitignore")
    line_comment: str | None
    block_comment: tuple[str, str] | None
    pygments: str  # Pygments lexer alias
    codemirror: str  # the website's language key


def _l(
    id_: str,
    name: str,
    exts: tuple[str, ...] = (),
    filenames: tuple[str, ...] = (),
    line: str | None = None,
    block: tuple[str, str] | None = None,
    pygments: str | None = None,
    cm: str | None = None,
) -> Language:
    return Language(id_, name, exts, filenames, line, block, pygments or id_, cm or id_)


C_BLOCK = ("/*", "*/")
XML_BLOCK = ("<!--", "-->")

PLAIN = _l("text", "Plain text", ("log", "txt"), pygments="text", cm="plain")

LANGUAGES: tuple[Language, ...] = (
    PLAIN,
    # Code (the "code" family)
    _l("css", "CSS", ("css",), block=C_BLOCK),
    _l("scss", "SCSS", ("scss",), line="//", block=C_BLOCK, cm="sass"),
    _l("less", "Less", ("less",), line="//", block=C_BLOCK),
    _l("javascript", "JavaScript", ("js", "mjs", "cjs"), line="//", block=C_BLOCK, pygments="js"),
    _l("jsx", "JavaScript (JSX)", ("jsx",), line="//", block=C_BLOCK, pygments="jsx"),
    _l("typescript", "TypeScript", ("ts", "tsx"), line="//", block=C_BLOCK),
    _l("python", "Python", ("py", "pyw"), line="#"),
    _l("java", "Java", ("java",), line="//", block=C_BLOCK),
    _l("c", "C", ("c", "h"), line="//", block=C_BLOCK, cm="cpp"),
    _l("cpp", "C++", ("cpp", "hpp", "cc", "cxx", "hh"), line="//", block=C_BLOCK),
    _l("csharp", "C#", ("cs",), line="//", block=C_BLOCK),
    _l("go", "Go", ("go",), line="//", block=C_BLOCK),
    _l("rust", "Rust", ("rs",), line="//", block=C_BLOCK),
    _l("php", "PHP", ("php",), line="//", block=C_BLOCK),
    _l("ruby", "Ruby", ("rb",), line="#"),
    _l("shell", "Shell script", ("sh", "bash", "zsh"), line="#", pygments="bash"),
    _l("powershell", "PowerShell", ("ps1", "psm1"), line="#", block=("<#", "#>")),
    _l("batch", "Batch file", ("bat", "cmd"), line="REM"),
    _l("sql", "SQL", ("sql",), line="--", block=C_BLOCK),
    _l(
        "ini",
        "INI / config",
        ("ini", "cfg", "conf"),
        (".editorconfig", ".npmrc"),
        line=";",
    ),
    _l("toml", "TOML", ("toml",), line="#"),
    _l("dotenv", "Environment file", ("env",), (".env",), line="#", pygments="bash", cm="shell"),
    _l("properties", "Properties", ("properties",), line="#"),
    _l("vue", "Vue", ("vue",), block=XML_BLOCK, pygments="html", cm="html"),
    _l("svelte", "Svelte", ("svelte",), block=XML_BLOCK, pygments="html", cm="html"),
    _l("lua", "Lua", ("lua",), line="--"),
    _l("kotlin", "Kotlin", ("kt", "kts"), line="//", block=C_BLOCK),
    _l("swift", "Swift", ("swift",), line="//", block=C_BLOCK),
    _l("r", "R", ("r",), line="#", pygments="r"),
    _l("perl", "Perl", ("pl", "pm"), line="#"),
    _l("dockerfile", "Dockerfile", (), ("dockerfile",), line="#", pygments="docker"),
    _l("makefile", "Makefile", ("mk",), ("makefile", "gnumakefile"), line="#", pygments="make"),
    _l("gitignore", "Git ignore", (), (".gitignore", ".gitattributes"), line="#", pygments="text"),
    # Editable as code, but they belong to other families (they keep converting as before)
    _l("html", "HTML", ("html",), block=XML_BLOCK),
    _l("xml", "XML", ("xml",), block=XML_BLOCK),
    _l("svg", "SVG", ("svg",), block=XML_BLOCK, pygments="xml", cm="xml"),
    _l("json", "JSON", ("json", "ipynb")),
    _l("yaml", "YAML", ("yaml",), line="#"),
    _l("csv", "CSV", ("csv", "tsv"), pygments="text", cm="plain"),
    _l("markdown", "Markdown", ("md",), block=XML_BLOCK),
    _l("subtitles", "Subtitles", ("srt", "vtt", "ass", "ssa"), pygments="text", cm="plain"),
    _l("latex", "LaTeX", ("tex",), line="%", pygments="tex", cm="stex"),
    _l("rst", "reStructuredText", ("rst",), line=".. ", pygments="rst", cm="plain"),
)

_BY_NAME = {n: lang for lang in LANGUAGES for n in lang.filenames}
_BY_EXT = {e: lang for lang in LANGUAGES for e in lang.exts}


def language_for(name: str) -> Language:
    """The language of a file, by its whole name first, then its extension (else plain text)."""
    lower = PurePath(name).name.lower()
    if lower in _BY_NAME:
        return _BY_NAME[lower]
    suffix = PurePath(lower).suffix.lstrip(".")
    from umc.core.detect import canonical_ext  # local: detect imports this module

    return _BY_EXT.get(canonical_ext(suffix), PLAIN) if suffix else PLAIN


def export() -> dict:
    return {"languages": [asdict(lang) for lang in LANGUAGES]}


if __name__ == "__main__":  # python -m umc.codeedit.languages > web/src/codeedit/languages.json
    json.dump(export(), sys.stdout, indent=1)
    sys.stdout.write("\n")
