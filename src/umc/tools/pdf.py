"""PDF helpers built on PyMuPDF. All PyMuPDF usage is kept in this module."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from umc.core.errors import ConversionError

# Formats PyMuPDF can open and turn into PDF directly.
OPENABLE = {"pdf", "epub", "xps", "mobi", "fb2", "cbz"}


def parse_page_range(text: str, page_count: int) -> list[int]:
    """Parse ``"1-3, 5, 8-"`` into zero-based page indexes. Empty/“all” → every page."""
    text = (text or "").strip().lower()
    if text in ("", "all", "*"):
        return list(range(page_count))
    pages: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                start = int(a) if a.strip() else 1
                end = int(b) if b.strip() else page_count
            else:
                start = end = int(part)
        except ValueError as exc:
            raise ConversionError(
                f"“{text}” isn't a valid page range. Use something like 1-3, 5, 8-"
            ) from exc
        if start < 1 or end < start:
            raise ConversionError(f"“{part}” isn't a valid page range.")
        for p in range(start, min(end, page_count) + 1):
            pages.append(p - 1)
    if not pages:
        raise ConversionError(
            f"The page range doesn't include any page (document has {page_count})."
        )
    return pages


def open_document(path: Path) -> pymupdf.Document:
    try:
        return pymupdf.open(path)
    except Exception as exc:
        raise ConversionError(f"Couldn't open “{path.name}”. Is it a valid document?") from exc


def page_count(path: Path) -> int:
    with open_document(path) as doc:
        return doc.page_count


def to_pdf(src: Path, dest: Path) -> Path:
    """Convert an EPUB/XPS/MOBI/FB2/CBZ (or PDF) to PDF."""
    with open_document(src) as doc:
        if doc.is_pdf:
            doc.save(dest)
        else:
            pdf_bytes = doc.convert_to_pdf()
            with pymupdf.open("pdf", pdf_bytes) as pdf:
                pdf.save(dest, garbage=3, deflate=True)
    return dest


def extract_pages(src: Path, dest: Path, pages: list[int]) -> Path:
    with open_document(src) as doc, pymupdf.open() as out:
        for p in pages:
            out.insert_pdf(doc, from_page=p, to_page=p)
        out.save(dest, garbage=3, deflate=True)
    return dest


def extract_text(src: Path, dest: Path, pages: list[int] | None, mode: str) -> Path:
    """mode: "txt" | "html" | "md"."""
    with open_document(src) as doc:
        idx = pages if pages is not None else range(doc.page_count)
        if mode == "md":
            try:
                import pymupdf4llm  # optional, gives much nicer Markdown

                text = pymupdf4llm.to_markdown(doc, pages=list(idx), show_progress=False)
            except Exception:
                text = "\n\n---\n\n".join(doc[i].get_text("text") for i in idx)
        elif mode == "html":
            body = "\n".join(doc[i].get_text("xhtml") for i in idx)
            text = (
                '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
                f"<title>{src.stem}</title></head><body>\n{body}\n</body></html>\n"
            )
        else:
            text = "\n\f".join(doc[i].get_text("text") for i in idx)
    dest.write_text(text, encoding="utf-8")
    return dest


def render_pages(src: Path, pages: list[int], dpi: int, alpha: bool, grayscale: bool):
    """Yield ``(page_index, PIL.Image)`` for each page."""
    from PIL import Image

    colorspace = pymupdf.csGRAY if grayscale else pymupdf.csRGB
    with open_document(src) as doc:
        for i in pages:
            pix = doc[i].get_pixmap(dpi=dpi, alpha=alpha and not grayscale, colorspace=colorspace)
            mode = "L" if pix.n == 1 else ("RGBA" if pix.alpha else "RGB")
            yield i, Image.frombytes(mode, (pix.width, pix.height), pix.samples)


# Paper sizes in millimetres (portrait).
PAPER_SIZES_MM = {
    "a3": (297, 420),
    "a4": (210, 297),
    "a5": (148, 210),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
}
_MM = 72 / 25.4  # PDF points per millimetre


def parse_page_groups(text: str, page_count: int) -> list[list[int]]:
    """``"1-3, 4-10, 11-"`` → one list of zero-based pages per comma-separated group."""
    groups = [parse_page_range(part, page_count) for part in text.split(",") if part.strip()]
    if not groups:
        raise ConversionError("Enter at least one page range, e.g. 1-3, 4-10, 11-")
    return groups


def refit_pages(
    src: Path, dest: Path, paper: str, orientation: str, margin_mm: float, scale_up: bool
) -> Path:
    """Place every page of ``src`` onto ``paper``-sized pages with the given margins."""
    w_mm, h_mm = PAPER_SIZES_MM[paper]
    with open_document(src) as doc, pymupdf.open() as out:
        for page in doc:
            landscape = orientation == "landscape" or (
                orientation == "auto" and page.rect.width > page.rect.height
            )
            w, h = (h_mm, w_mm) if landscape else (w_mm, h_mm)
            new = out.new_page(width=w * _MM, height=h * _MM)
            m = margin_mm * _MM
            box = pymupdf.Rect(m, m, new.rect.width - m, new.rect.height - m)
            if box.is_empty:
                raise ConversionError("The margins are larger than the page.")
            if not scale_up:
                # Don't enlarge small pages: shrink the target box to the page's own size.
                pw, ph = page.rect.width, page.rect.height
                if pw <= box.width and ph <= box.height:
                    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
                    box = pymupdf.Rect(cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2)
            new.show_pdf_page(box, doc, page.number, keep_proportion=True)
        out.save(dest, garbage=3, deflate=True)
    return dest
