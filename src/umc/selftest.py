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
            ("png -> jpg", lambda: _convert(imgs[:1], "jpg", out)),
            ("png -> heic", lambda: _convert(imgs[:1], "heic", out)),
            ("png -> avif", lambda: _convert(imgs[:1], "avif", out)),
            ("images -> pdf", lambda: _convert(imgs, "images:pdf", out)),
            (
                "images -> mp4 slideshow (h264)",
                lambda: _convert(imgs, "slideshow:mp4", out, seconds_per_image=0.5),
            ),
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
