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
