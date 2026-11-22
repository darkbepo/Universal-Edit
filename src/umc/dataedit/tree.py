"""The table inside a tree of values, and putting it back (port: web/src/dataedit/tree.ts).

Found like the data converter's ``as_table``: single-key wrappers are followed down to the one
list (``{"library": {"book": [...]}}``); a dict without one list is a single row, a lone value
a single "value" cell. Rows are flattened into ``a.b`` columns; lists and empty objects stay
one ``Raw`` cell. Everything around the table is kept when it is put back."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from umc.dataedit import jsontree
from umc.dataedit.cells import MISSING, Cell, Raw

VALUE = "value"


@dataclass(frozen=True)
class Located:
    path: tuple[str, ...]  # dict keys from the root to the table
    kind: Literal["list", "object", "value"]
    plain: bool  # a list of plain values (no dicts): one "value" column


def _at(root: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        root = root[key]
    return root


def locate(root: Any) -> Located:
    path: list[str] = []
    value = root
    while isinstance(value, dict):
        lists = [k for k, v in value.items() if isinstance(v, list)]
        if len(lists) == 1:
            path.append(lists[0])
            value = value[lists[0]]
        elif len(value) == 1 and isinstance(next(iter(value.values())), dict):
            key = next(iter(value))
            path.append(key)
            value = value[key]
        else:
            return Located(tuple(path), "object", False)
    if isinstance(value, list):
        plain = bool(value) and not any(isinstance(x, dict) for x in value)
        return Located(tuple(path), "list", plain)
    return Located(tuple(path), "value", False)


def cell_of(value: Any) -> Cell:
    if isinstance(value, list | dict):
        return Raw(jsontree.dumps(value, None))
    return value


def value_of(cell: Cell) -> Any:
    if cell is MISSING:
        return None
    if isinstance(cell, Raw):
        return jsontree.parse(cell.text)
    return cell


def flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Cell]:
    out: dict[str, Cell] = {}
    for k, v in row.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            out.update(flatten(v, key))
        else:
            out[key] = cell_of(v)
    return out


def unflatten(columns: tuple[str, ...], row: tuple[Cell, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, cell in zip(columns, row, strict=True):
        if cell is MISSING:
            continue
        *parents, last = name.split(".")
        node = out
        for p in parents:
            nxt = node.get(p)
            if not isinstance(nxt, dict):
                nxt = node[p] = {}
            node = nxt
        node[last] = value_of(cell)
    return out


def table_of(root: Any, loc: Located) -> tuple[tuple[str, ...], tuple[tuple[Cell, ...], ...]]:
    value = _at(root, loc.path)
    if loc.kind == "list":
        records = [flatten(x) if isinstance(x, dict) else {VALUE: cell_of(x)} for x in value]
    elif loc.kind == "object":
        records = [flatten(value)]
    else:
        records = [{VALUE: cell_of(value)}]
    columns: dict[str, None] = {}
    for rec in records:
        for k in rec:
            columns.setdefault(k, None)
    names = tuple(columns)
    return names, tuple(tuple(rec.get(c, MISSING) for c in names) for rec in records)


def _set_at(root: Any, path: tuple[str, ...], value: Any) -> Any:
    if not path:
        return value
    out = dict(root)
    out[path[0]] = _set_at(root[path[0]], path[1:], value)
    return out


def put(
    root: Any, loc: Located, columns: tuple[str, ...], rows: tuple[tuple[Cell, ...], ...]
) -> Any:
    """The root with the edited table in its place."""
    if columns == (VALUE,) and (loc.plain or loc.kind == "value"):
        items = [value_of(r[0]) for r in rows]
    else:
        items = [unflatten(columns, r) for r in rows]
    if loc.kind == "list":
        table: Any = items
    elif len(items) == 1:
        table = items[0]
    elif not items:
        table = {} if loc.kind == "object" else None
    else:
        table = items
    return _set_at(root, loc.path, table)
