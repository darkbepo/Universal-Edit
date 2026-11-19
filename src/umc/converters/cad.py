"""CAD drawings: DXF / DWG → PDF, SVG, PNG, JPG; DWG → DXF; DXF version change.

ezdxf (MIT) reads DXF and draws it (its PyMuPDF and SVG backends); DWG is first turned into DXF
by LibreDWG's ``dwg2dxf`` (tools/libredwg.py, one-click install).
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import libredwg, pdf

RENDER = {"cad:pdf": "PDF", "cad:svg": "SVG", "cad:png": "PNG", "cad:jpg": "JPG"}
PAPERS_MM = {
    "a4": (297, 210),
    "a3": (420, 297),
    "a2": (594, 420),
    "a1": (841, 594),
    "a0": (1189, 841),
    "letter": (279.4, 215.9),
}
DXF_VERSIONS = [
    ("R2018", "AutoCAD 2018"),
    ("R2013", "AutoCAD 2013"),
    ("R2010", "AutoCAD 2010"),
    ("R2007", "AutoCAD 2007"),
    ("R2004", "AutoCAD 2004"),
    ("R2000", "AutoCAD 2000"),
    ("R12", "AutoCAD R12 (oldest, most compatible)"),
]
RENDER_TARGETS = tuple(RENDER)


@dataclass
class CadOptions:
    paper: str = opt(
        "auto",
        "Page size",
        choices=[
            ("auto", "Fit the drawing"),
            ("a4", "A4"),
            ("a3", "A3"),
            ("a2", "A2"),
            ("a1", "A1"),
            ("a0", "A0"),
            ("letter", "US Letter"),
        ],
        targets=("cad:pdf", "cad:svg"),
        help="Landscape; the drawing is scaled to fit inside the margins.",
    )
    margin_mm: int = opt(
        10, "Margin", kind="slider", min=0, max=50, unit="mm", targets=RENDER_TARGETS
    )
    colours: str = opt(
        "black_on_white",
        "Colours",
        choices=[
            ("black_on_white", "Black lines on white (for printing)"),
            ("colour_light", "Layer colours on white"),
            ("colour_dark", "Layer colours on black (like the CAD screen)"),
        ],
        targets=RENDER_TARGETS,
    )
    lineweight: float = opt(
        1.0,
        "Line thickness",
        kind="float",
        min=0.1,
        max=10,
        decimals=1,
        unit="×",
        targets=RENDER_TARGETS,
    )
    dpi: int = opt(
        150,
        "Resolution",
        kind="slider",
        min=50,
        max=600,
        unit="DPI",
        targets=("cad:png", "cad:jpg"),
    )
    layouts: str = opt(
        "model",
        "What to draw",
        choices=[("model", "Model space"), ("paper", "Every paper layout (one page each)")],
        targets=RENDER_TARGETS,
    )
    dxf_version: str = opt("R2018", "DXF version", choices=DXF_VERSIONS, targets=("dxf",))


def fix_zero_handles(src: Path, dest: Path) -> bool:
    """Copy an ASCII DXF to ``dest`` giving every entity with handle 0 a fresh one.

    Handles are group codes 5 / 105 (hex); new ones start above the highest in the file, and
    ``$HANDSEED`` is raised past them. False (nothing written) when there was nothing to fix.
    """
    data = src.read_bytes()
    if data.startswith(b"AutoCAD Binary DXF"):
        return False
    lines = data.splitlines(keepends=True)
    top, zeros, seed_at = 0, [], None
    for i in range(0, len(lines) - 1, 2):
        code, value = lines[i].strip(), lines[i + 1].strip()
        if code in (b"5", b"105"):
            if value == b"0":
                zeros.append(i + 1)
            else:
                with contextlib.suppress(ValueError):
                    top = max(top, int(value, 16))
        elif code == b"9" and value == b"$HANDSEED" and i + 3 < len(lines):
            seed_at = i + 3
    if not zeros:
        return False

    def put(index: int, number: int) -> None:
        old = lines[index]
        ending = old[len(old.rstrip(b"\r\n")) :]
        lines[index] = f"{number:X}".encode() + ending

    for n, index in enumerate(zeros, start=top + 1):
        put(index, n)
    if seed_at is not None:
        put(seed_at, top + len(zeros) + 1)
    dest.write_bytes(b"".join(lines))
    return True


def _load(f: DetectedFile, tmp: Path, job: ConversionJob):
    import ezdxf
    from ezdxf import recover

    src = f.path
    if f.ext == "dwg":
        src = libredwg.dwg_to_dxf(f.path, tmp / f"{f.path.stem}.dxf", cancel_event=job.cancel_event)
    try:
        try:
            doc, auditor = recover.readfile(src)  # repairs the common faults of real-world files
        except ValueError as exc:
            # LibreDWG's dwg2dxf (and some other programs) write handle "0", which ezdxf refuses
            fixed = tmp / f"{f.path.stem} (fixed handles).dxf"
            if "handle" not in str(exc).lower() or not fix_zero_handles(Path(src), fixed):
                raise
            doc, auditor = recover.readfile(fixed)
    except (OSError, ValueError, ezdxf.DXFError) as exc:
        raise ConversionError(
            f"Couldn't read “{f.name}”. Is it a valid {f.ext.upper()} drawing?", details=repr(exc)
        ) from exc
    if auditor.has_errors:
        auditor.fix_errors()
    return doc


def save_dxf(doc, dest: Path, version: str) -> None:
    """Save as DXF ``version``: R12 needs ezdxf's own exporter, R2000+ is a header change."""
    from ezdxf.addons import r12export

    if version == "R12":
        r12export.saveas(doc, dest)
        return
    import ezdxf

    doc.dxfversion = ezdxf.const.acad_release_to_dxf_version[version]
    doc.saveas(dest)


def _config(o: CadOptions):
    from ezdxf.addons.drawing import config

    policy = {
        "black_on_white": (config.ColorPolicy.BLACK, config.BackgroundPolicy.WHITE),
        "colour_light": (config.ColorPolicy.COLOR_SWAP_BW, config.BackgroundPolicy.WHITE),
        "colour_dark": (config.ColorPolicy.COLOR, config.BackgroundPolicy.BLACK),
    }[o.colours]
    return config.Configuration(
        color_policy=policy[0], background_policy=policy[1], lineweight_scaling=o.lineweight
    )


def _render(doc, layout_obj, backend, o: CadOptions) -> None:
    from ezdxf.addons.drawing import Frontend, RenderContext

    Frontend(RenderContext(doc), backend, config=_config(o)).draw_layout(layout_obj, finalize=True)


def _page(o: CadOptions):
    from ezdxf.addons.drawing import layout

    w, h = PAPERS_MM.get(o.paper, (0, 0))
    return layout.Page(w, h, layout.Units.mm, margins=layout.Margins.all(o.margin_mm))


def _layouts(doc, o: CadOptions):
    if o.layouts == "paper":
        papers = [lay for lay in doc.layouts if lay.name != "Model" and len(lay)]
        if papers:
            return papers
    return [doc.modelspace()]


class CadConverter(Converter):
    family = MediaFamily.CAD
    options_cls = CadOptions

    def targets(self, files: list[DetectedFile]):
        out = [
            self.target(
                tid,
                label,
                tid.split(":")[1],
                "Document" if tid == "cad:pdf" else "Vector" if tid == "cad:svg" else "Image",
            )
            for tid, label in RENDER.items()
        ]
        out.insert(
            0, self.target("dxf", "DXF", "dxf", "CAD", "DWG → DXF, or save as another DXF version")
        )
        return out

    def run(self, job: ConversionJob) -> list[Path]:
        from ezdxf.addons.drawing import layout, pymupdf, svg

        o: CadOptions = job.options
        tid = job.target.id
        outputs: list[Path] = []
        with tempfile.TemporaryDirectory(prefix="umc-cad-") as tmp:
            for i, f in enumerate(job.files):
                job.check_cancelled()
                job.report(i / len(job.files), f"Reading {f.name}")
                doc = _load(f, Path(tmp), job)
                stem = f.path.stem
                if tid == "dxf":
                    dest = unique_path(job.output_dir_for(f.path), stem, "dxf")
                    save_dxf(doc, dest, o.dxf_version)
                    outputs.append(dest)
                    continue
                job.report((i + 0.3) / len(job.files), f"Drawing {f.name}")
                layouts = _layouts(doc, o)
                settings = layout.Settings(fit_page=True)
                if tid == "cad:svg":
                    for lay in layouts:
                        backend = svg.SVGBackend()
                        _render(doc, lay, backend, o)
                        name = stem if len(layouts) == 1 else f"{stem}_{lay.name}"
                        dest = unique_path(job.output_dir_for(f.path), name, "svg")
                        dest.write_text(
                            backend.get_string(_page(o), settings=settings), encoding="utf-8"
                        )
                        outputs.append(dest)
                    continue
                pages = []
                for lay in layouts:
                    backend = pymupdf.PyMuPdfBackend()
                    _render(doc, lay, backend, o)
                    pages.append((lay, backend))
                if tid == "cad:pdf":
                    dest = unique_path(job.output_dir_for(f.path), stem, "pdf")
                    parts = []
                    for n, (_lay, backend) in enumerate(pages):
                        part = Path(tmp) / f"{stem}-{n}.pdf"
                        part.write_bytes(backend.get_pdf_bytes(_page(o), settings=settings))
                        parts.append(part)
                    if len(parts) == 1:
                        dest.write_bytes(parts[0].read_bytes())
                    else:
                        pdf.merge(parts, dest, True, [lay.name for lay, _ in pages])
                    outputs.append(dest)
                else:
                    fmt = job.target.ext
                    for lay, backend in pages:
                        png = backend.get_pixmap_bytes(
                            _page(o), fmt="png", settings=settings, dpi=o.dpi
                        )
                        name = stem if len(pages) == 1 else f"{stem}_{lay.name}"
                        dest = unique_path(job.output_dir_for(f.path), name, fmt)
                        if fmt == "png":
                            dest.write_bytes(png)
                        else:
                            import io

                            from PIL import Image

                            Image.open(io.BytesIO(png)).convert("RGB").save(
                                dest, "JPEG", quality=92
                            )
                        outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
