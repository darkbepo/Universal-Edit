"""The pre-parse scan of the HTML normaliser: prepares the text so libxml2 reads what an HTML5
parser (the website) reads.

Numeric references (Windows-1252 and invalid code points), the content of raw text / RCDATA
elements, the "/" of self-closing start tags and ``</br>`` end tags are adjusted by one pass
from left to right over the tags, read with their attributes. The website's
`subset-scan.ts` is the mirror image (an HTML5 parser reads ``</br>`` as <br> by itself).
"""

from __future__ import annotations

import re

# Numeric character references (see _num_ref).
_NUM_REF = re.compile(r"&#(?:[xX]([0-9a-fA-F]+)|([0-9]+));?")

_REPLACEMENT = chr(0xFFFD)  # U+FFFD


def _bad_code_point(n: int) -> bool:
    """A numeric reference HTML5 reads as U+FFFD here (libxml2 drops these or keeps controls
    that aren't valid XML): 0, C0 controls other than tab / LF / FF / CR, DEL, surrogates,
    noncharacters and anything past U+10FFFF."""
    return (
        n == 0
        or (n < 0x20 and n not in (0x09, 0x0A, 0x0C, 0x0D))
        or n == 0x7F
        or 0xD800 <= n <= 0xDFFF
        or 0xFDD0 <= n <= 0xFDEF
        or (n & 0xFFFE) == 0xFFFE
        or n > 0x10FFFF
    )


def _num_ref(m: re.Match[str]) -> str:
    """One numeric reference, read the way HTML5 does where libxml2 differs."""
    hex_digits, dec_digits = m.group(1), m.group(2)
    digits = (hex_digits or dec_digits).lstrip("0")
    # Longer numbers are past U+10FFFF anyway (and int() refuses very long ones).
    n = int(digits or "0", 16 if hex_digits else 10) if len(digits) <= 8 else 0x110000
    if 128 <= n <= 159:
        try:
            return bytes([n]).decode("cp1252")
        except UnicodeDecodeError:  # 0x81, 0x8d, 0x8f, 0x90, 0x9d: kept as they are
            return m.group(0)
    return _REPLACEMENT if _bad_code_point(n) else m.group(0)


def _num_refs(html: str) -> str:
    """Numeric references as HTML5 reads them: ``&#146;`` and friends are Windows-1252 (old
    Word / Windows pages use them for curly quotes and dashes; libxml2 would give control
    characters), and impossible or invalid code points are U+FFFD."""
    return _NUM_REF.sub(_num_ref, html) if "&#" in html else html


# Elements whose content HTML5 reads as text, where libxml2 parses tags and references in it:
# raw text (nothing decoded) and RCDATA (references decoded, tags are text). Comments, <script>
# and <style> are only skipped, so a "<xmp" inside them isn't taken for a tag.
_RAW_TEXT = frozenset({"xmp", "noembed", "noframes", "plaintext", "iframe"})
_RCDATA = frozenset({"title", "textarea"})
_SPECIAL_START = re.compile(
    r"<(?:(!--)|(script|style|xmp|noembed|noframes|plaintext|iframe|title|textarea)"
    r"(?=[\t\n\f\r />]))",
    re.I,
)
_SPECIAL_END = {
    name: re.compile(rf"</{name}(?=[\t\n\f\r />])", re.I)
    for name in ("script", "style", "xmp", "noembed", "noframes", "iframe", "title", "textarea")
}
_SPECIAL = frozenset(_SPECIAL_END) | {"plaintext"}
# A comment, or a start / end tag and its name (HTML5: a letter, then anything up to a space,
# "/" or ">").
_TAG_START = re.compile(r"<(?:(!--)|(/?)([A-Za-z][^\t\n\f\r />]*))")
_HTML_SPACE = "\t\n\f\r "
# An end tag "</br>" (any case, attributes allowed): HTML5 reads it as <br>, libxml2 drops it.
_BR_END = re.compile(r"</br(?=[\t\n\f\r />])", re.I)


def _tag_end(html: str, i: int) -> tuple[int, bool]:
    """The index of the ">" that ends a tag whose attributes start at ``i`` (-1 when it never
    ends), and whether the tag ends with a self-closing "/". Quoted attribute values are
    skipped, so a ">" or "<xmp>" inside one is not markup."""
    n = len(html)
    in_name = False  # in (or after) an attribute name, where "=" starts its value
    slash = -1  # the last "/" outside attribute values
    while i < n:
        c = html[i]
        if c == ">":
            return i, slash == i - 1
        if c == "=" and in_name:
            i += 1
            while i < n and html[i] in _HTML_SPACE:
                i += 1
            if i < n and html[i] in "\"'":
                close = html.find(html[i], i + 1)
                if close < 0:
                    return -1, False
                i = close + 1
            else:  # unquoted: up to a space or ">"
                while i < n and html[i] not in _HTML_SPACE and html[i] != ">":
                    i += 1
            in_name = False
            continue
        if c == "/":
            in_name = False
            slash = i
        elif c not in _HTML_SPACE:
            in_name = True
        i += 1
    return -1, False


def html5_text(html: str) -> str:
    """Prepare ``html`` for libxml2 so it reads what an HTML5 parser (the website) reads:
    numeric references, the content of raw text / RCDATA elements (escaped, so libxml2
    keeps it as text), the "/" of a self-closing start tag (dropped: HTML5 ignores it, so
    ``<p/>x</p>`` is one paragraph), and ``</br>`` (a line break in HTML5, so written ``<br>``).
    One pass from left to right; the tags in between are read with their attributes, so a tag
    name written inside a quoted value starts nothing."""
    if (
        "&#" not in html
        and "/>" not in html
        and not _SPECIAL_START.search(html)
        and not _BR_END.search(html)
    ):
        return html
    out: list[str] = []
    pos = i = 0
    while (m := _TAG_START.search(html, i)) is not None:
        if m.group(1):  # a comment, up to "-->" (an unclosed one runs to the end)
            end = html.find("-->", m.end())
            i = len(html) if end < 0 else end + 3
            continue
        gt, self_closing = _tag_end(html, m.end())
        if gt < 0:
            break
        i = gt + 1
        name = m.group(3).lower()
        if m.group(2) and name == "br":
            out.append(_num_refs(html[pos : m.start()]))
            out.append("<br>")
            pos = i
            continue
        if self_closing and not m.group(2):
            out.append(_num_refs(html[pos : gt - 1]))
            pos = gt
        if m.group(2) or name not in _SPECIAL:
            continue
        out.append(_num_refs(html[pos:i]))
        if name == "plaintext":  # never ends
            end = len(html)
        else:
            close = _SPECIAL_END[name].search(html, i)
            end = close.start() if close else len(html)
        body = html[i:end]
        if name in _RAW_TEXT:
            body = body.replace("&", "&amp;").replace("<", "&lt;")
        elif name in _RCDATA:
            body = _num_refs(body.replace("<", "&lt;"))
        else:  # script / style: libxml2 keeps them as text already
            body = _num_refs(body)
        out.append(body)
        pos = i = end
    out.append(_num_refs(html[pos:]))
    return "".join(out)
