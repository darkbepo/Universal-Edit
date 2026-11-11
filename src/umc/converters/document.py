"""Document ↔ document.

Conversions are planned as the cheapest path through a graph of "edges" offered by the
available engines (PyMuPDF, Pandoc, LibreOffice). E.g. ``md → pdf`` becomes
``md →(pandoc) docx →(libreoffice) pdf``. Engines that aren't installed get a large cost, so
we prefer routes that work right now, and fall back to asking the user to install a tool.
"""

from __future__ import annotations

import heapq
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.converters.data import DATA_READ
from umc.core.detect import PRESENTATION_EXTS, SPREADSHEET_EXTS, DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt
from umc.tools import calibre, htmlpdf, libreoffice, pandoc, pdf

PYMUPDF, PANDOC, SOFFICE = "pymupdf", "pandoc", "libreoffice"
PDF2DOCX = "pdf2docx"
CALIBRE = "calibre"
# Built-in (no download): DOCX → HTML (mammoth), Markdown → HTML (markdown-it),
# HTML / TXT → PDF (PyMuPDF Story). Simpler layout than LibreOffice, so priced above it.
MAMMOTH, MARKDOWN, HTMLPDF = "mammoth", "markdown", "pymupdf-html"
# Calibre reads these and writes the e-book formats below (Kindle formats only through it)
CALIBRE_IN = ("epub", "mobi", "azw3", "fb2", "docx", "odt", "html", "txt", "md", "rtf")
EBOOK_OUT = ("mobi", "azw3", "epub", "fb2")

TEXT_TARGETS = [
    "pdf",
    "docx",
    "doc",
    "odt",
    "rtf",
    "txt",
    "md",
    "html",
    "epub",
    "mobi",
    "azw3",
    "fb2",
    "rst",
    "tex",
]
SHEET_TARGETS = ["xlsx", "xls", "ods", "csv", "pdf", "html"]
SLIDE_TARGETS = ["pptx", "ppt", "odp", "pdf"]

LABELS = {"md": "Markdown", "tex": "LaTeX", "txt": "TXT"}

# LibreOffice export filters (out_ext → filter) and import filters (in_ext → filter).
_LO_EXPORT = {
    "pdf": "writer_pdf_Export",
    "docx": "MS Word 2007 XML",
    "doc": "MS Word 97",
    "odt": "writer8",
    "rtf": "Rich Text Format",
    "txt": "Text (encoded):UTF8",
    "html": "HTML (StarWriter)",
}
_LO_CALC_EXPORT = {
    "csv": "Text - txt - csv (StarCalc):44,34,76",
    "pdf": "calc_pdf_Export",
    "html": "HTML (StarCalc)",
}
_LO_IMPRESS_EXPORT = {"pdf": "impress_pdf_Export"}
_LO_IMPORT = {
    "html": "HTML (StarWriter)",
    "txt": "Text (encoded):UTF8",
    "pdf": "writer_pdf_import",
    "csv": "CSV:44,34,76",
}


@dataclass(frozen=True)
class Edge:
    engine: str
    src: str
    dst: str
    cost: float


def _edges() -> list[Edge]:
    e: list[Edge] = []
    # PyMuPDF: PDF text extraction, and opening ebook/XPS formats as PDF.
    e += [
        Edge(PYMUPDF, "pdf", "txt", 1),
        Edge(PYMUPDF, "pdf", "md", 1.5),
        Edge(PYMUPDF, "pdf", "html", 2),
    ]
    e += [Edge(PYMUPDF, s, "pdf", 1.5) for s in ("epub", "xps", "mobi", "fb2", "cbz")]
    # Pandoc: great at structured text formats.
    for s in pandoc.READERS:
        for d in pandoc.WRITERS:
            if s != d:
                cost = 1.0
                if s == "txt":
                    cost = 2.0  # plain text read as Markdown — ok but not perfect
                if d in ("docx", "odt", "rtf"):
                    cost += 0.2  # LibreOffice is usually equal or better for these
                if d == "epub":
                    cost += 0.5  # adds a title page; a poor intermediate format
                e.append(Edge(PANDOC, s, d, cost))
    # LibreOffice Writer.
    writer_in = ["doc", "docx", "odt", "rtf", "txt", "html", "wpd"]
    for s in writer_in:
        for d in _LO_EXPORT:
            if s != d:
                e.append(Edge(SOFFICE, s, d, 1.5 if "html" in (s, d) else 1.0))
    e += [Edge(SOFFICE, "pdf", d, 3.5) for d in ("docx", "odt", "doc", "rtf")]
    # pdf2docx rebuilds the page layout (text boxes, tables, columns, images) as an editable DOCX;
    # cheaper than LibreOffice's PDF import, so PDF → ODT/DOC/RTF also go through it.
    e.append(Edge(PDF2DOCX, "pdf", "docx", 1))
    # Built in, so DOCX / Markdown / HTML / TXT → PDF work without LibreOffice (or Pandoc).
    # Priced above LibreOffice (docx→pdf 1.0, html→pdf 1.5) so it stays first when installed.
    e += [
        Edge(MAMMOTH, "docx", "html", 1.6),
        Edge(MARKDOWN, "md", "html", 1.3),
        Edge(HTMLPDF, "html", "pdf", 3.0),
        Edge(HTMLPDF, "txt", "pdf", 3.0),
    ]
    # Calibre: the only route to MOBI / AZW3; also reads Kindle files (→ EPUB opens up the rest).
    for s in CALIBRE_IN:
        for d in EBOOK_OUT:
            if s != d:
                e.append(
                    Edge(
                        CALIBRE, s, d, 1.2 if d in ("mobi", "azw3") or s in ("mobi", "azw3") else 3
                    )
                )
    # LibreOffice Calc / Impress.
    for s in SPREADSHEET_EXTS:
        for d in SHEET_TARGETS:
            if s != d:
                e.append(Edge(SOFFICE, s, d, 1))
    for s in PRESENTATION_EXTS:
        for d in SLIDE_TARGETS:
            if s != d:
                e.append(Edge(SOFFICE, s, d, 1))
    # Plain text loses all formatting: fine as a final target, poor as a stepping stone.
    return [Edge(x.engine, x.src, x.dst, x.cost + 2) if x.dst == "txt" else x for x in e]


EDGES = _edges()


def _engine_available(engine: str) -> bool:
    if engine == PANDOC:
        return pandoc.is_available()
    if engine == SOFFICE:
        return libreoffice.is_available()
    if engine == CALIBRE:
        return calibre.is_available()
    return True


def plan(src: str, dst: str, available: dict[str, bool] | None = None) -> list[Edge] | None:
    """Cheapest list of edges converting ``src`` to ``dst`` (``[]`` if they're equal)."""
    if src == dst:
        return []
    avail = available or {
        eng: _engine_available(eng) for eng in (PYMUPDF, PANDOC, SOFFICE, CALIBRE)
    }
    best: dict[str, float] = {src: 0.0}
    prev: dict[str, Edge] = {}
    heap = [(0.0, src)]
    while heap:
        cost, node = heapq.heappop(heap)
        if node == dst:
            break
        if cost > best.get(node, float("inf")):
            continue
        for edge in EDGES:
            if edge.src != node:
                continue
            c = cost + edge.cost + (0 if avail.get(edge.engine, True) else 100)
            if c < best.get(edge.dst, float("inf")):
                best[edge.dst] = c
                prev[edge.dst] = edge
                heapq.heappush(heap, (c, edge.dst))
    if dst not in prev:
        return None
    route = []
    node = dst
    while node != src:
        edge = prev[node]
        route.append(edge)
        node = edge.src
    return list(reversed(route))


def reachable(src: str) -> set[str]:
    seen = {src}
    stack = [src]
    while stack:
        node = stack.pop()
        for e in EDGES:
            if e.src == node and e.dst not in seen:
                seen.add(e.dst)
                stack.append(e.dst)
    return seen


@dataclass
class DocumentOptions:
    page_range: str = opt(
        "all", "Pages", help="For PDF sources. e.g. 1-3, 5, 8- (all = every page)"
    )
    ebook_title: str = opt(
        "",
        "Book title",
        targets=("doc:mobi", "doc:azw3", "doc:epub", "doc:fb2"),
        help="Shown in the e-reader's library. Empty = taken from the file.",
    )
    ebook_author: str = opt("", "Author", targets=("doc:mobi", "doc:azw3", "doc:epub", "doc:fb2"))
    toc: bool = opt(
        False,
        "Add a table of contents",
        targets=("doc:html", "doc:epub", "doc:docx", "doc:odt", "doc:md"),
    )
    paper: str = opt(
        "original",
        "Page size",
        targets=("doc:pdf",),
        choices=[
            ("original", "Keep original"),
            ("a4", "A4"),
            ("letter", "US Letter"),
            ("legal", "US Legal"),
            ("a3", "A3"),
            ("a5", "A5"),
        ],
    )
    orientation: str = opt(
        "auto",
        "Orientation",
        targets=("doc:pdf",),
        choices=[("auto", "Match each page"), ("portrait", "Portrait"), ("landscape", "Landscape")],
        show_if=lambda v: v["paper"] != "original",
    )
    margin_mm: int = opt(
        0,
        "Margins",
        kind="slider",
        min=0,
        max=50,
        unit="mm",
        targets=("doc:pdf",),
        show_if=lambda v: v["paper"] != "original",
    )
    scale_up: bool = opt(
        True,
        "Enlarge small pages to fill the paper",
        targets=("doc:pdf",),
        show_if=lambda v: v["paper"] != "original",
    )


class DocumentConverter(Converter):
    family = MediaFamily.DOCUMENT
    options_cls = DocumentOptions

    def targets(self, files: list[DetectedFile]):
        common: set[str] | None = None
        for f in files:
            r = reachable(f.ext)
            common = r if common is None else common & r
        common = common or set()
        order = TEXT_TARGETS + [t for t in SHEET_TARGETS + SLIDE_TARGETS if t not in TEXT_TARGETS]
        skip: set[str] = set()
        if files and all(f.ext in DATA_READ for f in files):
            skip = {"csv", "xlsx"}  # the Data group converts these without LibreOffice
        if len(files) == 1 and files[0].ext != "pdf":
            skip.add(files[0].ext)  # converting to the same format is pointless
        return [
            self.target(f"doc:{ext}", LABELS.get(ext, ext.upper()), ext, "Document")
            for ext in order
            if ext in common and ext not in skip
        ]

    def run(self, job: ConversionJob) -> list[Path]:
        o: DocumentOptions = job.options
        dst = job.target.ext
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Converting {f.name} ({i + 1}/{n})")
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, dst)
            convert_document(f, dst, dest, job, page_range=o.page_range, toc=o.toc)
            if dst == "pdf" and o.paper != "original":
                job.report((i + 0.9) / n, f"Fitting pages of {f.name} to {o.paper.upper()}")
                fitted = dest.with_name(dest.stem + ".fitting.pdf")
                try:
                    pdf.refit_pages(dest, fitted, o.paper, o.orientation, o.margin_mm, o.scale_up)
                    fitted.replace(dest)
                finally:
                    fitted.unlink(missing_ok=True)
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs


def convert_document(
    f: DetectedFile,
    dst: str,
    dest: Path,
    job: ConversionJob,
    *,
    page_range: str = "all",
    toc: bool = False,
) -> Path:
    """Convert one document to ``dest`` following the planned route."""
    src_path, src_ext = f.path, f.ext
    with tempfile.TemporaryDirectory(prefix="umc-doc-") as tmp:
        tmp_dir = Path(tmp)
        # Apply page selection up front for PDFs.
        if src_ext == "pdf" and page_range.strip().lower() not in ("", "all", "*"):
            pages = pdf.parse_page_range(page_range, pdf.page_count(src_path))
            subset = tmp_dir / f"{src_path.stem}.pdf"
            pdf.extract_pages(src_path, subset, pages)
            src_path = subset
        route = plan(src_ext, dst)
        if route is None:
            raise ConversionError(f"Can't convert {src_ext.upper()} to {dst.upper()}.")
        if not route:
            shutil.copyfile(src_path, dest)
            return dest
        current, current_ext = src_path, src_ext
        for step_no, edge in enumerate(route):
            job.check_cancelled()
            last = step_no == len(route) - 1
            out = dest if last else tmp_dir / f"step{step_no}" / f"{src_path.stem}.{edge.dst}"
            out.parent.mkdir(parents=True, exist_ok=True)
            _run_edge(edge, current, current_ext, out, job, toc=toc and last)
            current, current_ext = out, edge.dst
    return dest


def _run_edge(edge: Edge, src: Path, src_ext: str, out: Path, job: ConversionJob, toc: bool):
    if edge.engine == MAMMOTH:
        htmlpdf.docx_to_html(src, out)
    elif edge.engine == MARKDOWN:
        htmlpdf.markdown_to_html(src, out)
    elif edge.engine == HTMLPDF:
        if src_ext == "txt":
            html_src = out.with_suffix(".html")
            htmlpdf.text_to_html(src, html_src)
            try:
                htmlpdf.html_to_pdf(html_src, out)
            finally:
                html_src.unlink(missing_ok=True)
        else:
            htmlpdf.html_to_pdf(src, out)
    elif edge.engine == PDF2DOCX:
        pdf.pdf_to_docx(src, out)
    elif edge.engine == CALIBRE:
        o = job.options
        calibre.convert(
            src,
            out,
            cancel_event=job.cancel_event,
            title=getattr(o, "ebook_title", ""),
            authors=getattr(o, "ebook_author", ""),
        )
    elif edge.engine == PYMUPDF:
        if edge.dst == "pdf":
            pdf.to_pdf(src, out)
        else:
            pdf.extract_text(src, out, None, edge.dst)
    elif edge.engine == PANDOC:
        pandoc.convert(src, src_ext, out, edge.dst, cancel_event=job.cancel_event, toc=toc)
    else:
        if src_ext in SPREADSHEET_EXTS:
            export = _LO_CALC_EXPORT.get(edge.dst)
        elif src_ext in PRESENTATION_EXTS:
            export = _LO_IMPRESS_EXPORT.get(edge.dst)
        else:
            export = _LO_EXPORT.get(edge.dst)
        libreoffice.convert(
            src,
            edge.dst,
            out,
            cancel_event=job.cancel_event,
            export_filter=export,
            import_filter=_LO_IMPORT.get(src_ext),
        )
