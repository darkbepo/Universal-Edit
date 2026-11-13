"""Writes tests/fixtures/photo_edit_cases.json: small pictures + edits → the exact bytes the
desktop's properties/photo.py produces. The web port must produce the same bytes
(web/tests/photo.test.ts). Run on purpose only, after changing photo.py / exif.py /
containers.py / xmp.py:  python scripts/make_photo_cases.py"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from umc.core.samples import photo_exif, write_test_photo  # noqa: E402
from umc.properties import containers, photo  # noqa: E402

XMP = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    ' <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
    ' tiff:Orientation="6" exif:GPSLatitude="55,40.566N" exif:GPSLongitude="12,34.198E">\n'
    '  <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Old title</rdf:li></rdf:Alt></dc:title>\n'
    "  <dc:creator><rdf:Seq><rdf:li>Old Author</rdf:li></rdf:Seq></dc:creator>\n"
    "  <dc:subject><rdf:Bag><rdf:li>one</rdf:li><rdf:li>two</rdf:li></rdf:Bag></dc:subject>\n"
    " </rdf:Description>\n</rdf:RDF></x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)


def pictures() -> dict[str, bytes]:
    tmp = ROOT / "build" / "photo-cases"
    tmp.mkdir(parents=True, exist_ok=True)
    out = {"phone.jpg": write_test_photo(tmp / "phone.jpg").read_bytes()}
    b = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 40, 40)).save(b, "JPEG", quality=50)
    out["bare.jpg"] = b.getvalue()
    out["xmp.jpg"] = containers.put(out["phone.jpg"], containers.get_exif(out["phone.jpg"]), XMP)
    b = io.BytesIO()
    Image.new("RGBA", (6, 4), (10, 120, 200, 128)).save(b, "PNG", exif=photo_exif())
    out["exif.png"] = b.getvalue()
    for name, kw in (("lossy.webp", {"quality": 50}), ("lossless.webp", {"lossless": True})):
        b = io.BytesIO()
        Image.new("RGBA", (6, 4), (40, 200, 90, 200)).save(b, "WEBP", **kw)
        out[name] = b.getvalue()
    return out


EDITS = [
    ("set every field", {"values": {"title": "Harbour, æøå", "comment": "Shot from the pier",
     "author": "Grace Hopper", "copyright": "CC0", "tags": "sea, boats;dusk",
     "taken": "2025-01-02 03:04"}, "orientation": 1}),
    ("clear some, remove location", {"values": {"author": "", "title": ""},
     "remove_location": True}),
    ("only rotate", {"orientation": 8}),
    ("remove everything", {"remove_all": True}),
    ("remove everything but set a title", {"values": {"title": "Kept"}, "remove_all": True}),
]  # fmt: skip


def main() -> None:
    cases = []
    for name, data in pictures().items():
        cases.append(
            {
                "name": f"{name}: read",
                "input": data.hex(),
                "details": asdict(photo.read_details(data)),
            }
        )
        for label, edit in EDITS:
            pe = photo.PhotoEdit(**edit)
            out = photo.apply(data, pe)
            cases.append(
                {
                    "name": f"{name}: {label}",
                    "input": data.hex(),
                    "edit": edit,
                    "output": out.hex(),
                    "details": asdict(photo.read_details(out)),
                }
            )
    path = ROOT / "tests" / "fixtures" / "photo_edit_cases.json"
    path.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases → {path}")


if __name__ == "__main__":
    main()
