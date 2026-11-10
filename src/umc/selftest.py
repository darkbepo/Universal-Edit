"""``UniversalMediaConverter --self-test REPORT.txt`` — run real conversions without the GUI.

Used by CI to prove the frozen executable works (bundled FFmpeg found, image/PDF libraries
packaged correctly). Writes a plain-text report and exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import tempfile
import traceback
from pathlib import Path


def _convert(paths, target_id: str, out: Path, **values):
    from umc.core import options as opts
    from umc.core.detect import detect
    from umc.core.job import ConversionJob
    from umc.core.registry import targets_for

    files = [detect(p) for p in paths]
    target = {t.id: t for t in targets_for(files)}[target_id]
    cls = target.converter.options_cls
    job = ConversionJob(
        files=files,
        target=target,
        output_dir=out,
        options=opts.build(cls, {**opts.defaults(cls), **values}),
    )
    return target.converter.run(job)


def _box_stl(d: Path) -> Path:
    import trimesh

    path = d / "box.stl"
    path.write_bytes(trimesh.creation.box((20, 10, 5)).export(file_type="stl"))
    return path


def _cad_engine_check() -> list[Path]:
    import cascadio  # noqa: F401  (native OpenCASCADE module must be packaged)

    return []


def _ocr_check(d: Path, out: Path) -> list[Path]:
    """OCR with the bundled English data only (fresh tools folder, so nothing is downloaded)."""
    import os

    from umc.core.samples import SCAN_TEXT, write_test_scan

    os.environ["UMC_TOOLS_DIR"] = str(d / "tools")
    try:
        [txt] = _convert([write_test_scan(d / "scan.png")], "ocr:txt", out)
    finally:
        os.environ.pop("UMC_TOOLS_DIR", None)
    if SCAN_TEXT[0] not in txt.read_text(encoding="utf-8"):
        raise AssertionError(f"OCR text was {txt.read_text(encoding='utf-8')!r}")
    return [txt]


def _archive_check(d: Path, out: Path, imgs: list[Path]) -> list[Path]:
    from umc.core.samples import write_test_rar

    [zipped] = _convert(imgs, "pack:zip", out, password="selftest")
    [sevenz] = _convert([zipped], "7z", out, source_password="selftest", password="selftest")
    results = [zipped, sevenz]
    for version in (4, 5):
        rar = write_test_rar(d / f"sample{version}.rar", version)
        results += _convert([rar], "zip", out)
    return results


def _subtitle_check(d: Path, out: Path) -> list[Path]:
    srt = d / "clip.en.srt"
    srt.write_text("1\n00:00:00,100 --> 00:00:01,000\nHello ÆØÅ\n", encoding="utf-8")
    video = out / "img0 slideshow.mp4"
    [vtt] = _convert([srt], "vtt", out)
    [burned] = _convert([video, srt], "burn:mp4", out)
    [mkv] = _convert([video, srt], "subtrack:mkv", out)
    extracted = _convert([mkv], "subs:srt", out)
    if "ÆØÅ" not in extracted[0].read_text(encoding="utf-8"):
        raise AssertionError("subtitle text was not kept")
    return [vtt, burned, mkv, *extracted]


def _font_check(d: Path, out: Path) -> list[Path]:
    from umc.core.samples import write_test_font

    ttf = write_test_font(d / "UMC Test.ttf")
    [woff2] = _convert([ttf], "woff2", out)
    [otf] = _convert([woff2], "otf", out)
    [back] = _convert([otf], "ttf", out / "back", subset="latin")
    return [woff2, otf, back]


def _vector_check(d: Path, out: Path, imgs: list[Path]) -> list[Path]:
    svg = d / "logo.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">'
        '<rect width="60" height="60" fill="red"/>'
        '<circle cx="90" cy="30" r="25" fill="blue"/></svg>',
        encoding="utf-8",
    )
    [png] = _convert([svg], "vec:png", out)
    [pdf_out] = _convert([svg], "vec:pdf", out)
    [traced] = _convert(imgs[:1], "trace:svg", out)
    return [png, pdf_out, traced]


def _pdf_to_word_check(d: Path, out: Path) -> list[Path]:
    from umc.tools import pdf

    src = pdf.write_sample_pdf(d / "letter.pdf", ["Dear reader,", "This came from a PDF."])
    [docx] = _convert([src], "doc:docx", out)
    import docx as python_docx

    text = "\n".join(p.text for p in python_docx.Document(docx).paragraphs)
    if "This came from a PDF." not in text:
        raise AssertionError(f"PDF → Word text was {text!r}")
    return [docx]


def _builtin_documents_check(d: Path, out: Path) -> list[Path]:
    """Word / Markdown → PDF with the built-in engines (mammoth, Markdown, PyMuPDF Story)."""
    import pymupdf

    from umc.converters import document
    from umc.tools import htmlpdf

    md = d / "notes.md"
    md.write_text("# Built in\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    html = htmlpdf.markdown_to_html(md, d / "notes.html")
    pdf_out = htmlpdf.html_to_pdf(html, out / "notes.pdf")
    letter = d / "letter.docx"
    if not letter.exists():
        letter = _pdf_to_word_check(d, out)[0]
    docx_html = htmlpdf.docx_to_html(letter, d / "letter.html")
    docx_pdf = htmlpdf.html_to_pdf(docx_html, out / "letter-builtin.pdf")
    with pymupdf.open(pdf_out) as doc:
        if "Built in" not in doc[0].get_text():
            raise AssertionError("Markdown → PDF lost its text")
    with pymupdf.open(docx_pdf) as doc:
        if "This came from a PDF." not in doc[0].get_text():
            raise AssertionError("Word → PDF lost its text")
    assert document.plan("docx", "pdf", {document.SOFFICE: False}) is not None
    return [pdf_out, docx_pdf]


def _cad_check(d: Path, out: Path) -> list[Path]:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.modelspace().add_circle((50, 50), 25)
    doc.modelspace().add_line((0, 0), (100, 100))
    src = d / "drawing.dxf"
    doc.saveas(src)
    [pdf_out] = _convert([src], "cad:pdf", out)
    [png] = _convert([src], "cad:png", out)
    [r12] = _convert([src], "dxf", out / "r12", dxf_version="R12")
    return [pdf_out, png, r12]


def _editor_check(d: Path) -> list[Path]:
    """Image editor: two layers, a brush stroke, .ora round trip, PNG/JPG through the converter."""
    from PIL import Image

    from umc.editor.document import Document
    from umc.editor.layer import Layer
    from umc.editor.ora import read_ora
    from umc.editor.save import save_document
    from umc.editor.tools.base import ToolContext
    from umc.editor.tools.paint import BrushOptions, BrushTool

    base = Layer("base", Image.new("RGBA", (120, 80), (30, 120, 200, 255)))
    top = Layer("top", Image.new("RGBA", (40, 40), (255, 200, 0, 255)), x=20, y=10, opacity=0.5)
    doc = Document(120, 80, [base, top])
    brush, ctx = BrushTool(), ToolContext((255, 0, 0, 255), (255, 255, 255, 255), BrushOptions())
    brush.press(doc, 10, 10, ctx)
    brush.move(doc, 100, 60, ctx)
    if brush.release(doc, 100, 60, ctx) is None:
        raise RuntimeError("brush stroke changed nothing")
    # content-aware fill needs OpenCV's photo module (cv2.inpaint) inside the build
    from umc.editor.geometry import Rect
    from umc.editor.inpaint import content_aware_fill

    doc.active, doc.selection = 0, Rect(60, 40, 70, 50)
    if content_aware_fill(doc) is None:
        raise RuntimeError("content-aware fill changed nothing")
    doc.selection, doc.active = None, 1
    # background removal: ONNX Runtime must load inside the build (the model is downloaded later)
    import onnxruntime

    if "CPUExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError("ONNX Runtime has no CPU provider")
    d.mkdir(parents=True, exist_ok=True)
    ora = save_document(doc, d / "edited.ora", "ora")
    if len(read_ora(ora).layers) != 2:
        raise RuntimeError("layers lost in .ora round trip")
    png = save_document(doc, d / "edited.png", "png")
    jpg = save_document(doc, d / "edited.jpg", "jpg", {"quality": 80})
    for p in (png, jpg):
        Image.open(p).load()
    return [ora, png, jpg]


def run(report: Path) -> int:
    from PIL import Image

    from umc.tools import ffmpeg, libreoffice, pandoc

    lines = [
        f"ffmpeg: {ffmpeg.is_available()}",
        f"libreoffice: {libreoffice.is_available()}",
        f"pandoc: {pandoc.is_available()}",
    ]
    ok = True
    with tempfile.TemporaryDirectory(prefix="umc-selftest-") as tmp:
        d = Path(tmp)
        imgs = []
        for i, color in enumerate(("red", "green", "blue")):
            p = d / f"img{i}.png"
            Image.new("RGB", (320, 240), color).save(p)
            imgs.append(p)
        out = d / "out"
        from umc.core.samples import write_test_dng, write_test_psd

        dng = write_test_dng(d / "camera.dng")
        psd = write_test_psd(d / "design.psd")
        steps = [
            ("camera RAW (dng) -> jpg", lambda: _convert([dng], "jpg", out)),
            ("photoshop psd -> png", lambda: _convert([psd], "png", out)),
            ("3D stl -> 3mf", lambda: _convert([_box_stl(d)], "3mf", out)),
            ("3D CAD engine (STEP/IGES) loads", _cad_engine_check),
            ("OCR image -> text (bundled English)", lambda: _ocr_check(d, out)),
            ("archives: zip (AES) -> 7z, RAR4/RAR5 -> zip", lambda: _archive_check(d, out, imgs)),
            ("png -> jpg", lambda: _convert(imgs[:1], "jpg", out)),
            ("png -> heic", lambda: _convert(imgs[:1], "heic", out)),
            ("png -> avif", lambda: _convert(imgs[:1], "avif", out)),
            ("images -> pdf", lambda: _convert(imgs, "images:pdf", out)),
            (
                "images -> mp4 slideshow (h264)",
                lambda: _convert(imgs, "slideshow:mp4", out, seconds_per_image=0.5),
            ),
            ("subtitles: srt -> vtt, burn in, add track, extract", lambda: _subtitle_check(d, out)),
            ("fonts: ttf -> woff2 -> otf -> ttf (subset)", lambda: _font_check(d, out)),
            ("vector: svg -> png/pdf, trace png -> svg", lambda: _vector_check(d, out, imgs)),
            ("PDF -> Word keeping layout (pdf2docx)", lambda: _pdf_to_word_check(d, out)),
            (
                "Word/Markdown -> PDF built in (no LibreOffice)",
                lambda: _builtin_documents_check(d, out),
            ),
            ("CAD: dxf -> pdf/png, save as R12", lambda: _cad_check(d, out)),
            ("editor: layers -> ora -> png/jpg", lambda: _editor_check(d / "editor")),
            ("mp4 -> webm", lambda: _convert([out / "img0 slideshow.mp4"], "webm", out)),
            ("mp4 -> gif", lambda: _convert([out / "img0 slideshow.mp4"], "video:gif", out)),
            ("mp4 -> frames", lambda: _convert([out / "img0 slideshow.mp4"], "frames:png", out)),
            ("pdf -> png pages", lambda: _convert([out / "img0 + 2 more.pdf"], "pages:png", out)),
            ("pdf -> split", lambda: _convert([out / "img0 + 2 more.pdf"], "split:pdf", out)),
        ]
        for name, step in steps:
            try:
                result = step()
                lines.append(f"PASS {name}: {[p.name for p in result]}")
            except Exception:
                ok = False
                lines.append(f"FAIL {name}:\n{traceback.format_exc()}")
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1
