# ruff: noqa: E501  (sample XML strings are clearer on one line)
"""Write tests/fixtures/document_cases.json: what properties/document.py gives for each case.

Both test suites check it (tests/test_document_edit.py, web/tests/document.test.ts), so the
desktop and the website change Office / ODF details into the same bytes. Regenerate it only on
purpose (after changing document.py):  python scripts/make_document_cases.py"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from umc.properties import document as D  # noqa: E402

DOCX_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:title>Old &amp; &lt;odd&gt;</dc:title><dc:subject></dc:subject><dc:creator>Ann</dc:creator>"
    "<cp:keywords>a; b</cp:keywords><cp:lastModifiedBy>Ann</cp:lastModifiedBy><cp:revision>2</cp:revision>"
    '<dcterms:created xsi:type="dcterms:W3CDTF">2024-01-02T03:04:05Z</dcterms:created>'
    "</cp:coreProperties>"
)
OTHER_PREFIXES = (
    "<?xml version='1.0'?>\n<!-- written by a script -->\n"
    '<ns0:coreProperties xmlns:ns0="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:ns1="http://purl.org/dc/elements/1.1/">\n  <ns1:title xml:lang="en">Report</ns1:title>\n'
    "  <ns1:creator/>\n  <ns1:creator>Second</ns1:creator>\n</ns0:coreProperties>\n"
)
EMPTY_ROOT = (
    '<?xml version="1.0"?><cp:coreProperties '
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>'
)
DC_TAKEN = (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="urn:something-else"><dc:title>not ours</dc:title></cp:coreProperties>'
)
LOCAL_NS = (  # openpyxl declares dc / dcterms on each element, not on the root
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">'
    '<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">openpyxl</dc:creator>'
    '<dcterms:created xmlns:dcterms="http://purl.org/dc/terms/">2026-01-01T00:00:00Z</dcterms:created>'
    "</cp:coreProperties>"
)
ODF_META = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'office:version="1.3"><office:meta><meta:generator>LibreOffice/24.2</meta:generator>'
    "<dc:title>Notes</dc:title><meta:initial-creator>Ann</meta:initial-creator>"
    "<meta:keyword>one</meta:keyword><meta:keyword>two</meta:keyword>"
    '<meta:document-statistic meta:page-count="2"/></office:meta></office:document-meta>'
)
ODF_EMPTY_META = (
    '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'office:version="1.2"><office:meta/></office:document-meta>'
)
ODF_NO_META = (
    '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'office:version="1.2"></office:document-meta>'
)
TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/><Relationship Id="rIdUmcCore" Type="x" Target="y"/></Relationships>'
)
RELS_CORE = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="/props/core.xml"/></Relationships>'
)
MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
    "</manifest:manifest>"
)


def outcome(fn, *args):
    try:
        return {"expect": fn(*args)}
    except D.DocumentError as exc:
        return {"error": str(exc)}


def main() -> None:
    clean = [
        {
            "title": "  Spaced  ",
            "subject": "Line\nbreak\tand tab",
            "author": "Bo\x00\x07b",
            "keywords": "a, b",
        },
        {"title": "", "keywords": "\ud800broken"},
        {"title": "x" * (D.MAX_CHARS + 1)},
        {"title": "x" * D.MAX_CHARS, "subject": "￾￿ ok \x7f"},
    ]
    core = [
        (
            DOCX_CORE,
            {"title": "New ☃ & <more>", "subject": "Plans", "author": "", "keywords": "x, y"},
        ),
        (DOCX_CORE, {"title": ""}),
        (OTHER_PREFIXES, {"title": "Better", "author": "Only one"}),
        (OTHER_PREFIXES, {"keywords": "k"}),
        (EMPTY_ROOT, {"title": "T"}),
        (EMPTY_ROOT, {"title": ""}),
        (DC_TAKEN, {"title": "Ours", "subject": "S"}),
        (LOCAL_NS, {"title": "T", "author": "Bo"}),
        (LOCAL_NS, {"author": ""}),
        (None, {"title": "Made", "keywords": "a"}),
        ("<broken", {"title": "x"}),
    ]
    odf = [
        (ODF_META, {"title": "New", "keywords": "x; y, z", "author": "Bo"}),
        (ODF_META, {"keywords": "", "title": ""}),
        (ODF_META, {"subject": "S & S"}),
        (ODF_EMPTY_META, {"title": "T", "keywords": "a,b"}),
        (ODF_NO_META, {"author": "A"}),
        (None, {"title": "Made"}),
    ]
    parts = [
        (
            "office",
            {"_rels/.rels": RELS, "docProps/core.xml": DOCX_CORE, "[Content_Types].xml": TYPES},
        ),
        ("office", {"_rels/.rels": RELS_CORE, "props/core.xml": OTHER_PREFIXES}),
        ("office", {"_rels/.rels": RELS, "[Content_Types].xml": TYPES}),
        ("odf", {"meta.xml": ODF_META, "META-INF/manifest.xml": MANIFEST}),
        ("odf", {"META-INF/manifest.xml": MANIFEST}),
        ("odf", {}),
    ]
    data = {
        "clean": [{"values": v, **outcome(D.clean_values, v)} for v in clean],
        "keywords": [
            {"text": t, "expect": D.keyword_list(t)} for t in ["a, b;c", " ; ,", "one", ""]
        ],
        "unescape": [
            {"text": t, "expect": D.unescape(t)}
            for t in [
                "&amp;lt; &lt;&gt;&quot;&apos;",
                "&#65;&#x263A;&#X42;",
                "&#0; &#xD800; &bogus; &",
                "plain",
            ]
        ],
        "core": [{"xml": x, "values": v, **outcome(D.core_xml, x, v)} for x, v in core],
        "odf": [{"xml": x, "values": v, **outcome(D.odf_meta, x, v)} for x, v in odf],
        "types": [
            {"xml": x, **outcome(D.content_types, x)}
            for x in [
                TYPES,
                TYPES.replace(
                    "<Default", '<Override PartName="/docProps/core.xml" ContentType="x"/><Default'
                ),
                "<x/>",
            ]
        ],
        "rels": [{"xml": x, **outcome(D.package_rels, x)} for x in [RELS, RELS_CORE, "<x/>"]],
        "manifest": [
            {"xml": x, **outcome(D.manifest, x)}
            for x in [MANIFEST, MANIFEST.replace('full-path="/"', 'full-path="meta.xml"')]
        ],
        "target": [{"xml": x, "expect": D.core_target(x)} for x in [RELS, RELS_CORE, None]],
        "details": [
            {"kind": k, "parts": p, "expect": asdict(D.details_from_parts(k, p))} for k, p in parts
        ],
        "changed": [
            {
                "kind": k,
                "parts": p,
                "values": {"title": "T", "keywords": "a, b"},
                **outcome(D.changed_parts, k, p, {"title": "T", "keywords": "a, b"}),
            }
            for k, p in parts
        ],  # fmt: skip
    }
    out = ROOT / "tests" / "fixtures" / "document_cases.json"
    out.write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
