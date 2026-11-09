"""Data formats: JSON ↔ YAML ↔ XML (trees) ↔ CSV / TSV / XLSX (tables).

Everything is read into plain Python values (dicts, lists, scalars) and written back out.
Tables become a list of row dicts; nested data is flattened into ``a.b`` columns and rebuilt
on the way back.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt

DATA_READ = {"json", "yaml", "xml", "csv", "tsv", "xlsx"}
TARGETS = {"json": "JSON", "yaml": "YAML", "xml": "XML", "csv": "CSV", "tsv": "TSV", "xlsx": "XLSX"}
TABLES = ("data:csv", "data:tsv", "data:xlsx")
TREES = ("data:json", "data:yaml", "data:xml")


@dataclass
class DataOptions:
    indent: int = opt(2, "Indentation", choices=[(2, "2 spaces"), (4, "4 spaces"), (0, "Minified")],
                      targets=("data:json", "data:yaml", "data:xml"))  # fmt: skip
    delimiter: str = opt(",", "Separator", choices=[(",", "Comma ,"), (";", "Semicolon ;"),
                         ("|", "Pipe |")], targets=("data:csv",))  # fmt: skip
    excel_bom: bool = opt(
        False,
        "Excel-friendly (UTF-8 with BOM)",
        targets=("data:csv", "data:tsv"),
        help="Makes Excel show accents and other special characters correctly.",
    )
    flatten: bool = opt(True, "Flatten nested data into columns (a.b)", targets=TABLES)
    unflatten: bool = opt(True, "Rebuild nesting from column names like a.b", targets=TREES)
    xml_root: str = opt("root", "XML root element", targets=("data:xml",))
    xml_item: str = opt("item", "XML element for list items", targets=("data:xml",))
    header: bool = opt(
        True,
        "Tables I'm converting have a header row",
        help="For CSV/TSV/XLSX input. Off = columns are named column1, column2…",
    )
    detect_numbers: bool = opt(True, "Turn numbers and true/false in CSV into real values")
    sheets: str = opt("first", "Spreadsheet sheets", choices=[("first", "First sheet only"),
                      ("all", "All sheets")], help="For XLSX input.")  # fmt: skip


# --------------------------------------------------------------------------- helpers


def _scalar(text: str) -> Any:
    t = text.strip()
    if t in ("true", "false"):
        return t == "true"
    if re.fullmatch(r"-?(0|[1-9]\d*)", t):
        return int(t)
    if re.fullmatch(r"-?(0|[1-9]\d*)?\.\d+([eE][-+]?\d+)?|-?(0|[1-9]\d*)[eE][-+]?\d+", t):
        return float(t)
    return text


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


def flatten(row: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(row, dict):
        for k, v in row.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(flatten(v, key))
            elif isinstance(v, list):
                out[key] = json.dumps(_jsonable(v), ensure_ascii=False)
            else:
                out[key] = v
    else:
        out[prefix or "value"] = row
    return out


def unflatten(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        parts = str(key).split(".")
        node = out
        for p in parts[:-1]:
            nxt = node.get(p)
            if not isinstance(nxt, dict):
                nxt = node[p] = {}
            node = nxt
        node[parts[-1]] = value
    return out


def as_table(value: Any) -> list[dict[str, Any]]:
    """Find the table inside a tree: follow single-key wrappers (``{"library": {"book": [...]}}``)
    down to a list of rows; a dict holding exactly one list uses that list."""
    while isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        if len(lists) == 1:
            value = lists[0]
        elif len(value) == 1 and isinstance(next(iter(value.values())), dict):
            value = next(iter(value.values()))
        else:
            value = [value]
    if not isinstance(value, list):
        value = [value]
    return [r if isinstance(r, dict) else {"value": r} for r in value]


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    cols: dict[str, None] = {}
    for r in rows:
        for k in r:
            cols.setdefault(k, None)
    return list(cols)


# --------------------------------------------------------------------------- XML

_XML_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _xml_name(key: str) -> str:
    name = _XML_NAME.sub("_", str(key)) or "_"
    return f"_{name}" if not re.match(r"[A-Za-z_]", name) else name


def xml_to_tree(el: ET.Element) -> Any:
    node: dict[str, Any] = {f"@{k}": v for k, v in el.attrib.items()}
    for child in el:
        value = xml_to_tree(child)
        if child.tag in node:
            if not isinstance(node[child.tag], list):
                node[child.tag] = [node[child.tag]]
            node[child.tag].append(value)
        else:
            node[child.tag] = value
    text = (el.text or "").strip()
    if not node:
        return text
    if text:
        node["#text"] = text
    return node


def tree_to_xml(parent: ET.Element, value: Any, item: str) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "#text":
                parent.text = "" if v is None else str(v)
            elif str(k).startswith("@"):
                parent.set(_xml_name(str(k)[1:]), "" if v is None else str(v))
            elif isinstance(v, list):
                for x in v:
                    tree_to_xml(ET.SubElement(parent, _xml_name(k)), x, item)
            else:
                tree_to_xml(ET.SubElement(parent, _xml_name(k)), v, item)
    elif isinstance(value, list):
        for x in value:
            tree_to_xml(ET.SubElement(parent, _xml_name(item)), x, item)
    elif value is not None:
        parent.text = str(value).lower() if isinstance(value, bool) else str(value)


# --------------------------------------------------------------------------- read / write


def read(path: Path, ext: str, o: DataOptions) -> Any:
    try:
        if ext == "json":
            return json.loads(path.read_text(encoding="utf-8-sig"))
        if ext == "yaml":
            import yaml

            return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if ext == "xml":
            root = ET.parse(path).getroot()
            return {root.tag: xml_to_tree(root)}
        if ext in ("csv", "tsv"):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            delim = "\t" if ext == "tsv" else csv.Sniffer().sniff(text[:4096], ",;|\t").delimiter
            rows = list(csv.reader(io.StringIO(text), delimiter=delim))
            return _rows_to_records(rows, o, typed=False)
        if ext == "xlsx":
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)
            sheets = {ws.title: _rows_to_records(
                [list(r) for r in ws.iter_rows(values_only=True)], o, typed=True)
                for ws in (wb.worksheets if o.sheets == "all" else wb.worksheets[:1])}  # fmt: skip
            return sheets if o.sheets == "all" else next(iter(sheets.values()), [])
    except (ValueError, ET.ParseError, csv.Error) as exc:
        raise ConversionError(f"“{path.name}” isn't valid {ext.upper()}: {exc}") from exc
    except Exception as exc:
        if exc.__class__.__name__ in (
            "YAMLError",
            "ScannerError",
            "ParserError",
            "InvalidFileException",
        ):
            raise ConversionError(f"“{path.name}” isn't valid {ext.upper()}.") from exc
        raise
    raise ConversionError(f"Can't read {ext.upper()} files as data.")


def _rows_to_records(rows: list[list[Any]], o: DataOptions, typed: bool) -> list[dict[str, Any]]:
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    if o.header:
        head = [str(h) if h not in (None, "") else f"column{i + 1}" for i, h in enumerate(rows[0])]
        head += [f"column{i + 1}" for i in range(len(head), width)]
        body = rows[1:]
    else:
        head = [f"column{i + 1}" for i in range(width)]
        body = rows
    out = []
    for r in body:
        cells = list(r) + [None] * (width - len(r))
        if not typed and o.detect_numbers:
            cells = [_scalar(c) if isinstance(c, str) else c for c in cells]
        out.append(dict(zip(head, cells, strict=False)))
    return out


def write(value: Any, ext: str, o: DataOptions) -> bytes:
    value = _jsonable(value)
    if ext == "json":
        return json.dumps(value, indent=o.indent or None, ensure_ascii=False).encode() + b"\n"
    if ext == "yaml":
        import yaml

        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True,
                              indent=o.indent or 2).encode()  # fmt: skip
    if ext == "xml":
        if (
            isinstance(value, dict)
            and len(value) == 1
            and not isinstance(next(iter(value.values())), list)
        ):
            ((root_name, body),) = value.items()
        else:
            root_name, body = o.xml_root or "root", value
        root = ET.Element(_xml_name(root_name))
        tree_to_xml(root, body, o.xml_item or "item")
        if o.indent:
            ET.indent(root, space=" " * o.indent)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
    # tables
    if (
        ext == "xlsx"
        and isinstance(value, dict)
        and value
        and all(isinstance(v, list) for v in value.values())
    ):
        sheets = {name: as_table(rows) for name, rows in value.items()}  # one sheet per key
    else:
        sheets = {"Sheet1": as_table(value)}
    if o.flatten:
        sheets = {n: [flatten(r) for r in rows] for n, rows in sheets.items()}
    sheets = {n: [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v)
                   for k, v in r.items()} for r in rows] for n, rows in sheets.items()}  # fmt: skip
    if ext == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(re.sub(r"[\\/*?:\[\]]", "_", str(name))[:31] or "Sheet")
            cols = _columns(rows)
            ws.append(cols)
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in rows:
                ws.append([r.get(c) for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    rows = next(iter(sheets.values()))
    cols = _columns(rows)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t" if ext == "tsv" else o.delimiter, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else str(r.get(c)).lower() if isinstance(r.get(c), bool)
                    else r.get(c) for c in cols])  # fmt: skip
    return buf.getvalue().encode("utf-8-sig" if o.excel_bom else "utf-8")


class DataConverter(Converter):
    family = MediaFamily.DOCUMENT
    options_cls = DataOptions

    def targets(self, files: list[DetectedFile]):
        if not files or not all(f.ext in DATA_READ for f in files):
            return []
        single = files[0].ext if len(files) == 1 else None
        return [self.target(f"data:{ext}", label, ext, "Data") for ext, label in TARGETS.items()
                if ext != single]  # fmt: skip

    def run(self, job: ConversionJob) -> list[Path]:
        o: DataOptions = job.options
        ext = job.target.ext
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Converting {f.name} ({i + 1}/{n})")
            value = read(f.path, f.ext, o)
            if ext in ("json", "yaml", "xml") and f.ext in ("csv", "tsv", "xlsx") and o.unflatten:
                value = ({k: [unflatten(r) for r in v] for k, v in value.items()}
                         if isinstance(value, dict) else [unflatten(r) for r in value])  # fmt: skip
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, ext)
            dest.write_bytes(write(value, ext, o))
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
