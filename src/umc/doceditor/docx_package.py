"""Opening a DOCX package for the reader (no Qt): the zip-bomb check and the in-memory repair of
packages python-docx refuses. The website's `docx-package.ts` does the same for its own reader.
"""

from __future__ import annotations

import logging
import posixpath
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import docx
from lxml import etree

from umc.core.errors import ConversionError
from umc.doceditor.types import MAX_UNPACKED_BYTES, TOO_LARGE

log = logging.getLogger(__name__)

_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_PKG_OVERRIDE = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"


def _repaired(data: bytes) -> bytes | None:
    """The package with relationships to missing parts dropped and part names matched the way
    Word matches them (ignoring case, percent-encoding); None when nothing needed changing."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zin:
            members = {i.filename: zin.read(i) for i in zin.infolist() if not i.is_dir()}
    except Exception:
        return None
    lookup = {name.lower(): name for name in members}

    def actual(name: str) -> str | None:
        return name if name in members else lookup.get(unquote(name).lower())

    changed = False
    for name, blob in list(members.items()):
        if name == "[Content_Types].xml":
            tag, attr = _PKG_OVERRIDE, "PartName"
        elif name.endswith(".rels"):
            tag, attr = _PKG_REL, "Target"
        else:
            continue
        try:
            root = etree.fromstring(blob)
        except etree.XMLSyntaxError:
            continue
        source_dir = posixpath.dirname(posixpath.dirname(name))  # word/_rels/x.rels -> word
        touched = False
        for el in list(root.iter(tag)):
            target = el.get(attr) or ""
            if tag == _PKG_REL and el.get("TargetMode") == "External":
                continue
            if target.startswith("/"):
                full = target.lstrip("/")
            else:
                full = posixpath.normpath(posixpath.join(source_dir, target))
            found = actual(full)
            if found is None:
                if tag == _PKG_REL:  # dangling: python-docx would refuse the whole file
                    root.remove(el)
                    touched = True
            elif target != "/" + found:
                el.set(attr, "/" + found)
                touched = True
        if touched:
            members[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            changed = True
    if not changed:
        return None
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in members.items():
            zout.writestr(name, blob)
    return out.getvalue()


def _check_unpacked_size(data: bytes) -> None:
    """Refuse a package whose parts would unpack to more than MAX_UNPACKED_BYTES (a zip bomb).
    The sizes are the ones the zip declares: ``zipfile`` never inflates an entry past its
    declared size (such a part fails its CRC check), so lying headers are bounded too."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            total = sum(info.file_size for info in z.infolist())
    except Exception:
        return  # not a zip: python-docx says so
    if total > MAX_UNPACKED_BYTES:
        raise ConversionError(TOO_LARGE, details=f"{total} bytes unpacked")


def open_package(path: Path) -> Any:
    """``docx.Document`` for ``path``; a package python-docx refuses (a relationship to a part
    that isn't there, part names written in another case) is repaired in memory first."""
    data = Path(path).read_bytes()
    _check_unpacked_size(data)
    try:
        return docx.Document(BytesIO(data))
    except Exception as first:
        fixed = _repaired(data)
        if fixed is None:
            raise
        log.info("Opening %s after repairing its package (%s)", path, first)
        return docx.Document(BytesIO(fixed))
