"""Changing a document's details: title, subject, author and keywords.

PDF       the Info dictionary (Title / Subject / Author / Keywords), saved *incrementally*: the
          new values are appended after the original bytes, so signatures stay valid for what
          was signed. A changed field's XMP twin (dc:title, dc:description, dc:creator,
          pdf:Keywords) is removed so an older value can't win. A password-locked PDF needs its
          password. PyMuPDF through ``tools/pdf.py``.
Office    DOCX / XLSX / PPTX (+ macro and template kinds): ``docProps/core.xml`` (dc:title,
          dc:subject, dc:creator, cp:keywords); made, with its content type and relationship,
          when the file has none.
ODF       ODT / ODS / ODP / ODG: ``meta.xml`` (dc:title, dc:subject, meta:initial-creator, one
          meta:keyword per keyword); made, with its manifest entry, when missing.

Every other part of the zip is copied as it is (same order and compression; ODF's ``mimetype``
stays first and stored). The XML is changed as text — only the touched elements change — by
``set_text`` / ``core_xml`` / ``odf_meta``, ported line by line to
``web/src/properties/document.ts``; both suites check ``tests/fixtures/document_cases.json``
(same output bytes)."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from umc.properties import saving
from umc.properties.exif import WS, well_formed

PDF_EXTS = {"pdf"}
OFFICE_EXTS = {"docx", "docm", "dotx", "xlsx", "xlsm", "pptx", "pptm", "ppsx"}
ODF_EXTS = {"odt", "ods", "odp", "odg"}
WRITABLE_EXTS = PDF_EXTS | OFFICE_EXTS | ODF_EXTS
FIELDS = ("title", "subject", "author", "keywords")
MAX_CHARS = 2000
MAX_PART_BYTES = 10 * 1024 * 1024  # core.xml / meta.xml bigger than this is refused

DC = "http://purl.org/dc/elements/1.1/"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DCTERMS = "http://purl.org/dc/terms/"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

CORE_PART = "docProps/core.xml"
CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
# field → (namespace, local name, prefix used when the file has none for it)
CORE_FIELDS = {
    "title": (DC, "title", "dc"),
    "subject": (DC, "subject", "dc"),
    "author": (DC, "creator", "dc"),
    "keywords": (CP, "keywords", "cp"),
}
ODF_FIELDS = {
    "title": (DC, "title", "dc"),
    "subject": (DC, "subject", "dc"),
    "author": (META, "initial-creator", "meta"),
    "keywords": (META, "keyword", "meta"),
}
PDF_KEYS = {"title": "Title", "subject": "Subject", "author": "Author", "keywords": "Keywords"}
XMP_TWINS = {
    "title": "dc:title",
    "subject": "dc:description",
    "author": "dc:creator",
    "keywords": "pdf:Keywords",
}

LOCKED_TEXT = "This PDF is locked with a password. Type it to see and change the details."
WRONG_PASSWORD_TEXT = "That password isn't right."
FORMAT_TEXT = "Only PDF, Word, Excel, PowerPoint and OpenDocument files can be changed here."
BROKEN_TEXT = "The details part of this file can't be read, so it can't be changed safely."
SIGNED_TEXT = (
    "This PDF is signed. The signature stays, but PDF readers will show that the file was "
    "changed after signing."
)


class DocumentError(ValueError):
    """A plain-language reason the details can't be changed."""


class PasswordNeeded(DocumentError):
    """The PDF needs its password (none given, or a wrong one)."""


@dataclass
class DocumentDetails:
    kind: str = ""  # "pdf" | "office" | "odf"
    title: str = ""
    subject: str = ""
    author: str = ""
    keywords: str = ""
    signed: bool = False  # a PDF with signatures

    def value(self, name: str) -> str:
        return str(getattr(self, name))


@dataclass
class DocumentEdit:
    """Only what the user changed ("" clears a field); ``password`` opens a locked PDF."""

    values: dict[str, str] = field(default_factory=dict)
    password: str = ""


def kind_of(ext: str) -> str | None:
    ext = ext.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in ODF_EXTS:
        return "odf"
    return None


def clean_values(values: dict[str, str]) -> dict[str, str]:
    """The user's text → what's written ("" = remove it); raises DocumentError. Line breaks
    and tabs become spaces, other control characters are dropped (XML 1.0 can't hold them)."""
    out: dict[str, str] = {}
    for name in FIELDS:
        if name not in values:
            continue
        text = well_formed(values[name])
        text = re.sub(r"[\t\n\r]+", " ", text)
        text = re.sub("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]", "", text).strip(WS)
        if len(text) > MAX_CHARS:
            raise DocumentError(f"Keep each detail under {MAX_CHARS} characters.")
        out[name] = text
    return out


def keyword_list(text: str) -> list[str]:
    """ "a, b; c" → ["a", "b", "c"] (ODF stores one element per keyword)."""
    return [k.strip(WS) for k in re.split(r"[,;]", text) if k.strip(WS)]


def common(details: list[DocumentDetails]) -> dict[str, str | None]:
    """Each field's value when every file has the same one, else None ("Several values")."""
    out: dict[str, str | None] = {}
    for name in FIELDS:
        values = {d.value(name) for d in details}
        out[name] = values.pop() if len(values) == 1 else None
    return out


# ----------------------------------------------------------------------------- XML as text
def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        e = m[1]
        if e.startswith("#x") or e.startswith("#X"):
            return _char(int(e[2:], 16), m[0])
        if e.startswith("#"):
            return _char(int(e[1:]), m[0])
        return {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}.get(e, m[0])

    return re.sub(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);", one, text)


def _char(code: int, fallback: str) -> str:
    return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else fallback


def prefix_of(xml: str, uri: str) -> str | None:
    """The prefix ``xml`` declares for ``uri`` (the first one), or None."""
    m = re.search(
        r"\sxmlns:([\w.-]+)\s*=\s*(?:\"" + re.escape(uri) + r"\"|'" + re.escape(uri) + "')", xml
    )
    return m[1] if m else None


def _root_start(xml: str) -> re.Match[str]:
    """The root element's start tag (after the declaration, comments and processing steps)."""
    m = re.search(r"<(?![?!])[\w.:-]+(?:\s[^>]*?)?/?>", xml)
    if not m:
        raise DocumentError(BROKEN_TEXT)
    return m


def declare(xml: str, uri: str, prefix: str) -> tuple[str, str]:
    """(xml with ``uri`` declared on the root, the prefix to use for it). Only the root's own
    declarations count: one on an inner element (openpyxl writes ``<dc:creator xmlns:dc=…>``)
    doesn't reach the elements added next to it."""
    m = _root_start(xml)
    tag = m[0]
    have = prefix_of(tag, uri)
    if have is not None:
        return xml, have
    while re.search(r"\sxmlns:" + re.escape(prefix) + r"\s*=", tag):
        prefix += "x"  # that prefix is taken by another namespace
    end = len(tag) - (2 if tag.endswith("/>") else 1)
    tag = f'{tag[:end]} xmlns:{prefix}="{uri}"{tag[end:]}'
    return xml[: m.start()] + tag + xml[m.end() :], prefix


def element_texts(xml: str, name: str) -> list[str]:
    """The text of every ``name`` element (prefixed name), unescaped."""
    n = re.escape(name)
    found = re.findall(rf"<{n}(?:\s[^>]*)?>([\s\S]*?)</{n}>", xml)
    return [unescape(re.sub(r"<[^>]*>", "", t)) for t in found]


def set_text(xml: str, container: str, uri: str, local: str, prefix: str, values: list[str]) -> str:
    """``xml`` with every ``local`` element (in namespace ``uri``) inside the ``container``
    element replaced by one element per value, where the first one was (else at the end of
    ``container``). No values removes them."""
    xml, p = declare(xml, uri, prefix)
    n = re.escape(f"{p}:{local}")
    pattern = re.compile(rf"<{n}(?:\s[^>]*?)?/>|<{n}(?:\s[^>]*)?>[\s\S]*?</{n}>")
    new = "".join(f"<{p}:{local}>{escape(v)}</{p}:{local}>" for v in values)
    first = pattern.search(xml)
    if first:
        head, tail = xml[: first.start()], xml[first.end() :]
        return head + new + pattern.sub("", tail)
    if not new:
        return xml
    c = re.escape(container)
    close = re.search(rf"</{c}\s*>", xml)
    if close:
        return xml[: close.start()] + new + xml[close.start() :]
    empty = re.search(rf"<{c}(\s[^>]*?)?/>", xml)
    if empty:
        return (
            xml[: empty.start()]
            + f"<{container}{empty[1] or ''}>{new}</{container}>"
            + xml[empty.end() :]
        )
    raise DocumentError(BROKEN_TEXT)


def _root_name(xml: str) -> str:
    return re.match(r"<([\w.:-]+)", _root_start(xml)[0])[1]  # type: ignore[index]


NEW_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<cp:coreProperties xmlns:cp="{CP}" xmlns:dc="{DC}" xmlns:dcterms="{DCTERMS}" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>'
)


def core_xml(xml: str | None, values: dict[str, str]) -> str:
    """``docProps/core.xml`` (None = the file has none) with the cleaned ``values`` set."""
    xml = NEW_CORE if xml is None else xml
    root = _root_name(xml)
    for name in FIELDS:
        if name in values:
            uri, local, prefix = CORE_FIELDS[name]
            xml = set_text(xml, root, uri, local, prefix, [values[name]] if values[name] else [])
    return xml


NEW_META = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<office:document-meta xmlns:office="{OFFICE}" xmlns:meta="{META}" xmlns:dc="{DC}" '
    'office:version="1.2"><office:meta></office:meta></office:document-meta>'
)


def odf_meta(xml: str | None, values: dict[str, str]) -> str:
    """``meta.xml`` (None = the file has none) with the cleaned ``values`` set."""
    xml = NEW_META if xml is None else xml
    xml, op = declare(xml, OFFICE, "office")
    container = f"{op}:meta"
    if not re.search(rf"<{re.escape(container)}[\s/>]", xml):
        root = _root_name(xml)
        xml = set_text(xml, root, OFFICE, "meta", op, [""])  # an empty <office:meta> to fill
    for name in FIELDS:
        if name not in values:
            continue
        uri, local, prefix = ODF_FIELDS[name]
        text = values[name]
        items = keyword_list(text) if name == "keywords" else ([text] if text else [])
        xml = set_text(xml, container, uri, local, prefix, items)
    return xml


def content_types(xml: str) -> str:
    """``[Content_Types].xml`` with an override for a new core.xml (kept if it has one)."""
    if re.search(r"PartName\s*=\s*[\"']/docProps/core\.xml[\"']", xml, re.IGNORECASE):
        return xml
    add = f'<Override PartName="/{CORE_PART}" ContentType="{CORE_TYPE}"/>'
    m = re.search(r"</(?:[\w.-]+:)?Types\s*>", xml)
    if not m:
        raise DocumentError(BROKEN_TEXT)
    return xml[: m.start()] + add + xml[m.start() :]


def package_rels(xml: str) -> str:
    """``_rels/.rels`` with a relationship to a new core.xml."""
    if CORE_REL in xml:
        return xml
    ids = set(re.findall(r"\sId\s*=\s*[\"']([^\"']*)[\"']", xml))
    rid, n = "rIdUmcCore", 1
    while rid in ids:
        n += 1
        rid = f"rIdUmcCore{n}"
    add = f'<Relationship Id="{rid}" Type="{CORE_REL}" Target="{CORE_PART}"/>'
    m = re.search(r"</(?:[\w.-]+:)?Relationships\s*>", xml)
    if not m:
        raise DocumentError(BROKEN_TEXT)
    return xml[: m.start()] + add + xml[m.start() :]


def manifest(xml: str) -> str:
    """``META-INF/manifest.xml`` with an entry for a new meta.xml."""
    if re.search(r"full-path\s*=\s*[\"']meta\.xml[\"']", xml):
        return xml
    p = prefix_of(xml, MANIFEST) or "manifest"
    add = f'<{p}:file-entry {p}:full-path="meta.xml" {p}:media-type="text/xml"/>'
    m = re.search(rf"</{re.escape(p)}:manifest\s*>", xml)
    if not m:
        raise DocumentError(BROKEN_TEXT)
    return xml[: m.start()] + add + xml[m.start() :]


def core_target(rels: str | None) -> str:
    """Where ``_rels/.rels`` says the core properties are (most files: docProps/core.xml)."""
    for rel in re.findall(r"<(?:[\w.-]+:)?Relationship\b[^>]*>", rels or ""):
        if CORE_REL in rel:
            m = re.search(r"\sTarget\s*=\s*[\"']([^\"']*)[\"']", rel)
            if m:
                return unescape(m[1]).lstrip("/")
    return CORE_PART


# ----------------------------------------------------------------------------- zips
def _part(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise DocumentError(BROKEN_TEXT)
    data = zf.read(info)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError(BROKEN_TEXT) from exc


def changed_parts(kind: str, read: dict[str, str | None], values: dict[str, str]) -> dict[str, str]:
    """{part name: new text} for an Office / ODF file whose parts ``read`` holds
    (``_rels/.rels``, the core part, ``[Content_Types].xml``; or ``meta.xml``, the manifest)."""
    if kind == "office":
        target = core_target(read.get("_rels/.rels"))
        core = read.get(target)
        out = {target: core_xml(core, values)}
        if core is None:
            out["[Content_Types].xml"] = content_types(read.get("[Content_Types].xml") or "")
            out["_rels/.rels"] = package_rels(read.get("_rels/.rels") or "")
        return out
    meta = read.get("meta.xml")
    out = {"meta.xml": odf_meta(meta, values)}
    if meta is None and read.get("META-INF/manifest.xml") is not None:
        out["META-INF/manifest.xml"] = manifest(read["META-INF/manifest.xml"] or "")
    return out


def parts_needed(kind: str, rels: str | None) -> list[str]:
    if kind == "office":
        return ["_rels/.rels", core_target(rels), "[Content_Types].xml"]
    return ["meta.xml", "META-INF/manifest.xml"]


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentError(
            f"“{path.name}” can't be read as a document — it may be damaged or password-locked."
        ) from exc


def _read_parts(zf: zipfile.ZipFile, kind: str) -> dict[str, str | None]:
    rels = _part(zf, "_rels/.rels") if kind == "office" else None
    return {name: _part(zf, name) for name in parts_needed(kind, rels)}


def details_from_parts(kind: str, read: dict[str, str | None]) -> DocumentDetails:
    d = DocumentDetails(kind=kind)
    if kind == "office":
        xml = read.get(core_target(read.get("_rels/.rels"))) or ""
        table = CORE_FIELDS
    else:
        xml = read.get("meta.xml") or ""
        table = ODF_FIELDS
    for name in FIELDS:
        uri, local, _ = table[name]
        p = prefix_of(xml, uri)
        if p is None:
            continue
        texts = [t.strip(WS) for t in element_texts(xml, f"{p}:{local}")]
        texts = [t for t in texts if t]
        setattr(d, name, ", ".join(texts) if name == "keywords" else (texts[0] if texts else ""))
    return d


def rewrite_zip(src: Path, dest: Path, parts: dict[str, str]) -> None:
    """Copy every entry of ``src`` into ``dest`` (same order, names, dates and compression),
    with ``parts`` (name → text) replaced or, when missing, added at the end."""
    with _open_zip(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        zout.comment = zin.comment
        done = set()
        for info in zin.infolist():
            data = (
                parts[info.filename].encode("utf-8") if info.filename in parts else zin.read(info)
            )
            done.add(info.filename)
            out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out.compress_type = info.compress_type
            out.external_attr = info.external_attr
            out.create_system = info.create_system
            out.comment = info.comment
            out.extra = info.extra
            zout.writestr(out, data)
        for name, text in parts.items():
            if name not in done:
                out = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                out.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(out, text.encode("utf-8"))


# ----------------------------------------------------------------------------- reading / saving
def read_details(path: Path, password: str = "") -> DocumentDetails:
    """The file's details; raises PasswordNeeded for a locked PDF without (the right) password."""
    kind = kind_of(path.suffix.lstrip("."))
    if kind is None:
        raise DocumentError(FORMAT_TEXT)
    if kind == "pdf":
        from umc.tools import pdf

        try:
            info = pdf.read_info(path, password)
        except pdf.PasswordError as exc:
            raise PasswordNeeded(WRONG_PASSWORD_TEXT if password else LOCKED_TEXT) from exc
        except RuntimeError as exc:
            raise DocumentError(f"“{path.name}” couldn't be read.") from exc
        d = DocumentDetails(kind="pdf", signed=info["signed"])
        for name, key in PDF_KEYS.items():
            setattr(d, name, (info["info"].get(key) or "").strip(WS))
        return d
    with _open_zip(path) as zf:
        return details_from_parts(kind, _read_parts(zf, kind))


def check_edit(details: DocumentDetails, edit: DocumentEdit) -> dict[str, str]:
    del details
    return clean_values(edit.values)


def save_file(path: Path, edit: DocumentEdit, as_copy: bool = False) -> Path:
    """Write ``edit`` into ``path`` (or a copy, "name (1).pdf"); see ``saving.save_with``."""
    path = path.resolve()
    kind = kind_of(path.suffix.lstrip("."))
    if kind is None:
        raise DocumentError(FORMAT_TEXT)
    values = clean_values(edit.values)
    if kind == "pdf":
        from umc.tools import pdf

        def write(dest: Path) -> None:
            try:
                pdf.write_info(path, dest, {PDF_KEYS[k]: v for k, v in values.items()},
                               [XMP_TWINS[k] for k in values], edit.password)  # fmt: skip
            except pdf.PasswordError as exc:
                raise PasswordNeeded(WRONG_PASSWORD_TEXT if edit.password else LOCKED_TEXT) from exc
            except RuntimeError as exc:
                raise DocumentError(f"The details of “{path.name}” couldn't be changed.") from exc

        return saving.save_with(path, write, as_copy, DocumentError)
    with _open_zip(path) as zf:
        parts = changed_parts(kind, _read_parts(zf, kind), values)
    return saving.save_with(
        path, lambda dest: rewrite_zip(path, dest, parts), as_copy, DocumentError
    )
