"""``UniversalEdit --self-test REPORT.txt`` — run real conversions without the GUI.

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
    """Word / Markdown → PDF with the built-in engines (mammoth, markdown-it, PyMuPDF Story)."""
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


def _logo_check(d: Path) -> list[Path]:
    """The window / taskbar icon: logo.svg must be bundled and render (QtSvg)."""
    from umc.gui import branding

    d.mkdir(parents=True, exist_ok=True)
    if not branding.LOGO_SVG.is_file():
        raise RuntimeError(f"logo missing: {branding.LOGO_SVG}")
    image = branding.logo_image(64)
    if image.pixelColor(32, 32).alpha() == 0 or image.pixelColor(0, 0).alpha() != 0:
        raise RuntimeError("logo rendered wrongly")
    out = d / "logo.png"
    image.save(str(out))
    return [out]


def _viewer_check(d: Path, video: Path) -> list[Path]:
    """The viewer: Qt Multimedia (PySide6-Addons) with its FFmpeg backend is bundled, the
    single-instance socket module loads, and the folder rules work."""
    import importlib
    import os

    from PySide6.QtCore import QLibraryInfo

    from umc.viewer import files as vf

    for mod in ("PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork"):
        importlib.import_module(mod)
    importlib.import_module("umc.gui.viewer.viewer_window")
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "multimedia"
    if not any("ffmpeg" in p.name.lower() for p in plugins.glob("*")):
        raise RuntimeError(f"Qt's FFmpeg media plugin is missing in {plugins}")
    # The plugin must also *load* (its avcodec/avformat DLLs found) and decode: play a few frames
    # of the H.264 slideshow an earlier step made. The app forces the same backend (app.py), so
    # a silent fall-back to Windows' own one can't hide a broken FFmpeg backend.
    os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
    import time

    from PySide6.QtCore import QCoreApplication, QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

    app = QCoreApplication.instance() or QCoreApplication([])
    player, sink, frames, errors = QMediaPlayer(), QVideoSink(), [], []
    player.setVideoOutput(sink)
    sink.videoFrameChanged.connect(lambda f: frames.append(f.isValid()))
    player.errorOccurred.connect(lambda _e, msg: errors.append(msg))
    player.setSource(QUrl.fromLocalFile(str(video)))
    player.play()
    deadline = time.monotonic() + 20
    while sum(frames) < 3 and not errors and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    player.stop()
    if sum(frames) < 3:
        raise RuntimeError(f"Qt Multimedia didn't play the test video: {errors or 'no frames'}")
    d.mkdir(parents=True, exist_ok=True)
    for name in ("b.png", "a.mp4", "c.pdf"):
        (d / name).write_bytes(b"x")
    if [p.name for p in vf.folder_items(d / "b.png")] != ["a.mp4", "b.png", "c.pdf"]:
        raise RuntimeError("viewer folder list is wrong")
    return []


def _addons_check(d: Path) -> list[Path]:
    """Every editor add-on is installed (CI installs them from the built zips first) and its
    code loads inside the built app — proves the app kept what the add-ons import."""
    import importlib

    from umc.core import addons

    for addon in addons.ADDONS.values():
        if not addons.installed(addon.id):
            raise RuntimeError(f"add-on {addon.id!r} isn't installed")
        addons.activate(addon.id)
        importlib.import_module(addon.entry)
        for package in addon.packages:
            importlib.import_module(package)
    return []


def _icon_check(d: Path) -> list[Path]:
    """Convert screen icons: Tabler SVG markup rendered by QtSvg (needs the QtSvg module)."""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from umc.gui.icon_data import ICONS
    from umc.gui.icons import svg

    d.mkdir(parents=True, exist_ok=True)
    for name in ICONS:
        renderer = QSvgRenderer(QByteArray(svg(name, "#000000")))
        if not renderer.isValid():
            raise RuntimeError(f"icon {name!r} is not valid SVG")
    image = QImage(48, 48, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg("upload", "#5b5ef0"))).render(painter, QRectF(0, 0, 48, 48))
    painter.end()
    if not any(image.pixelColor(x, y).alpha() for x in range(48) for y in range(48)):
        raise RuntimeError("icon rendered empty")
    out = d / "upload.png"
    image.save(str(out))
    return [out]


def _doc_editor_check(d: Path) -> list[Path]:
    """Document editor: heading + table + picture -> DOCX (read back) and PDF."""
    import io

    import pymupdf
    from PIL import Image

    from umc.doceditor.io import open_document
    from umc.doceditor.save import save_document
    from umc.doceditor.types import SaveContent

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    html = (
        "<h1>Self-test</h1><table><tr><td><p>a</p></td></tr></table>"
        '<p><img src="img/1.png" width="8"></p>'
    )
    content = SaveContent(html, {"img/1.png": buf.getvalue()})
    d.mkdir(parents=True, exist_ok=True)
    docx = save_document(content, d / "doc.docx", "docx")
    pdf_out = save_document(content, d / "doc.pdf", "pdf")
    opened = open_document(docx)
    low = opened.html.lower()
    if "<h1" not in low or "self-test" not in low:
        raise RuntimeError("heading lost in DOCX round trip")
    if "<table" not in low:
        raise RuntimeError("table lost in DOCX round trip")
    if len(opened.images) != 1:
        raise RuntimeError(f"expected 1 picture, found {len(opened.images)}")
    with pymupdf.open(pdf_out) as pdf_doc:
        if "Self-test" not in pdf_doc[0].get_text():
            raise RuntimeError("PDF text is missing the heading")
    md = d / "notes.md"
    md.write_text("- one\n  - two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    low = open_document(md).html.lower()
    if "<ul><li>one<ul><li>two" not in low or "<table" not in low:
        raise RuntimeError("Markdown (markdown-it) lost the nested list or the table")
    return [docx, pdf_out]


def _viewer3d_check(d: Path) -> list[Path]:
    """3D viewer: load a model for viewing, and the Qt OpenGL modules are packaged."""
    import importlib

    from umc.editor3d.mesh import load_view_model

    importlib.import_module("PySide6.QtOpenGL")
    importlib.import_module("PySide6.QtOpenGLWidgets")
    importlib.import_module("umc.gui.editor3d.viewer_page")
    d.mkdir(parents=True, exist_ok=True)
    path = _box_stl(d)
    m = load_view_model(path, "stl")
    if [round(v, 3) for v in m.size] != [20, 10, 5] or not m.watertight:
        raise RuntimeError(f"unexpected model: {m.size}, watertight={m.watertight}")
    if m.triangle_buffer().shape != (36, 6):
        raise RuntimeError("bad triangle buffer")
    return [path]


def _audio_editor_check(d: Path) -> list[Path]:
    """Audio editor: open (FFmpeg → float WAV), cut, fade, save as MP3, and Qt Multimedia's
    audio output classes are packaged."""
    import importlib

    from umc.audioedit.io import open_audio, save_audio

    importlib.import_module("umc.gui.audioeditor.audio_editor_page")
    import threading

    from PySide6.QtMultimedia import QAudioFormat, QAudioSink  # noqa: F401

    from umc.tools import ffmpeg

    d.mkdir(parents=True, exist_ok=True)
    sound = d / "tone.wav"
    ffmpeg.run_ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ac", "2", str(sound)],
        total_seconds=1,
        progress=lambda _f: None,
        cancel_event=threading.Event(),
    )
    doc = open_audio([sound])
    before = doc.frames
    doc.set_selection(0, doc.rate // 10)
    doc.cut()
    doc.set_selection(0, doc.rate // 10)
    doc.fade_in()
    out = save_audio(doc, d / "edited.mp3", "mp3", 128)
    back = open_audio([out])
    if not before > doc.frames > 0 or abs(back.duration - doc.duration) > 0.15:
        raise RuntimeError(f"unexpected lengths: {before}, {doc.frames}, {back.duration}")
    return [out]


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
            ("convert screen icons (QtSvg)", lambda: _icon_check(d / "icons")),
            ("app logo / window icon", lambda: _logo_check(d / "logo")),
            ("editor add-ons installed and loading", lambda: _addons_check(d / "addons")),
            ("document editor", lambda: _doc_editor_check(d / "doceditor")),
            ("3D viewer (model + Qt OpenGL)", lambda: _viewer3d_check(d / "viewer3d")),
            (
                "audio editor (open, cut, fade, save as mp3)",
                lambda: _audio_editor_check(d / "audioedit"),
            ),
            ("mp4 -> webm", lambda: _convert([out / "img0 slideshow.mp4"], "webm", out)),
            ("mp4 -> gif", lambda: _convert([out / "img0 slideshow.mp4"], "video:gif", out)),
            ("mp4 -> frames", lambda: _convert([out / "img0 slideshow.mp4"], "frames:png", out)),
            ("pdf -> png pages", lambda: _convert([out / "img0 + 2 more.pdf"], "pages:png", out)),
            ("pdf -> split", lambda: _convert([out / "img0 + 2 more.pdf"], "split:pdf", out)),
            # last: it leaves a QCoreApplication behind
            (
                "viewer (Qt Multimedia + FFmpeg backend)",
                lambda: _viewer_check(d / "viewer", out / "img0 slideshow.mp4"),
            ),
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
