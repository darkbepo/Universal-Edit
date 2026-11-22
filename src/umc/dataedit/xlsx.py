"""Excel workbooks (.xlsx) ↔ sheets (port: web/src/dataedit/xlsx.ts).

Our own reader (zip + a few regular expressions, the same in both apps), so the editor sees the
same cells everywhere: numbers keep the spelling Excel stored, shared / inline strings,
booleans, formula results (the cached value) and date-formatted numbers as ISO text
("2024-01-31", "2024-01-31 08:30:00").

Saving rewrites only the ``<sheetData>`` of the sheets that changed; every other part (styles,
column widths, merged cells, charts, pictures …) is copied as it was. A cell that still holds
what the file had at that place is written back exactly as it was (formula, style and all);
a changed one keeps the style of its place, and ISO text typed where a date was is stored as a
date again. When anything changed, ``calcChain.xml`` is dropped (Excel rebuilds it)."""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from dataclasses import dataclass

from umc.dataedit.cells import NUMBER, Cell, Num, Raw, display
from umc.dataedit.table import Sheet, letter
from umc.dataedit.xmltree import unescape

MAX_UNPACKED = 200 * 1024 * 1024  # a bigger workbook is refused (zip bombs)
MAX_DAYS = 2958465  # 9999-12-31


class XlsxError(ValueError):
    """Why a workbook can't be read; the message is shown as it is."""


@dataclass(frozen=True)
class Original:
    cell: Cell
    xml: str  # the <c> element exactly as written
    style: str  # its s="…" value ("" = none)


@dataclass(frozen=True)
class SheetPart:
    path: str  # inside the zip
    xml: str  # the whole sheet part
    data: tuple[int, int]  # where <sheetData …> … </sheetData> sits in it
    prefix: str  # "x:" when the file writes its elements with a prefix
    row_attrs: dict[int, str]  # row number → its other attributes (height, style …)
    cells: dict[tuple[int, int], Original]  # (row number, column index) → what was there


@dataclass(frozen=True)
class Book:
    entries: tuple[tuple[str, bytes], ...]  # every zip entry, in order
    parts: tuple[SheetPart, ...]
    sheets: tuple[Sheet, ...]  # as read
    dates: tuple[str, ...]  # per cell style index: "" / "date" / "time"
    date1904: bool


# ---------------------------------------------------------------------------- dates
_BUILTIN_DATES = {
    14: "date",
    15: "date",
    16: "date",
    17: "date",
    18: "time",
    19: "time",
    20: "time",
    21: "time",
    22: "date",
    45: "time",
    46: "time",
    47: "time",
}


def format_kind(code: str) -> str:
    """ "date", "time" (no day in it) or "" for a number format code."""
    bare = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).lower()
    if not re.search(r"[dmyhs]", bare) or bare in ("general",):
        return ""
    return "date" if re.search(r"[dy]", bare) else "time"


def _base(date1904: bool) -> dt.date:
    return dt.date(1904, 1, 1) if date1904 else dt.date(1899, 12, 31)


def serial_text(text: str, kind: str, date1904: bool) -> str | None:
    """A date cell's ISO text, or None when the number isn't a date we can show."""
    value = float(text)
    if value < 0:
        return None
    seconds = round(value * 86400)
    days, secs = divmod(seconds, 86400)
    if days > MAX_DAYS - (1462 if date1904 else 0):
        return None
    clock = f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}"
    if kind == "time" and days == 0:
        return clock
    if not date1904 and days >= 61:  # Excel counts a 29 February 1900 that never was
        days -= 1
    day = (_base(date1904) + dt.timedelta(days=days)).isoformat()
    return day if secs == 0 else f"{day} {clock}"


_ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?)?")
_CLOCK = re.compile(r"([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?")


def text_serial(text: str, date1904: bool) -> str | None:
    """ISO text typed into a date cell → the number Excel stores (None: not a date)."""
    m = _ISO.fullmatch(text)
    if m:
        try:
            day = dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
        days = (day - _base(date1904)).days
        if days < (0 if date1904 else 1):
            return None
        if not date1904 and days >= 60:
            days += 1
        clock = m.groups()[3:]
    else:
        m = _CLOCK.fullmatch(text)
        if not m:
            return None
        days, clock = 0, m.groups()
    hh, mm, ss = (int(x) if x else 0 for x in clock)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    secs = hh * 3600 + mm * 60 + ss
    if secs == 0:
        return str(days)
    return f"{days + secs / 86400:.10f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------- reading
def _tag(name: str) -> str:
    return rf"<(?:[\w.-]+:)?{name}\b"


def _end(name: str) -> str:
    return rf"</(?:[\w.-]+:)?{name}\s*>"


_ATTR = re.compile(r"([\w.:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_SHEET_DATA = re.compile(_tag("sheetData") + r"[^>]*?(?:/>|>.*?" + _end("sheetData") + ")", re.S)
_ROW = re.compile(_tag("row") + r"([^>]*?)(?:/>|>(.*?)" + _end("row") + ")", re.S)
_CELL = re.compile(_tag("c") + r"([^>]*?)(?:/>|>(.*?)" + _end("c") + ")", re.S)
_V = re.compile(_tag("v") + r"[^>]*>(.*?)" + _end("v"), re.S)
_IS = re.compile(_tag("is") + r"[^>]*>(.*?)" + _end("is"), re.S)
_T = re.compile(_tag("t") + r"[^>]*?(?:/>|>(.*?)" + _end("t") + ")", re.S)
_RPH = re.compile(_tag("rPh") + r".*?" + _end("rPh"), re.S)
_SI = re.compile(_tag("si") + r"[^>]*?(?:/>|>(.*?)" + _end("si") + ")", re.S)
_NUMFMT = re.compile(_tag("numFmt") + r"([^>]*?)/?>", re.S)
_CELL_XFS = re.compile(_tag("cellXfs") + r"[^>]*>(.*?)" + _end("cellXfs"), re.S)
_XF = re.compile(_tag("xf") + r"([^>]*?)/?>", re.S)
_SHEET = re.compile(_tag("sheet") + r"([^>]*?)/?>", re.S)
_REL = re.compile(_tag("Relationship") + r"([^>]*?)/?>", re.S)
_PR = re.compile(_tag("workbookPr") + r"([^>]*?)/?>", re.S)
_ESCAPED = re.compile(r"_x([0-9A-Fa-f]{4})_")
_REF = re.compile(r"([A-Za-z]{1,3})([0-9]+)")


def _int(text: str) -> int | None:
    """Plain 0-9 digits as a number (str.isdigit also takes other scripts' digits)."""
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def attrs(text: str) -> dict[str, str]:
    return {m[1]: m[2] if m[2] is not None else m[3] for m in _ATTR.finditer(text)}


def _text(xml: str) -> str:
    """The text of <t> runs (phonetic guides left out), XML and Excel escapes undone."""
    xml = _RPH.sub("", xml)
    raw = "".join(unescape(m[1] or "") for m in _T.finditer(xml))
    return _ESCAPED.sub(lambda m: chr(int(m[1], 16)), raw)


def column_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _resolve(target: str) -> str:
    if target.startswith("/"):
        return target[1:]
    parts: list[str] = ["xl"]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece not in ("", "."):
            parts.append(piece)
    return "/".join(parts)


def _part(entries: dict[str, bytes], name: str) -> str:
    data = entries.get(name)
    return data.decode("utf-8-sig", errors="replace") if data is not None else ""


def read(data: bytes) -> Book:
    """Raises XlsxError with a message for the user."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise XlsxError("This workbook unpacks to more than 200 MB: too big for the editor.")
        entries = {i.filename: zf.read(i) for i in infos if not i.is_dir()}
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        if isinstance(exc, XlsxError):
            raise
        raise XlsxError("This isn't a valid Excel (XLSX) file.") from exc
    workbook = _part(entries, "xl/workbook.xml")
    if not workbook:
        raise XlsxError("This isn't a valid Excel (XLSX) file.")
    shared = [_text(m[1] or "") for m in _SI.finditer(_part(entries, "xl/sharedStrings.xml"))]
    styles = _part(entries, "xl/styles.xml")
    formats = {
        _int(a.get("numFmtId", "")): format_kind(unescape(a.get("formatCode", "")))
        for a in (attrs(m[1]) for m in _NUMFMT.finditer(styles))
    }
    xfs = _CELL_XFS.search(styles)
    dates = tuple(
        formats.get(n, _BUILTIN_DATES.get(n, ""))
        if (n := _int(attrs(m[1]).get("numFmtId", "0")) or 0)
        else ""
        for m in _XF.finditer(xfs[1] if xfs else "")
    )
    pr = _PR.search(workbook)
    date1904 = bool(pr) and attrs(pr[1]).get("date1904", "") in ("1", "true")
    rels = {
        a.get("Id", ""): a.get("Target", "")
        for a in (attrs(m[1]) for m in _REL.finditer(_part(entries, "xl/_rels/workbook.xml.rels")))
    }
    parts, sheets = [], []
    for m in _SHEET.finditer(workbook):
        a = attrs(m[1])
        rid = next((v for k, v in a.items() if k.endswith(":id")), "")
        path = _resolve(rels.get(rid, ""))
        if path not in entries:
            continue
        part, sheet = _read_sheet(
            path, _part(entries, path), unescape(a.get("name", "Sheet")), shared, dates, date1904
        )
        parts.append(part)
        sheets.append(sheet)
    if not sheets:
        raise XlsxError("This workbook has no sheets the editor can show.")
    return Book(tuple(entries.items()), tuple(parts), tuple(sheets), dates, date1904)


def _style_kind(style: str, dates: tuple[str, ...]) -> str:
    n = _int(style)
    return dates[n] if n is not None and n < len(dates) else ""


def _value(a: dict[str, str], body: str, shared: list[str], kind: str, date1904: bool) -> Cell:
    t = a.get("t", "n")
    v = _V.search(body)
    raw = unescape(v[1]) if v else None
    if t == "s":
        index = _int((raw or "").strip())
        return shared[index] if index is not None and index < len(shared) else ""
    if t == "inlineStr":
        m = _IS.search(body)
        return _text(m[1]) if m else ""
    if t == "b":
        return None if raw is None else raw.strip() == "1"
    if t in ("str", "e", "d"):
        return _ESCAPED.sub(lambda m: chr(int(m[1], 16)), raw or "")
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if not NUMBER.fullmatch(text):
        return text
    if kind:
        shown = serial_text(text, kind, date1904)
        if shown is not None:
            return shown
    return Num(text)


def _read_sheet(path, xml, name, shared, dates, date1904) -> tuple[SheetPart, Sheet]:
    m = _SHEET_DATA.search(xml)
    span = (m.start(), m.end()) if m else (len(xml), len(xml))
    prefix = ""
    if m:
        p = re.match(r"<([\w.-]+:)?", m[0])
        prefix = (p[1] or "") if p else ""
    rows: dict[int, dict[int, Cell]] = {}
    row_attrs: dict[int, str] = {}
    originals: dict[tuple[int, int], Original] = {}
    r = 0
    for rm in _ROW.finditer(m[0] if m else ""):
        ra = attrs(rm[1])
        r = _int(ra.get("r", "")) or r + 1
        kept = "".join(f' {k}="{v}"' for k, v in ra.items() if k not in ("r", "spans"))
        if kept:
            row_attrs[r] = kept
        c = -1
        for cm in _CELL.finditer(rm[2] or ""):
            ca = attrs(cm[1])
            ref = _REF.fullmatch(ca.get("r", ""))
            c = column_index(ref[1]) if ref else c + 1
            style = ca.get("s", "")
            kind = _style_kind(style, dates)
            cell = _value(ca, cm[2] or "", shared, kind, date1904)
            originals[(r, c)] = Original(cell, cm[0], style)
            rows.setdefault(r, {})[c] = cell
    height = max(rows, default=0)
    width = max((max(cs) + 1 for cs in rows.values() if cs), default=0)
    grid = tuple(tuple(rows.get(i, {}).get(j) for j in range(width)) for i in range(1, height + 1))
    columns = tuple(display(c) for c in grid[0]) if grid else ("",) * width
    sheet = Sheet(name, columns, grid[1:], typed=True, header=True)
    return SheetPart(path, xml, span, prefix, row_attrs, originals), sheet


# ---------------------------------------------------------------------------- writing
def _escape(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(
        r"[\x00-\x08\x0b-\x1f]|_(?=x[0-9A-Fa-f]{4}_)", lambda m: f"_x{ord(m[0]):04X}_", text
    )


def _new_cell(p: str, ref: str, cell: Cell, orig: Original | None, book: Book) -> str:
    style = orig.style if orig else ""
    s = f' s="{style}"' if style else ""
    if not isinstance(cell, str | bool | Num | Raw):  # None / MISSING: empty
        return f'<{p}c r="{ref}"{s}/>' if s else ""
    if isinstance(cell, bool):
        return f'<{p}c r="{ref}"{s} t="b"><{p}v>{int(cell)}</{p}v></{p}c>'
    if isinstance(cell, Num) and NUMBER.fullmatch(cell.text):
        return f'<{p}c r="{ref}"{s}><{p}v>{cell.text}</{p}v></{p}c>'
    text = display(cell)
    kind = _style_kind(style, book.dates)
    if kind:
        serial = text_serial(text, book.date1904)
        if serial is not None:
            return f'<{p}c r="{ref}"{s}><{p}v>{serial}</{p}v></{p}c>'
    if not text:
        return f'<{p}c r="{ref}"{s}/>' if s else ""
    return (
        f'<{p}c r="{ref}"{s} t="inlineStr"><{p}is><{p}t xml:space="preserve">'
        f"{_escape(text)}</{p}t></{p}is></{p}c>"
    )


def sheet_xml(part: SheetPart, sheet: Sheet, book: Book) -> str:
    """The sheet part with its cells rewritten from ``sheet``."""
    p = part.prefix
    grid: list[tuple[Cell, ...]] = list(sheet.rows)
    if sheet.header:
        grid.insert(0, tuple(name if name else None for name in sheet.columns))
    out: list[str] = []
    last_r, last_c = 0, -1
    for i, row in enumerate(grid):
        r = i + 1
        cells = []
        for c, cell in enumerate(row):
            orig = part.cells.get((r, c))
            if orig is not None and (
                orig.cell == cell or sheet.header and i == 0 and display(orig.cell) == display(cell)
            ):
                xml = orig.xml
            else:
                xml = _new_cell(p, f"{letter(c)}{r}", cell, orig, book)
            if xml:
                cells.append(xml)
                last_r, last_c = r, max(last_c, c)
        extra = part.row_attrs.get(r, "")
        if cells or extra:
            out.append(
                f'<{p}row r="{r}"{extra}>' + "".join(cells) + f"</{p}row>"
                if cells
                else f'<{p}row r="{r}"{extra}/>'
            )
    data = f"<{p}sheetData>" + "".join(out) + f"</{p}sheetData>" if out else f"<{p}sheetData/>"
    a, b = part.data
    xml = part.xml[:a] + data + part.xml[b:]
    ref = f"A1:{letter(last_c)}{last_r}" if last_c >= 0 and (last_r, last_c) != (1, 0) else "A1"
    return re.sub(
        r"(" + _tag("dimension") + r"[^>]*?\bref=)\"[^\"]*\"",
        lambda m: f'{m[1]}"{ref}"',
        xml,
        count=1,
    )


def _without_calc_chain(entries: dict[str, bytes]) -> None:
    if "xl/calcChain.xml" not in entries:
        return
    del entries["xl/calcChain.xml"]
    for name, pattern in (
        ("xl/_rels/workbook.xml.rels", _tag("Relationship") + r"[^>]*?calcChain[^>]*?/>"),
        ("[Content_Types].xml", _tag("Override") + r"[^>]*?/xl/calcChain\.xml[^>]*?/>"),
    ):
        if name in entries:
            entries[name] = re.sub(pattern, "", entries[name].decode("utf-8")).encode("utf-8")


def changed_parts(book: Book, sheets: tuple[Sheet, ...]) -> dict[str, bytes]:
    """Every zip entry after saving ``sheets`` (unchanged ones as they were)."""
    entries = dict(book.entries)
    changed = False
    for part, before, now in zip(book.parts, book.sheets, sheets, strict=True):
        if now != before:
            entries[part.path] = sheet_xml(part, now, book).encode("utf-8")
            changed = True
    if changed:
        _without_calc_chain(entries)
    return entries


def write(book: Book, sheets: tuple[Sheet, ...]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in changed_parts(book, sheets).items():
            zf.writestr(name, data)
    return buf.getvalue()
