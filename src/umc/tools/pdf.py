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
        # A scan would give an empty file. (HTML is fine: it keeps the page images.)
        if mode in ("txt", "md") and not any(doc[i].get_text().strip() for i in idx):
            raise ConversionError(
                f"“{src.name}” has no text (it's a scan). Run “Searchable PDF” (OCR) on it first."
            )
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


# --------------------------------------------------------------------------- PDF toolkit

# Compression presets: (image DPI target, JPEG quality); None = lossless clean-up only.
COMPRESS_LEVELS = {
    "lossless": None,
    "recommended": (150, 75),
    "strong": (110, 60),
    "extreme": (72, 45),
}


def _open_unlocked(src: Path) -> pymupdf.Document:
    doc = open_document(src)
    if doc.needs_pass:
        doc.close()
        raise ConversionError(
            f"“{src.name}” is password-protected. Use “Remove password” on it first."
        )
    return doc


def _save(doc: pymupdf.Document, dest: Path, **kw) -> Path:
    doc.save(dest, garbage=4, deflate=True, clean=True, **kw)
    return dest


def merge(sources: list[Path], dest: Path, bookmarks: bool, titles: list[str]) -> Path:
    with pymupdf.open() as out:
        toc = []
        for src, title in zip(sources, titles, strict=True):
            with _open_unlocked(src) as doc:
                toc.append([1, title, out.page_count + 1])
                out.insert_pdf(doc)
        if bookmarks:
            out.set_toc(toc)
        return _save(out, dest)


# Ever stronger settings tried, in order, when a target size is set.
_LADDER = [(150, 75), (110, 60), (72, 45), (60, 35), (48, 30)]


def compress(
    src: Path, dest: Path, level: str, strip_metadata: bool, target_bytes: int = 0
) -> Path:
    preset = COMPRESS_LEVELS[level]
    attempts: list[tuple[int, int] | None] = [preset]
    if target_bytes:
        attempts += [p for p in _LADDER if preset is None or p[0] < preset[0]]
    for attempt in attempts:
        with _open_unlocked(src) as doc:
            if attempt:
                dpi, quality = attempt
                doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality)
            if strip_metadata:
                doc.set_metadata({})
                doc.del_xml_metadata()
            doc.subset_fonts()
            _save(doc, dest)
        if not target_bytes or dest.stat().st_size <= target_bytes:
            break
    return dest  # best effort: the smallest we could get


def rotate(src: Path, dest: Path, angle: int, page_range: str) -> Path:
    with _open_unlocked(src) as doc:
        for i in parse_page_range(page_range, doc.page_count):
            page = doc[i]
            page.set_rotation((page.rotation + angle) % 360)
        return _save(doc, dest)


def organize(src: Path, dest: Path, keep: str, reverse: bool) -> Path:
    with _open_unlocked(src) as doc:
        pages = parse_page_range(keep, doc.page_count)
        if reverse:
            pages.reverse()
        doc.select(pages)
        return _save(doc, dest)


def protect(
    src: Path,
    dest: Path,
    password: str,
    owner_password: str,
    allow_print: bool,
    allow_copy: bool,
    allow_edit: bool,
) -> Path:
    if not password:
        raise ConversionError("Enter a password to protect the PDF with.")
    perms = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perms |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perms |= pymupdf.PDF_PERM_COPY
    if allow_edit:
        perms |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM
        perms |= pymupdf.PDF_PERM_ASSEMBLE
    # The owner password unlocks everything, so it must differ from the open password or the
    # permissions would be ignored. Without one, use a random secret nobody knows.
    if not owner_password or owner_password == password:
        import secrets

        owner_password = secrets.token_urlsafe(24)
    with _open_unlocked(src) as doc:
        return _save(
            doc,
            dest,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=owner_password,
            permissions=perms,
        )


def unlock(src: Path, dest: Path, password: str) -> Path:
    with open_document(src) as doc:
        if doc.needs_pass and not doc.authenticate(password):
            raise ConversionError(f"That password doesn't open “{src.name}”.")
        return _save(doc, dest, encryption=pymupdf.PDF_ENCRYPT_NONE)


# --------------------------------------------------------------------------- OCR
# PyMuPDF ships Tesseract inside MuPDF; it only needs the language files (tools/tessdata.py).


def ocr_document() -> pymupdf.Document:
    """An empty PDF that :func:`ocr_add_image` / :func:`ocr_add_pdf` append pages to."""
    return pymupdf.open()


def _ocr_pixmap(out: pymupdf.Document, pix: pymupdf.Pixmap, language: str, tessdata: str) -> None:
    try:
        data = pix.pdfocr_tobytes(compress=True, language=language, tessdata=tessdata)
    except RuntimeError as exc:
        raise ConversionError("Text recognition (OCR) failed.", details=repr(exc)) from exc
    with pymupdf.open("pdf", data) as page_doc:
        out.insert_pdf(page_doc)


def ocr_add_image(out: pymupdf.Document, img, language: str, tessdata: str) -> None:
    """Recognise a PIL image and append it as a page (image + invisible, selectable text)."""
    from PIL import Image

    dpi = img.info.get("dpi", (0, 0))[0] or 0
    dpi = round(dpi) if dpi >= 72 else 300
    img = img.convert("L") if img.mode in ("1", "L", "LA", "I;16") else img.convert("RGB")
    if min(img.size) < 1000:  # small text recognises far better when upscaled
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        dpi *= 2
    cs = pymupdf.csGRAY if img.mode == "L" else pymupdf.csRGB
    pix = pymupdf.Pixmap(cs, img.width, img.height, img.tobytes(), False)
    pix.set_dpi(dpi, dpi)
    _ocr_pixmap(out, pix, language, tessdata)


def ocr_add_pdf(
    out: pymupdf.Document,
    src: Path,
    pages: list[int],
    language: str,
    tessdata: str,
    dpi: int,
    skip_text_pages: bool,
    on_page=lambda done, total: None,
) -> None:
    """Append ``pages`` of ``src``: pages with real text are copied, scanned ones recognised."""
    with _open_unlocked(src) as doc:
        for n, i in enumerate(pages):
            page = doc[i]
            if skip_text_pages and page.get_text().strip():
                out.insert_pdf(doc, from_page=i, to_page=i)
            else:
                pix = page.get_pixmap(dpi=dpi)
                pix.set_dpi(dpi, dpi)
                _ocr_pixmap(out, pix, language, tessdata)
            on_page(n + 1, len(pages))


def ocr_save(out: pymupdf.Document, dest: Path) -> Path:
    return _save(out, dest)


def ocr_text(out: pymupdf.Document) -> str:
    """Plain text of every page, pages separated by a blank line."""
    return "\n\n".join(page.get_text().strip() for page in out) + "\n"


# --------------------------------------------------------------------------- vector files


def vector_to_pdf(src: Path, ext: str, dest: Path) -> Path | None:
    """SVG or PDF-compatible Illustrator (.ai) → PDF. ``None`` if MuPDF can't read it (old AI)."""
    try:
        if ext == "svg":
            with pymupdf.open(src, filetype="svg") as doc:
                dest.write_bytes(doc.convert_to_pdf())
            return dest
        with pymupdf.open(src, filetype="pdf") as doc:  # Illustrator 9+ saves a PDF inside .ai
            if doc.needs_pass:
                raise ConversionError(f"“{src.name}” is password-protected.")
            doc.save(dest, garbage=3, deflate=True)
        return dest
    except ConversionError:
        raise
    except (RuntimeError, ValueError, pymupdf.FileDataError) as exc:
        if ext == "svg":
            raise ConversionError(
                f"Couldn't read “{src.name}”. Is it a valid SVG?", details=repr(exc)
            ) from exc
        return None


def pdf_page_count(src: Path) -> int:
    with pymupdf.open(src) as doc:
        return doc.page_count


def page_to_svg(src: Path, index: int, text_as_paths: bool) -> str:
    with pymupdf.open(src) as doc:
        return doc[index].get_svg_image(text_as_path=text_as_paths)


def render_page(src: Path, index: int, width_px: int, default_dpi: int, alpha: bool):
    """Render one page to a PIL image, ``width_px`` wide (0 = at ``default_dpi``)."""
    from PIL import Image

    with pymupdf.open(src) as doc:
        page = doc[index]
        zoom = width_px / page.rect.width if width_px else default_dpi / 72
        if page.rect.width * zoom * page.rect.height * zoom > 400_000_000:
            raise ConversionError("That output size is too large. Choose a smaller width.")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=alpha)
        mode = "RGBA" if pix.alpha else "RGB"
        return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


# --------------------------------------------------------------------------- PDF → Word


def pdf_to_docx(src: Path, dest: Path) -> Path:
    """PDF → DOCX keeping the layout (text boxes, tables, images, columns) with pdf2docx."""
    import logging

    from pdf2docx import Converter

    logging.getLogger("pdf2docx").setLevel(logging.WARNING)
    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ConversionError(
                f"“{src.name}” is password-protected. Use “Remove password” on it first."
            )
        if not any(page.get_text().strip() for page in doc):
            raise ConversionError(
                f"“{src.name}” has no text (it's a scan). Run “Searchable PDF” (OCR) on it first."
            )
    cv = Converter(str(src))
    try:
        cv.convert(str(dest), multi_processing=False)
    except Exception as exc:  # pdf2docx raises many kinds of errors on unusual PDFs
        dest.unlink(missing_ok=True)
        raise ConversionError(
            f"Couldn't turn “{src.name}” into a Word document.", details=repr(exc)
        ) from exc
    finally:
        cv.close()
    return dest


def write_sample_pdf(dest: Path, lines: list[str]) -> Path:
    """A one-page text PDF (used by the self-test)."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 80 + 24 * i), line, fontsize=14)
        doc.save(dest)
    return dest
