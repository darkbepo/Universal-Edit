"""Try every conversion the desktop app offers, with default settings, and report what fails.

    python scripts/convert_matrix.py [--out DIR] [--only FAMILY,...] [--jobs N] [--timeout S]

Generates one sample per input format (plus a few multi-file sets), asks the registry for every
target each sample can be converted to, runs each conversion in its own process (so a hang or a
crash only loses that one) and checks that every output exists and opens. External programs that
aren't installed (LibreOffice, Pandoc, Calibre, Ghostscript, LibreDWG) are reported as "tool
missing", not as failures. Writes ``report.md`` and ``report.json`` into the output folder.
"""

# ruff: noqa: E501  (a test-run script with long sample definitions)
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

# ----------------------------------------------------------------------------- samples


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def make_samples(d: Path) -> dict[str, list[Path]]:
    """name → input files. Formats needing a missing tool to *create* are skipped."""
    from PIL import Image

    from umc.core import samples

    d.mkdir(parents=True, exist_ok=True)
    s: dict[str, list[Path]] = {}

    def add(name: str, *paths: Path) -> None:
        s[name] = list(paths)

    # images
    img = Image.new("RGBA", (321, 241), "red")
    img.paste((0, 0, 255, 128), (100, 60, 220, 180))
    for ext in ("png", "webp", "tiff", "gif", "bmp", "ico", "avif"):
        p = d / f"image.{ext}"
        (img if ext in ("png", "webp", "tiff", "gif", "avif", "ico") else img.convert("RGB")).save(
            p
        )
        add(f"image.{ext}", p)
    img.convert("RGB").save(d / "image.jpg", quality=90)
    add("image.jpg", d / "image.jpg")
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        img.convert("RGB").save(d / "image.heic")
        add("image.heic", d / "image.heic")
    except Exception as exc:  # noqa: BLE001
        print("no HEIC sample:", exc)
    add("image.dng", samples.write_test_dng(d / "image.dng"))
    add("image.psd", samples.write_test_psd(d / "image.psd"))
    add("scan.png", samples.write_test_scan(d / "scan.png"))
    frames = [Image.new("RGB", (64, 64), c) for c in ("red", "yellow", "blue")]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    add("anim.gif", d / "anim.gif")
    slides = []
    for i, c in enumerate(("red", "green", "blue", "orange", "purple")):
        p = d / f"slide{i}.png"
        Image.new("RGB", (640 + i * 16, 360), c).save(p)  # different sizes on purpose
        slides.append(p)
    add("5 images", *slides)

    # video / audio
    if shutil.which("ffmpeg"):
        src = ["-f", "lavfi", "-i", "testsrc2=duration=3:size=640x360:rate=30",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=3"]  # fmt: skip
        for ext, extra in {
            "mp4": ["-pix_fmt", "yuv420p"],
            "mkv": [],
            "webm": ["-c:v", "libvpx-vp9", "-b:v", "500k", "-c:a", "libopus"],
            "avi": [],
            "mov": ["-pix_fmt", "yuv420p"],
        }.items():
            _ffmpeg(*src, "-shortest", *extra, str(d / f"video.{ext}"))
            add(f"video.{ext}", d / f"video.{ext}")
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=duration=3:size=1080x1920:rate=30", "-f", "lavfi",
                "-i", "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p",
                str(d / "portrait.mp4"))  # fmt: skip
        add("portrait.mp4", d / "portrait.mp4")
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=duration=3:size=320x240:rate=30",
                "-pix_fmt", "yuv420p", str(d / "silent.mp4"))  # fmt: skip
        add("silent.mp4 (no audio)", d / "silent.mp4")
        tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "2"]
        for ext in ("wav", "mp3", "flac", "ogg", "m4a", "opus"):
            _ffmpeg(*tone, str(d / f"audio.{ext}"))
            add(f"audio.{ext}", d / f"audio.{ext}")

    # subtitles
    srt = "1\n00:00:00,500 --> 00:00:02,000\nHello world\n\n2\n00:00:02,100 --> 00:00:02,900\nÆØÅ ok\n"
    (d / "subs.srt").write_text(srt, encoding="utf-8")
    add("subs.srt", d / "subs.srt")
    (d / "subs.vtt").write_text(
        "WEBVTT\n\n00:00.500 --> 00:02.000\nHello world\n", encoding="utf-8"
    )
    add("subs.vtt", d / "subs.vtt")
    if "video.mp4" in s:
        add("video.mp4 + subs.srt", d / "video.mp4", d / "subs.srt")
        for ext, codec in (("mkv", "srt"), ("mp4", "mov_text")):
            out = d / f"with-subs.{ext}"
            _ffmpeg("-i", str(d / "video.mp4"), "-i", str(d / "subs.srt"), "-map", "0", "-map", "1",
                    "-c", "copy", "-c:s", codec, str(out))  # fmt: skip
            add(f"with-subs.{ext}", out)

    # documents
    md = "# Title\n\nHello **world** with *style*.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- one\n- two\n"
    (d / "doc.md").write_text(md, encoding="utf-8")
    add("doc.md", d / "doc.md")
    (d / "notes.txt").write_text("Plain text line 1\nLine 2 æøå\n", encoding="utf-8")
    add("notes.txt", d / "notes.txt")
    html = "<html><body><h1>Title</h1><p>Hello <b>world</b></p><table><tr><td>1</td><td>2</td></tr></table></body></html>"
    (d / "page.html").write_text(html, encoding="utf-8")
    add("page.html", d / "page.html")
    import pymupdf

    with pymupdf.open() as pdf:
        for n in range(3):
            page = pdf.new_page()
            page.insert_text((72, 72), f"Page {n + 1} with some text", fontsize=14)
        pdf.save(d / "three.pdf")
    add("three.pdf", d / "three.pdf")
    scan_pdf = pymupdf.open()
    page = scan_pdf.new_page(width=600, height=200)
    page.insert_image(page.rect, filename=str(d / "scan.png"))
    scan_pdf.save(d / "scanned.pdf")
    add("scanned.pdf (image only)", d / "scanned.pdf")
    _office_samples(d, add)

    # data
    rows = [{"name": "Ann", "age": 31, "city": "Oslo"}, {"name": "Bo", "age": 27, "city": "Århus"}]
    (d / "data.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    add("data.json", d / "data.json")
    (d / "data.csv").write_text("name,age,city\nAnn,31,Oslo\nBo,27,Århus\n", encoding="utf-8")
    add("data.csv", d / "data.csv")
    (d / "data.yaml").write_text(
        "- name: Ann\n  age: 31\n- name: Bo\n  age: 27\n", encoding="utf-8"
    )
    add("data.yaml", d / "data.yaml")
    (d / "data.xml").write_text(
        '<people><person id="1"><name>Ann</name></person><person id="2"><name>Bo</name></person></people>',
        encoding="utf-8",
    )
    add("data.xml", d / "data.xml")
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["name", "age"])
    wb.active.append(["Ann", 31])
    wb.save(d / "data.xlsx")
    add("data.xlsx", d / "data.xlsx")

    # 3D
    import trimesh

    box = trimesh.creation.box((10, 10, 10))
    for ext in ("stl", "obj", "ply", "glb", "3mf"):
        box.export(d / f"model.{ext}")
        add(f"model.{ext}", d / f"model.{ext}")
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    for name in (
        "cube-10mm.stp",
        "cube-10mm.igs",
        "sample.7z",
        "sample-rar5.rar",
        "sample-aes.zip",
    ):
        if (fixtures / name).exists():
            shutil.copy(fixtures / name, d / name)
            add(name, d / name)

    # archives
    import zipfile

    with zipfile.ZipFile(d / "files.zip", "w") as z:
        z.writestr("a.txt", "hello")
        z.writestr("sub/b.txt", "world")
    add("files.zip", d / "files.zip")
    import tarfile

    with tarfile.open(d / "files.tar.gz", "w:gz") as t:
        t.add(d / "notes.txt", arcname="notes.txt")
    add("files.tar.gz", d / "files.tar.gz")
    add("mixed: image + pdf + txt", d / "image.png", d / "three.pdf", d / "notes.txt")

    # fonts
    add("font.ttf", samples.write_test_font(d / "font.ttf"))

    # vector
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><rect x="10" y="10" '
           'width="180" height="100" fill="teal"/><text x="30" y="70" font-size="24">Hi</text></svg>')  # fmt: skip
    (d / "drawing.svg").write_text(svg, encoding="utf-8")
    add("drawing.svg", d / "drawing.svg")
    (d / "drawing.eps").write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n0 0 1 setrgbcolor\n"
        "10 10 80 80 rectfill\nshowpage\n%%EOF\n",
        encoding="ascii",
    )
    add("drawing.eps", d / "drawing.eps")

    # CAD
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"color": 1})
    msp.add_circle((50, 50), 30, dxfattribs={"color": 5})
    msp.add_text("UMC", dxfattribs={"height": 5}).set_placement((10, 80))
    doc.saveas(d / "drawing.dxf")
    add("drawing.dxf", d / "drawing.dxf")
    return s


def _office_samples(d: Path, add) -> None:
    """DOCX/ODT/RTF/DOC/XLS/ODS/PPTX/ODP made by LibreOffice (when installed)."""
    from umc.tools import libreoffice

    exe = libreoffice.soffice_path()
    if not exe:
        print("LibreOffice missing: no Office samples")
        return
    jobs = [
        ("page.html", "docx", "MS Word 2007 XML", None),
        ("page.html", "odt", "writer8", None),
        ("page.html", "rtf", "Rich Text Format", None),
        ("page.html", "doc", "MS Word 97", None),
        ("data.xlsx", "ods", "calc8", None),
        ("data.xlsx", "xls", "MS Excel 97", None),
        ("three.pdf", "odp", "impress8", "impress_pdf_import"),
        ("three.pdf", "pptx", "Impress MS PowerPoint 2007 XML", "impress_pdf_import"),
    ]
    for src_name, ext, filt, infilter in jobs:
        src = d / src_name
        if not src.exists() and src_name == "data.xlsx":
            import openpyxl

            wb = openpyxl.Workbook()
            wb.active.append(["name", "age"])
            wb.active.append(["Ann", 31])
            wb.save(src)
        out = d / "lo-out"
        out.mkdir(exist_ok=True)
        cmd = [
            exe,
            "--headless",
            "--norestore",
            "--convert-to",
            f"{ext}:{filt}",
            "--outdir",
            str(out),
        ]
        if infilter:
            cmd.insert(2, f"--infilter={infilter}")
        subprocess.run([*cmd, str(src)], capture_output=True, timeout=180)
        made = out / f"{src.stem}.{ext}"
        if made.exists():
            target = d / f"office.{ext}"
            shutil.move(made, target)
            add(f"office.{ext}", target)
        else:
            print(f"couldn't make office.{ext} sample")


# ----------------------------------------------------------------------------- running


@dataclass
class Result:
    sample: str
    target: str
    status: str  # ok | fail | bad-output | tool-missing | timeout | crash
    seconds: float
    message: str = ""
    outputs: int = 0


def _worker(paths: list[str], target_id: str, out_dir: str, q) -> None:
    try:
        from umc.core import options as opts
        from umc.core.detect import detect
        from umc.core.errors import ToolMissingError
        from umc.core.job import ConversionJob
        from umc.core.registry import targets_for

        files = [detect(p) for p in paths]
        target = {t.id: t for t in targets_for(files)}[target_id]
        cls = target.converter.options_cls
        job = ConversionJob(files=files, target=target, options=opts.build(cls, opts.defaults(cls)),
                            output_dir=Path(out_dir))  # fmt: skip
        try:
            outs = target.converter.run(job)
        except ToolMissingError as exc:
            q.put(("tool-missing", str(exc), 0))
            return
        problems = [p for o in outs for p in _check_output(Path(o))]
        if not outs:
            problems.append("no output files")
        q.put(("bad-output" if problems else "ok", "; ".join(problems[:3]), len(outs)))
    except Exception as exc:  # noqa: BLE001
        details = getattr(exc, "details", "")
        tail = (details.strip().splitlines() or [""])[-1][:200] if details else ""
        msg = f"{type(exc).__name__}: {exc}" + (f" | {tail}" if tail else "")
        if not isinstance(exc, _user_errors()):
            msg += " | " + traceback.format_exc().strip().splitlines()[-3].strip()
        q.put(("fail", msg, 0))


def _user_errors():
    from umc.core.errors import ConversionError

    return (ConversionError,)


def _check_output(path: Path) -> list[str]:
    """Does the output exist and open? Returns problems."""
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file()]
        return [] if files else [f"{path.name}: empty folder"]
    if not path.exists():
        return [f"{path.name}: missing"]
    if path.stat().st_size == 0:
        return [f"{path.name}: empty file"]
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext in ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "ico", "avif", "heic"):
            from PIL import Image

            if ext == "heic":
                import pillow_heif

                pillow_heif.register_heif_opener()
            with Image.open(path) as im:
                im.load()
        elif ext == "gif" or ext in ("mp4", "mkv", "mov", "webm", "avi", "m4v", "flv", "wmv",
                                     "ts", "mpg", "3gp", "ogv", "mp3", "wav", "flac", "ogg",
                                     "m4a", "opus", "aiff", "wma", "ac3"):  # fmt: skip
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(path)], capture_output=True, text=True)  # fmt: skip
            if r.returncode != 0:
                return [f"{path.name}: ffprobe can't read it ({r.stderr.strip()[:120]})"]
            dur = r.stdout.strip()
            if ext != "gif" and dur not in ("", "N/A") and float(dur) <= 0:
                return [f"{path.name}: zero duration"]
        elif ext in ("pdf", "epub", "xps", "cbz", "svg"):
            import pymupdf

            with pymupdf.open(path) as doc:
                if doc.page_count == 0:
                    return [f"{path.name}: no pages"]
        elif ext in ("json",):
            json.loads(path.read_text(encoding="utf-8"))
        elif ext in ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "3mf"):
            import zipfile

            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad:
                    return [f"{path.name}: damaged zip member {bad}"]
        elif ext in ("ttf", "otf", "woff", "woff2"):
            from fontTools.ttLib import TTFont

            TTFont(path).close()
        elif ext in ("stl", "obj", "ply", "glb"):
            import trimesh

            m = trimesh.load(path, force="mesh")
            if len(m.vertices) == 0:
                return [f"{path.name}: no vertices"]
        elif ext == "dxf":
            import ezdxf

            ezdxf.readfile(path)
    except Exception as exc:  # noqa: BLE001
        return [f"{path.name}: doesn't open ({type(exc).__name__}: {str(exc)[:120]})"]
    return []


def run_one(
    paths: list[Path], target_id: str, out_dir: Path, timeout: float
) -> tuple[str, str, int, float]:
    q = mp.Queue()
    p = mp.Process(target=_worker, args=([str(x) for x in paths], target_id, str(out_dir), q))
    t0 = time.monotonic()
    p.start()
    p.join(timeout)
    elapsed = time.monotonic() - t0
    if p.is_alive():
        p.kill()
        p.join()
        return "timeout", f"no result after {timeout:.0f} s", 0, elapsed
    if q.empty():
        return "crash", f"process died (exit code {p.exitcode})", 0, elapsed
    status, msg, n = q.get()
    return status, msg, n, elapsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="matrix-out")
    ap.add_argument("--only", default="", help="comma-separated substrings of sample names")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    shutil.rmtree(out, ignore_errors=True)
    samples_dir = out / "samples"
    sample_sets = make_samples(samples_dir)
    if a.only:
        keys = [k.strip() for k in a.only.split(",")]
        sample_sets = {n: p for n, p in sample_sets.items() if any(k in n for k in keys)}

    from umc.core.detect import detect
    from umc.core.registry import targets_for

    tasks = []
    for name, paths in sample_sets.items():
        files = [detect(p) for p in paths]
        if any(f is None for f in files):
            tasks.append((name, "(detect)", paths))
            continue
        for t in targets_for(files):
            tasks.append((name, t.id, paths))
    print(f"{len(sample_sets)} samples, {len(tasks)} conversions")

    results: list[Result] = []
    from concurrent.futures import ThreadPoolExecutor

    def go(i_task):
        i, (name, tid, paths) = i_task
        if tid == "(detect)":
            return Result(name, tid, "fail", 0, "file type not recognised")
        dest = out / "results" / f"{i:04d}_{tid.replace(':', '_')}"
        dest.mkdir(parents=True, exist_ok=True)
        status, msg, n, secs = run_one(paths, tid, dest, a.timeout)
        r = Result(name, tid, status, round(secs, 1), msg, n)
        mark = {"ok": ".", "tool-missing": "m"}.get(status, "X")
        print(f"{mark} {name:28s} → {tid:22s} {status:12s} {secs:6.1f}s {msg[:110]}", flush=True)
        return r

    with ThreadPoolExecutor(a.jobs) as pool:
        results = list(pool.map(go, enumerate(tasks)))

    (out / "report.json").write_text(
        json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    lines = [
        "# Conversion matrix",
        "",
        ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
        "",
    ]
    lines += ["| Sample | Target | Result | Time | Message |", "|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r.status == "ok", r.status, r.sample, r.target)):
        if r.status != "ok":
            lines.append(
                f"| {r.sample} | {r.target} | {r.status} | {r.seconds} s | {r.message.replace('|', '/')} |"
            )
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + lines[2])
    return 0 if all(r.status in ("ok", "tool-missing") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
