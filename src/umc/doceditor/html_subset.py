"""Normalise arbitrary HTML into the editor's small, canonical HTML subset (no Qt).

The output is a fragment (no <html>/<body>) and is deterministic and idempotent, so other
modules can compare it with ``==``. Pictures are pulled out into a ``DocImages`` dict and the
HTML refers to them as ``img/{n}.{ext}``.

The subset: blocks ``p``, ``h1``-``h3``, ``blockquote`` (of ``p``), ``pre`` (one per code
paragraph), ``ul`` / ``ol`` / ``li`` (nested lists inside ``li``; empty items are kept; text
after a nested list becomes the next item), ``table`` / ``tr`` / ``td`` (``colspan``,
``rowspan``; ``<table style="border:none">`` = no borders), ``hr``. Paragraph styles
``text-align``, ``margin-left:{36*n}pt``, ``page-break-before:always``. Inline ``b``, ``i``,
``u``, ``s``, ``a href``, ``img src width height``, ``br``, ``span style`` (``font-family``,
``font-size:{n}pt``, ``color``, ``background-color``; CSS bold / italic / underline / strike
on a span become tags). Text: runs of spaces and line breaks collapse to one space; tabs and
no-break spaces (U+00A0) are kept; ``pre`` keeps everything.
"""

from __future__ import annotations

import base64
import binascii
import html as _html
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import ParseResult, unquote, urlparse

import lxml.html
from lxml import etree
from PIL import ImageColor

from umc.core.errors import ConversionError
from umc.doceditor.html_scan import html5_text
from umc.doceditor.types import (
    MAX_FILE_BYTES,
    W_PAGESTYLE,
    W_PICTURES,
    W_RAWHTML,
    W_WEBPICS,
    DocImages,
    next_image_name,
)

# Removed together with everything inside them.
_DROP = frozenset(
    {
        "script",
        "style",
        "head",
        "title",
        "meta",
        "link",
        "iframe",
        "object",
        "embed",
        "noscript",
        "template",
        "svg",
        "canvas",
        "audio",
        "video",
        "applet",
        "textarea",
        "select",
        "option",
        "button",
        "frame",
        "frameset",
    }
)
_BLOCK = frozenset(
    {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "hr",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "form",
        "figure",
        "figcaption",
        "address",
        "center",
        "dl",
        "dt",
        "dd",
        "fieldset",
        "body",
        "html",
    }
)
_LISTS = frozenset({"ul", "ol"})
# Embedded content that is dropped (W_RAWHTML); forms keep their text, not their fields.
_RAW_DROPPED = frozenset(
    {
        "iframe",
        "object",
        "embed",
        "applet",
        "audio",
        "video",
        "canvas",
        "svg",
        "frame",
        "frameset",
        "form",
        "input",
        "textarea",
        "select",
        "button",
    }
)
_INLINE_MAP = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "cite": "i",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
}
_HEADINGS = {"h1": "h1", "h2": "h2", "h3": "h3", "h4": "h3", "h5": "h3", "h6": "h3"}
_IMAGE_EXTS = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "gif": "gif",
    "bmp": "bmp",
    "webp": "webp",
}
_FONT_SIZES_PT = {1: 8, 2: 10, 3: 12, 4: 14, 5: 18, 6: 24, 7: 36}
_SAFE_SCHEMES = {"", "http", "https", "mailto", "tel"}
_DATA_URI = re.compile(
    r"^data:image/(png|jpe?g|gif|bmp|webp)(?:;[^,;]*)*;base64,(.*)$", re.I | re.S
)
_CANON_IMG = re.compile(r"^img/\d+\.(?:png|jpg|gif|bmp|webp)$")
# Collapsed to one space: runs of spaces, and line breaks with the indentation around them
# (source formatting). Tabs elsewhere and no-break spaces (U+00A0) are content and are kept.
_WS = re.compile(r"[ \t]*[\r\n\f][ \t\r\n\f]*| {2,}")
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)
# Deepest paragraph indent: 20 levels of 36 pt (huge values used to print as "1e+27pt").
_MAX_INDENT = 20
_BR = ("V", "<br>")

# A token is (kind, text): T = text, O = opening tag, C = closing tag, V = void tag,
# S = a line break the normaliser added between blocks (dropped at the ends).
_Token = tuple[str, str]


def _parse_style(value: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for decl in (value or "").split(";"):
        key, sep, val = decl.partition(":")
        if sep:
            result[key.strip().lower()] = re.sub(r"!\s*important", "", val, flags=re.I).strip()
    return result


def _colour(value: str) -> str | None:
    try:
        rgb = ImageColor.getrgb(value.strip().lower())
    except ValueError:
        return None
    return "#" + "".join(f"{c:02x}" for c in rgb[:3])


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _points(value: str) -> float | None:
    """A CSS length in pt or px, as points."""
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(pt|px)", value.strip().lower())
    if not m:
        return None
    n = float(m.group(1))
    return n * 0.75 if m.group(2) == "px" else n


def _font_family(value: str) -> str | None:
    first = value.split(",")[0].strip().strip("'\"").strip()
    first = re.sub(r"[<>\"';{}]", "", first)
    return first or None


def _span_style(fam: str | None, size: float | None, fg: str | None, bg: str | None) -> str:
    parts = []
    if fam:
        parts.append(f"font-family:{fam}")
    if size is not None and 0 < size <= 1000:
        parts.append(f"font-size:{_number(size)}pt")
    if fg:
        parts.append(f"color:{fg}")
    if bg:
        parts.append(f"background-color:{bg}")
    return ";".join(parts)


def _css_emphasis(css: dict[str, str]) -> list[str]:
    """Bold / italic / underline / strike set through CSS, as the tags b, i, u, s."""
    tags = []
    weight = css.get("font-weight", "").lower()
    if weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600):
        tags.append("b")
    if css.get("font-style", "").lower() in ("italic", "oblique"):
        tags.append("i")
    deco = f"{css.get('text-decoration', '')} {css.get('text-decoration-line', '')}".lower()
    if "underline" in deco:
        tags.append("u")
    if "line-through" in deco:
        tags.append("s")
    return tags


_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _urlparse(url: str) -> ParseResult | None:
    """``urlparse``, or None for a URL it can't split (such as ``http://[::1/x``)."""
    try:
        return urlparse(url)
    except ValueError:
        return None


def _link_scheme(href: str) -> str | None:
    """The scheme of a link (lower case, "" for a relative one), or None when the link is
    unusable. A host urlparse refuses ("http://[abc]/", on Python 3.11.4+) still has a
    scheme; only unbalanced brackets in the host are refused, as on the website."""
    try:
        return urlparse(href).scheme.lower()
    except ValueError:
        pass
    m = _SCHEME.match(href)
    rest = href[m.end() :] if m else href
    if rest.startswith("//"):
        end = len(rest)
        for c in "/?#":
            i = rest.find(c, 2)
            if i >= 0:
                end = min(end, i)
        netloc = rest[2:end]
        if ("[" in netloc) != ("]" in netloc):
            return None
    return m.group(0)[:-1].lower() if m else ""


def _not_bold(tag: str, node: etree._Element) -> bool:
    """<b style="font-weight:normal"> (pasted from Google Docs) isn't bold."""
    weight = _parse_style(node.get("style")).get("font-weight", "").lower()
    return tag in ("b", "strong") and (
        weight in ("normal", "lighter") or (weight.isdigit() and int(weight) < 600)
    )


def _attr(value: str) -> str:
    return _html.escape(value, quote=True)


def _tag(node: etree._Element) -> str | None:
    tag = node.tag
    return tag.lower() if isinstance(tag, str) else None  # comments / PIs have no str tag


def _finalise(tokens: list[_Token], *, pre: bool = False, drop_trailing_br: bool = False) -> str:
    """Collapse whitespace (outside <pre>), trim the ends and serialise the tokens."""
    if not pre:
        out: list[_Token] = []
        prev_space = True
        for kind, text in tokens:
            if kind == "T":
                text = _WS.sub(" ", text)
                if prev_space and text.startswith(" "):
                    text = text[1:]
                if not text:
                    continue
                prev_space = text.endswith(" ")
            elif kind in ("V", "S"):
                prev_space = text == "<br>"
            elif kind == "C" and out and out[-1][0] == "O" and text == "</a>":
                prev_space = False  # an empty link (a badge without its picture) is content
            out.append((kind, text))
        tokens = out
        if drop_trailing_br:
            while tokens and tokens[-1] == _BR:
                tokens.pop()
        i = len(tokens)
        while i > 0:  # skip closing tags at the end, then trim the last text
            kind, text = tokens[i - 1]
            if kind == "C":
                i -= 1
            elif kind == "S":
                del tokens[i - 1]
                i -= 1
            elif kind == "T" and text.endswith(" "):
                tokens[i - 1] = (kind, text.rstrip(" "))
                break
            else:
                break
    tokens = _merge_adjacent(tokens)
    return "".join(_html.escape(t, quote=False) if k == "T" else t for k, t in tokens)


def _merge_adjacent(tokens: list[_Token]) -> list[_Token]:
    """``<b>x</b><b>y</b>`` → ``<b>xy</b>``: an inline tag closed and opened again right away
    is one (so the subset is canonical). An empty element stays its own (two links in a row
    where the first is an empty badge)."""
    out: list[_Token] = []
    stack: list[str] = []  # the opening tags still open
    closed: list[tuple[str, bool]] = []  # the elements the closing tags ending out close
    for kind, text in tokens:
        if kind == "O" and closed and closed[-1][0] == text and not closed[-1][1]:
            out.pop()  # reopen it instead (and the one it was in, if that comes next)
            closed.pop()
            stack.append(text)
            continue
        if kind == "C" and stack:
            opener = stack.pop()
            closed.append((opener, bool(out) and out[-1] == ("O", opener)))
        else:
            closed = []
            if kind == "O":
                stack.append(text)
        out.append((kind, text))
    return out


def _is_plain_relative(base_dir: Path, rel: str) -> bool:
    """``rel`` is a relative path that stays inside ``base_dir`` (decided without any I/O)."""
    if rel.startswith(("\\\\", "//")) or PureWindowsPath(rel).drive:
        return False
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute():
        return False
    base = os.path.normpath(base_dir)
    joined = os.path.normpath(os.path.join(base, rel))
    return joined.startswith(base.rstrip("\\/") + os.sep)


class _Normaliser:
    def __init__(self, base_dir: Path | None, reserved: set[str]) -> None:
        self.base_dir = base_dir.resolve() if base_dir else None
        self.images: DocImages = {}
        self.names: dict[tuple[bytes, str], str] = {}  # one picture used twice keeps one name
        self.reserved = reserved
        self.lost_pictures = False  # a relative picture that couldn't be read (W_PICTURES)

    # ----- pictures -----------------------------------------------------
    def _add_image(self, data: bytes, ext: str) -> str:
        if (data, ext) in self.names:
            return self.names[(data, ext)]
        name = next_image_name(self.images, ext)
        n = len(self.images) + 1
        while name in self.images or name in self.reserved:
            n += 1
            name = f"img/{n}.{ext}"
        self.images[name] = data
        self.names[(data, ext)] = name
        return name

    def _image_src(self, src: str) -> str | None:
        src = src.strip()
        m = _DATA_URI.match(src)
        if m:
            try:
                data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
            except (binascii.Error, ValueError):
                return None
            return self._add_image(data, _IMAGE_EXTS[m.group(1).lower()]) if data else None
        parsed = _urlparse(src)
        if parsed is None or parsed.scheme or parsed.netloc or not parsed.path:
            return None
        found = self._read_relative(unquote(parsed.path))
        if found is not None:
            data, ext = found
            return self._add_image(data, ext)
        # Already in canonical form (output of an earlier pass): keep the reference.
        if _CANON_IMG.match(src):
            return src
        self.lost_pictures = True  # missing, outside the folder, or not a picture
        return None

    def _read_relative(self, rel: str) -> tuple[bytes, str] | None:
        if self.base_dir is None or not _is_plain_relative(self.base_dir, rel):
            return None  # checked before touching the disk: a UNC path would contact the host
        try:
            path = (self.base_dir / rel).resolve()
            ext = _IMAGE_EXTS.get(path.suffix.lower().lstrip("."))
            if (
                ext is None
                or not path.is_relative_to(self.base_dir)
                or not path.is_file()
                or path.stat().st_size > MAX_FILE_BYTES
            ):
                return None
            return path.read_bytes(), ext
        except (OSError, ValueError):
            return None

    # ----- inline -------------------------------------------------------
    @staticmethod
    def _text(text: str | None, toks: list[_Token]) -> None:
        if text:
            toks.append(("T", text))

    def _inline_children(self, node: etree._Element, toks: list[_Token]) -> None:
        self._text(node.text, toks)
        for child in node:
            self._inline(child, toks)
            self._text(child.tail, toks)

    def _inline(self, node: etree._Element, toks: list[_Token]) -> None:
        tag = _tag(node)
        if tag is None or tag in _DROP:
            return
        if tag == "br":
            toks.append(_BR)
        elif tag == "img":
            self._img(node, toks)
        elif tag in _INLINE_MAP and not _not_bold(tag, node):
            out = _INLINE_MAP[tag]
            toks.append(("O", f"<{out}>"))
            self._inline_children(node, toks)
            toks.append(("C", f"</{out}>"))
        elif tag == "a":
            href = re.sub(r"[\x00-\x1f\x7f]", "", node.get("href") or "").strip()
            scheme = _link_scheme(href) if href else None
            if scheme in _SAFE_SCHEMES:
                toks.append(("O", f'<a href="{_attr(href)}">'))
                self._inline_children(node, toks)
                toks.append(("C", "</a>"))
            else:
                self._inline_children(node, toks)
        elif tag in ("span", "font"):
            style = self._span_style(node, font=tag == "font")
            wraps = _css_emphasis(_parse_style(node.get("style")))
            if style:
                toks.append(("O", f'<span style="{_attr(style)}">'))
            toks.extend(("O", f"<{t}>") for t in wraps)
            self._inline_children(node, toks)
            toks.extend(("C", f"</{t}>") for t in reversed(wraps))
            if style:
                toks.append(("C", "</span>"))
        elif tag in _BLOCK:  # a block inside an inline tag: its content becomes a line
            self._separate(toks)
            self._inline_children(node, toks)
            self._separate(toks)
        else:  # unknown inline: keep the content
            self._inline_children(node, toks)

    @staticmethod
    def _separate(toks: list[_Token]) -> None:
        for kind, text in reversed(toks):
            if kind == "O":
                continue
            if kind == "S" or (kind, text) == _BR:
                return
            toks.append(("S", "<br>"))
            return

    @staticmethod
    def _span_style(node: etree._Element, *, font: bool) -> str:
        css = _parse_style(node.get("style"))
        fam = css.get("font-family")
        size = _points(css["font-size"]) if "font-size" in css else None
        fg = _colour(css["color"]) if "color" in css else None
        bg = _colour(css["background-color"]) if "background-color" in css else None
        if font:
            fam = fam or node.get("face")
            digits = re.fullmatch(r"\s*([1-7])\s*", node.get("size") or "")
            if size is None and digits:
                size = _FONT_SIZES_PT[int(digits.group(1))]
            if fg is None and node.get("color"):
                fg = _colour(node.get("color") or "")
        return _span_style(_font_family(fam) if fam else None, size, fg, bg)

    def _img(self, node: etree._Element, toks: list[_Token]) -> None:
        name = self._image_src(node.get("src") or "")
        if name is None:
            return
        css = _parse_style(node.get("style"))
        attrs = f'src="{_attr(name)}"'
        for dim in ("width", "height"):
            raw = node.get(dim) or css.get(dim) or ""
            m = re.fullmatch(r"\s*(\d+)(?:px)?\s*", raw)
            if m and 0 < int(m.group(1)) <= 100000:
                attrs += f' {dim}="{int(m.group(1))}"'
        toks.append(("V", f"<img {attrs}>"))

    # ----- blocks -------------------------------------------------------
    @staticmethod
    def _block_style(node: etree._Element) -> str:
        css = _parse_style(node.get("style"))
        parts = []
        align = (css.get("text-align") or node.get("align") or "").strip().lower()
        if align in ("left", "center", "right", "justify"):
            parts.append(f"text-align:{align}")
        if "margin-left" in css:
            pts = _points(css["margin-left"])
            level = 0 if pts is None else round(min(pts, 36 * _MAX_INDENT) / 36)
            if level >= 1:
                parts.append(f"margin-left:{36 * level}pt")
        if (css.get("page-break-before") or css.get("break-before") or "").lower() in (
            "always",
            "page",
        ):
            parts.append("page-break-before:always")
        return f' style="{";".join(parts)}"' if parts else ""

    def blocks(self, node: etree._Element, out: list[str]) -> None:
        """Emit the content of a block container; loose inline content becomes paragraphs."""
        run: list[_Token] = []

        def flush() -> None:
            if any(k == "V" or (k == "T" and t.strip(" \t\r\n\f")) for k, t in run):
                out.append(f"<p>{_finalise(run)}</p>")
            run.clear()

        self._text(node.text, run)
        for child in node:
            tag = _tag(child)
            if tag in _BLOCK:
                flush()
                self._block(child, tag, out)
            else:
                self._inline(child, run)
            self._text(child.tail, run)
        flush()

    def _leaf(self, tag: str, node: etree._Element) -> str:
        toks: list[_Token] = []
        self._inline_children(node, toks)
        style = self._heading_style(node) if tag in _HEADINGS else self._block_style(node)
        return f"<{tag}{style}>{_finalise(toks)}</{tag}>"

    @classmethod
    def _heading_style(cls, node: etree._Element) -> str:
        """A heading's style; without its own alignment it takes that of the first block inside
        it that has one (``<h1><p align="center">``: the paragraph is part of the heading)."""
        style = cls._block_style(node)
        if "text-align:" in style:
            return style
        for child in node:
            if _tag(child) not in _BLOCK:
                continue
            m = re.search(r"text-align:[a-z]+", cls._block_style(child))
            if m:
                return f' style="{";".join([m.group(0)] + ([style[8:-1]] if style else []))}"'
        return style

    def _block(self, node: etree._Element, tag: str, out: list[str]) -> None:
        if tag == "p":
            out.append(self._leaf("p", node))
        elif tag in _HEADINGS:
            out.append(self._leaf(_HEADINGS[tag], node))
        elif tag == "pre":
            toks: list[_Token] = []
            self._inline_children(node, toks)
            out.append(f"<pre>{_finalise(toks, pre=True)}</pre>")
        elif tag == "blockquote":
            inner: list[str] = []
            self.blocks(node, inner)
            out.append(f"<blockquote>{''.join(inner)}</blockquote>")
        elif tag in _LISTS:
            out.append(self._list(node, tag))
        elif tag == "table":
            self._table(node, out)
        elif tag == "hr":
            out.append("<hr>")
        elif tag == "caption":
            out.append(self._leaf("p", node))
        elif tag in ("body", "html") or any(_tag(c) in _BLOCK for c in node):
            self.blocks(node, out)
        else:  # div, section, ...: a paragraph
            out.append(self._leaf("p", node))

    def _list(self, node: etree._Element, tag: str) -> str:
        items: list[str] = []
        for child in node:
            ctag = _tag(child)
            if ctag is None or ctag in _DROP:
                continue
            if (
                ctag in _LISTS and items
            ):  # a list nested directly in a list belongs to the item above
                items[-1] = items[-1][: -len("</li>")] + self._list(child, ctag) + "</li>"
            else:
                items.extend(self._li(child))
        return f"<{tag}>{''.join(items)}</{tag}>"

    def _li(self, node: etree._Element) -> list[str]:
        """One item, or more when text follows a nested list: that text becomes the next item
        at the same level, so nothing moves before the sublist."""
        groups: list[tuple[list[_Token], list[str]]] = [([], [])]
        self._li_walk(node, groups)
        style = self._block_style(node)
        out = []
        for n, (toks, nested) in enumerate(groups):
            text = _finalise(toks, drop_trailing_br=True)
            if n == 0 or text or nested:
                out.append(f"<li{style}>{text}{''.join(nested)}</li>")
        return out

    @staticmethod
    def _add(groups: list[tuple[list[_Token], list[str]]], new: list[_Token]) -> None:
        """Add inline tokens to the item being built (a new item after a nested list)."""
        if groups[-1][1]:
            if all(k == "T" and not t.strip(" \t\r\n\f") for k, t in new):
                return  # white space between a sublist and what follows
            groups.append(([], []))
        groups[-1][0].extend(new)

    @staticmethod
    def _break_line(group: tuple[list[_Token], list[str]]) -> None:
        """End the item's current line where a paragraph starts or ends. White space before
        the break is source formatting: dropped, so a blank span, "a <p>b" or "<b> </b><p>"
        leaves no space and no empty wrapper."""
        toks, nested = group
        if nested:
            return
        i = len(toks)
        while i > 0:
            kind, text = toks[i - 1]
            if kind == "C":
                i -= 1
            elif kind == "T":
                text = _WS.sub(" ", text).rstrip(" ")
                if text:
                    toks[i - 1] = (kind, text)
                    break
                del toks[i - 1]
                i -= 1
            elif kind == "O" and i < len(toks) and toks[i][0] == "C":
                del toks[i - 1 : i + 1]  # an inline wrapper left empty
                i -= 1
            else:
                break
        if toks and toks[-1] != _BR:
            toks.append(_BR)

    def _li_walk(self, node: etree._Element, groups: list[tuple[list[_Token], list[str]]]) -> None:
        """Flatten a list item: its paragraphs become lines, nested lists stay lists."""
        self._li_text(groups, node.text, node[0] if len(node) else None)
        for child in node:
            tag = _tag(child)
            if tag in _LISTS:
                groups[-1][1].append(self._list(child, tag))
            elif tag in _BLOCK:
                self._break_line(groups[-1])
                self._li_walk(child, groups)
                self._break_line(groups[-1])
            elif tag is not None:
                new: list[_Token] = []
                self._inline(child, new)
                if new:
                    self._add(groups, new)
            self._li_text(groups, child.tail, child.getnext())

    def _li_text(
        self,
        groups: list[tuple[list[_Token], list[str]]],
        text: str | None,
        following: etree._Element | None,
    ) -> None:
        """Text inside an item. White space beside a block (the newlines of a loose list) is
        dropped, so a paragraph never starts or doubles a line break."""
        if not text:
            return
        if not text.strip(" \t\r\n\f"):
            toks = groups[-1][0]
            if not toks or toks[-1] == _BR or (following is not None and _tag(following) in _BLOCK):
                return
        self._add(groups, [("T", text)])

    def _table(self, node: etree._Element, out: list[str]) -> None:
        rows: list[list[etree._Element]] = []
        captions: list[etree._Element] = []

        def collect(parent: etree._Element) -> None:
            loose: list[etree._Element] | None = None  # cells outside a row (libxml2 leaves
            for c in parent:  # them there; HTML5 puts them in a new row)
                t = _tag(c)
                if t in ("td", "th"):
                    if loose is None:
                        loose = []
                        rows.append(loose)
                    loose.append(c)
                    continue
                loose = None
                if t == "tr":
                    rows.append(list(c))
                elif t in ("thead", "tbody", "tfoot"):
                    collect(c)
                elif t == "caption":
                    captions.append(c)

        collect(node)
        for cap in captions:
            out.append(self._leaf("p", cap))
        html_rows = []
        for tr in rows:
            cells = []
            for td in tr:
                if _tag(td) not in ("td", "th"):
                    continue
                span = ""
                for name in ("colspan", "rowspan"):
                    m = re.fullmatch(r"\s*(\d+)\s*", td.get(name) or "")
                    if m and 1 < int(m.group(1)) <= 1000:
                        span += f' {name}="{int(m.group(1))}"'
                inner: list[str] = []
                self.blocks(td, inner)
                cells.append(f"<td{span}>{''.join(inner) or '<p></p>'}</td>")
            if cells:
                html_rows.append(f"<tr>{''.join(cells)}</tr>")
        if html_rows:
            css = _parse_style(node.get("style"))
            border = (css.get("border") or css.get("border-style") or "").lower()
            plain = border in ("none", "0", "0px", "hidden") or (node.get("border") or "") == "0"
            tag = '<table style="border:none">' if plain else "<table>"
            out.append(f"{tag}{''.join(html_rows)}</table>")


_UNREADABLE = "This file couldn't be read as HTML."


def _parse(html: str) -> etree._Element | None:
    html = html5_text(_XML_DECL.sub("", html))
    if not html.strip():
        return None
    try:
        return lxml.html.fragment_fromstring(
            html, create_parent="div", parser=lxml.html.HTMLParser(huge_tree=True)
        )
    except (AssertionError, IndexError):  # a page without a <body>: nothing to show
        if _page(html) is None:
            raise ConversionError(_UNREADABLE, details="no body") from None
        return lxml.html.Element("div")
    except (etree.LxmlError, ValueError) as exc:
        raise ConversionError(_UNREADABLE, details=str(exc)) from exc


def _page(html: str) -> etree._Element | None:
    """The whole page, <head> included (the fragment parser drops it)."""
    try:
        return etree.fromstring(
            html5_text(_XML_DECL.sub("", html)), lxml.html.HTMLParser(huge_tree=True)
        )
    except (etree.LxmlError, ValueError):
        return None


def _is_language_class(value: str) -> bool:
    """markdown-it marks fenced code with class="language-x": not the page's styling."""
    return all(c.startswith("language-") for c in value.split())


def _lost_parts(html: str) -> list[str]:
    """What normalising ``html`` drops that a reader would miss (warnings, in a fixed order)."""
    page = _page(html)
    found: set[str] = set()
    for el in page.iter() if page is not None else ():
        tag = _tag(el)
        if tag is None:
            continue
        rel = (el.get("rel") or "").lower()
        if tag in ("style", "script") or (tag == "link" and "stylesheet" in rel):
            found.add(W_PAGESTYLE)
        elif tag in _RAW_DROPPED:
            found.add(W_RAWHTML)
        elif tag == "img":
            parsed = _urlparse((el.get("src") or "").strip())
            # A URL that can't be split has a broken host part: a web picture, dropped.
            if parsed is None or parsed.scheme.lower() in ("http", "https") or parsed.netloc:
                found.add(W_WEBPICS)
        if (el.get("class") and not _is_language_class(el.get("class") or "")) or any(
            k.lower().startswith("on") for k in el.attrib
        ):
            found.add(W_PAGESTYLE)
    return [w for w in (W_PAGESTYLE, W_WEBPICS, W_RAWHTML) if w in found]


def html_title(html: str) -> str:
    """The page's <title>, with its white space collapsed ("" if there is none)."""
    page = _page(html)
    title = page.find(".//title") if page is not None else None
    return " ".join("".join(title.itertext()).split()) if title is not None else ""


def normalise_html(html: str, base_dir: Path | None = None) -> tuple[str, DocImages]:
    """Rewrite ``html`` into the editor's subset; returns (fragment, pictures).

    ``base_dir`` is where relative picture paths are read from (never outside it).
    """
    out, images, _ = normalise_html_report(html, base_dir, report=False)
    return out, images


def normalise_html_report(
    html: str, base_dir: Path | None = None, *, report: bool = True
) -> tuple[str, DocImages, list[str]]:
    """Like ``normalise_html``, plus the warnings for what was dropped (W_PAGESTYLE, ...)."""
    root = _parse(html)
    if root is None:
        return "", {}, []
    warnings = _lost_parts(html) if report else []
    reserved = {
        src for img in root.iter("img") if _CANON_IMG.match(src := (img.get("src") or "").strip())
    }
    norm = _Normaliser(base_dir, reserved)
    out: list[str] = []
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 20000))  # deeply nested pages recurse deeply
    try:
        norm.blocks(root, out)
    except RecursionError as exc:
        raise ConversionError(_UNREADABLE, details="nesting too deep") from exc
    finally:
        sys.setrecursionlimit(old_limit)
    if report and norm.lost_pictures:
        warnings.append(W_PICTURES)
    return "".join(out), norm.images, warnings


def text_length(html: str) -> int:
    """Number of characters of visible text in ``html``."""
    root = _parse(html)
    if root is None:
        return 0
    etree.strip_elements(root, *_DROP, etree.Comment, etree.ProcessingInstruction, with_tail=False)
    return len("".join(root.itertext()))
