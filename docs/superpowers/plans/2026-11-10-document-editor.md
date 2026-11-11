# Document Editor (desktop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Word-like editor for one DOCX / ODT / RTF / DOC / MD / TXT / HTML file, reached through the Convert | Edit switch in the desktop app.

**Architecture:** Qt-free code in `src/umc/doceditor/` turns files into a small HTML subset (+ a picture map) and turns that subset back into files: DOCX with our own python-docx reader / writer, MD / TXT / HTML directly, PDF via the built-in PyMuPDF layout (or LibreOffice when installed), everything else through the existing document route planner via DOCX. The Qt side (`src/umc/gui/doceditor/`) edits with `QTextEdit` and exports by walking the `QTextDocument` into the same subset — it never parses Qt's own `toHtml()`.

**Tech Stack:** Python 3.11+, PySide6-Essentials (`QTextEdit` / `QTextDocument`), python-docx (MIT), lxml, Python-Markdown, PyMuPDF, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-10-document-editor-design.md` (phase 1 only; the website gets its own plan after this one ships).

## Global Constraints

- Only `src/umc/gui/` imports Qt. `src/umc/doceditor/` must import without PySide6.
- The converter screen and the image editor behave exactly as before.
- Editable extensions: `EDITABLE_DOCUMENT_EXTS = {"docx", "odt", "rtf", "doc", "md", "txt", "html", "htm"}` (detect already maps `htm` → `html`). Edit is offered only for **exactly one** such file.
- New dependency: declare `python-docx>=1.1` in `pyproject.toml` (MIT; already bundled through pdf2docx — `packaging/umc.spec` already has `collect_data_files("docx")` and the `docx` hidden import, keep them).
- Limits: refuse files over 50 MB or documents over 2 000 000 characters with `"This document is too large to edit here."`
- Temporary folders: `tempfile.TemporaryDirectory(prefix="umc-docedit-")`.
- Never overwrite silently: Save writes back to the file the user opened; Save as relies on the system dialog's replace prompt.
- User-facing errors are `ConversionError` messages in plain language; details go to the log. A missing Pandoc / LibreOffice raises `ToolMissingError` and the GUI shows `ToolMissingDialog`, retrying after a successful install.
- Keep references to every worker and its `QThread` until `QThread.finished` (CLAUDE.md rule).
- Work in the worktree `.claude/worktrees/doc-editor` (branch `doc-editor`) with its own `.venv`. Commits: author `darkbepo`, imperative subject, **no co-author or session trailers**; `git add` only the paths each task lists.
- Lint: `ruff check . && ruff format --check .` must pass before every commit.

## Review Focus

1. **Non-Latin text and emoji** (Danish æøå, Cyrillic, Chinese, 😀) and Windows-1252 TXT must survive open → save in DOCX, TXT, MD and HTML — tests in Task 3 (`test_unicode_round_trip`) and Task 5 (`test_save_txt_md_unicode`).
2. **Empty documents** (0-byte TXT, a DOCX with no paragraphs) must open as one empty paragraph and save without error — test in Task 4 (`test_open_empty_files`).
3. **Lists nested three levels deep and lists inside table cells** must keep their levels — tests in Task 2/3 (`test_nested_lists_round_trip`, `test_list_in_table_cell`) and Task 6 (`test_export_nested_list_in_table`).
4. **Pictures Qt can't show** (EMF / WMF / TIFF inside a DOCX) must become PNG when Pillow can read them, otherwise be dropped with the warning "pictures in a format the editor can't show" — never crash — test in Task 3 (`test_unsupported_picture_warns`).
5. **Saving over a file that is open in Word / read-only** must fail with a plain message and leave both the file and the "unsaved" state untouched — tests in Task 5 (`test_save_locked_file_message`) and Task 9 (`test_failed_save_stays_unsaved`).

---

## File structure

```
src/umc/doceditor/__init__.py
src/umc/doceditor/types.py        # OpenedDocument, SaveContent, DocImages, constants, warning_text()
src/umc/doceditor/html_subset.py  # normalise_html(): any HTML → the subset (+ pictures)
src/umc/doceditor/docx_writer.py  # subset HTML + pictures → DOCX
src/umc/doceditor/docx_reader.py  # DOCX → OpenedDocument (+ warnings)
src/umc/doceditor/routes.py       # convert_file(): one file through DocumentConverter
src/umc/doceditor/io.py           # open_document()
src/umc/doceditor/save.py         # save_document(), save_formats()
src/umc/gui/doceditor/__init__.py
src/umc/gui/doceditor/text_view.py      # DocTextView (QTextEdit): load, page column, page-break lines, pictures
src/umc/gui/doceditor/html_export.py    # QTextDocument → SaveContent
src/umc/gui/doceditor/formatting.py     # cursor operations (bold, styles, lists, tables, …)
src/umc/gui/doceditor/format_bar.py     # formatting bar + table menu
src/umc/gui/doceditor/find_bar.py       # find & replace
src/umc/gui/doceditor/doc_editor_page.py
tests/test_doceditor_docx.py, tests/test_doceditor_io.py, tests/test_doceditor_save.py
tests/test_gui_doceditor.py
```

## The HTML subset (used by every task)

Blocks: `p`, `h1`–`h3`, `blockquote` (containing `p`), `pre` (one per code paragraph), `ul` / `ol` / `li`
(nested lists = nested `ul`/`ol` inside `li`), `table` / `tr` / `td` (`colspan`, `rowspan`; cells contain
blocks), `hr`. Paragraph style attributes on `p` / `h1`–`h3` / `li`: `text-align:left|center|right|justify`,
`margin-left:{36*n}pt` (indent level n ≥ 1), `page-break-before:always`.
Inline: `b`, `i`, `u`, `s`, `a href`, `img src width height` (px), `br`, `span style` with any of
`font-family`, `font-size:{n}pt`, `color:#rrggbb`, `background-color:#rrggbb`.
Pictures: `src="img/{n}.{ext}"`, bytes in `DocImages` (`ext` ∈ png, jpg, gif, bmp, webp).
Output is a fragment (no `<html>`/`<body>`), tags lowercase, colours lowercase `#rrggbb`.

---

### Task 1: Types, subset normaliser and worktree setup

**Files:**
- Create: `src/umc/doceditor/__init__.py`, `src/umc/doceditor/types.py`, `src/umc/doceditor/html_subset.py`
- Modify: `pyproject.toml` (add `"python-docx>=1.1",` after `"pdf2docx>=0.5.8",`)
- Test: `tests/test_doceditor_io.py`

**Interfaces:**
- Produces (`types.py`):
  - `DocImages = dict[str, bytes]`
  - `@dataclass class OpenedDocument: html: str; images: DocImages; source_format: str; warnings: list[str] = field(default_factory=list)`
  - `@dataclass class SaveContent: html: str; images: DocImages; markdown: str | None = None; plain_text: str | None = None`
  - `EDITABLE_DOCUMENT_EXTS: frozenset[str]`, `MAX_FILE_BYTES = 50 * 1024 * 1024`, `MAX_CHARS = 2_000_000`, `TOO_LARGE = "This document is too large to edit here."`
  - Warning names (constants, exact strings): `W_HEADERS = "headers and footers"`, `W_TEXTBOX = "text boxes and shapes"`, `W_COLUMNS = "multiple columns"`, `W_TRACKED = "tracked changes"`, `W_COMMENTS = "comments"`, `W_NOTES = "footnotes and endnotes"`, `W_FIELDS = "fields"`, `W_OBJECTS = "embedded objects"`, `W_PICTURES = "pictures in a format the editor can't show"`
  - `warning_text(warnings: list[str]) -> str`
  - `next_image_name(images: DocImages, ext: str) -> str` → `"img/{n}.{ext}"` with n = 1 + count.
- Produces (`html_subset.py`): `normalise_html(html: str, base_dir: Path | None = None) -> tuple[str, DocImages]`, `text_length(html: str) -> int` (characters of visible text).

- [ ] **Step 1: Set up the worktree** — from the main checkout:
  `powershell -ExecutionPolicy Bypass -File G:\Claude\Universal-Media-Converter\scripts\setup_worktree.ps1 -Worktree G:\Claude\Universal-Media-Converter\.claude\worktrees\doc-editor`, then add the dependency line and `.venv\Scripts\pip install -e ".[dev]"`.
  Expected: `.venv\Scripts\python -c "import docx, umc"` exits 0.

- [ ] **Step 2: Write the failing tests**

```python
def test_warning_text():
    assert warning_text([W_HEADERS, W_COMMENTS]) == (
        "This document has parts the editor can't keep: headers and footers, comments. "
        "They will be lost if you save over the original — use Save as to keep a copy."
    )
    assert warning_text([]) == ""


def test_normalise_keeps_subset_and_drops_the_rest():
    html, imgs = normalise_html(
        '<html><body><h1 class="x">T</h1><script>bad()</script>'
        '<p style="text-align:center;color:red;margin-top:3px">a <strong>b</strong> '
        '<span style="color:RGB(255,0,0);font-size:12pt;position:absolute">c</span>'
        '<font face="Arial">d</font></p><div>e</div><h4>f</h4></body></html>'
    )
    assert "<script" not in html and "bad()" not in html and "class=" not in html
    assert "<h1>T</h1>" in html
    assert '<p style="text-align:center">' in html  # unknown block styles dropped
    assert "<b>b</b>" in html  # strong → b
    assert 'style="color:#ff0000;font-size:12pt"' in html  # colour normalised, position dropped
    assert '<span style="font-family:Arial">d</span>' in html  # <font face> → span
    assert "<p>e</p>" in html and "<h3>f</h3>" in html  # div → p, h4–h6 → h3
    assert imgs == {}


def test_normalise_images(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    html, imgs = normalise_html(
        f'<p><img src="pic.png" width="10"><img src="data:image/png;base64,{b64(PNG_1PX)}">'
        '<img src="https://example.com/x.png"></p>',
        base_dir=tmp_path,
    )
    assert list(imgs) == ["img/1.png", "img/2.png"] and "example.com" not in html


def test_text_length():
    assert text_length("<p>ab <b>c</b></p><p>d</p>") == 5
```
(`PNG_1PX` / `b64` are small helpers at the top of the test file: a 1×1 PNG made with Pillow.)

- [ ] **Step 3: Run** `.venv\Scripts\pytest tests/test_doceditor_io.py -v` — Expected: FAIL (module not found).

- [ ] **Step 4: Implement** `types.py` and `html_subset.py`. Parse with `lxml.html.fragment_fromstring(..., create_parent="div")`, walk the tree and rebuild an allowed-only tree: unknown inline tags are unwrapped (children kept), unknown block tags (`div`, `section`, `article`, …) become `p`, `strong`/`em`/`ins`/`del`/`strike` map to `b`/`i`/`u`/`s`, `script`/`style`/`head`/`iframe`/`object` are removed with their content, attributes other than the allowed ones are removed, colours (`red`, `rgb()`, `#abc`) become `#rrggbb` (named colours via `PIL.ImageColor.getrgb`), `font-size` in px is converted to pt (× 0.75). Images: `data:` URIs decoded, relative paths read from `base_dir` (never outside it), anything else dropped; JPEG ext is `jpg`.

- [ ] **Step 5: Run the tests again** — Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add pyproject.toml src/umc/doceditor/__init__.py src/umc/doceditor/types.py src/umc/doceditor/html_subset.py tests/test_doceditor_io.py
git commit -m "Add the document editor's HTML subset and types"
```

---

### Task 2: DOCX writer

**Files:**
- Create: `src/umc/doceditor/docx_writer.py`
- Test: `tests/test_doceditor_docx.py`

**Interfaces:**
- Consumes: the HTML subset, `DocImages` (Task 1).
- Produces: `write(html: str, images: DocImages, dest: Path) -> Path`.

- [ ] **Step 1: Write the failing tests** (check the DOCX with python-docx directly; the reader comes in Task 3):

```python
def test_write_headings_runs_alignment(tmp_path):
    p = write(
        '<h1>Title</h1><p style="text-align:center">a <b>b</b> <i>c</i> <u>d</u> <s>e</s> '
        '<span style="font-family:Arial;font-size:14pt;color:#ff0000;background-color:#ffcc00">f</span></p>',
        {},
        tmp_path / "o.docx",
    )
    d = docx.Document(p)
    assert d.paragraphs[0].style.name == "Heading 1"
    para = d.paragraphs[1]
    assert para.alignment == WD_ALIGN_PARAGRAPH.CENTER
    runs = {r.text.strip(): r for r in para.runs if r.text.strip()}
    assert runs["b"].bold and runs["c"].italic and runs["d"].underline and runs["e"].font.strike
    f = runs["f"].font
    assert f.name == "Arial" and f.size.pt == 14 and str(f.color.rgb) == "FF0000"
    assert runs["f"]._r.rPr.find(qn("w:shd")).get(qn("w:fill")) == "FFCC00"


def test_write_lists_restart_numbering(tmp_path):
    d = docx.Document(
        write("<ol><li>a</li></ol><p>x</p><ol><li>b</li></ol>", {}, tmp_path / "o.docx")
    )
    num_ids = [
        p._p.pPr.numPr.numId.val
        for p in d.paragraphs
        if p._p.pPr is not None and p._p.pPr.numPr is not None
    ]
    assert len(set(num_ids)) == 2


def test_write_table_merges_picture_link_break_hr_indent(tmp_path):
    ...
    # table 2×2 with colspan=2 in the first row → first row has one w:tc with gridSpan 2;
    # <img src="img/1.png" width="96"> → one inline picture 1 inch wide (914400 EMU);
    # <a href="https://x.org">x</a> → a w:hyperlink whose relationship target is https://x.org;
    # <p style="page-break-before:always">n</p> → paragraph_format.page_break_before is True;
    # <hr> → empty paragraph with a w:pBdr/w:bottom border;
    # <p style="margin-left:72pt">i</p> → paragraph_format.left_indent.pt == 72
```
Write each of the commented cases as its own assertion in that test.

- [ ] **Step 2: Run** `.venv\Scripts\pytest tests/test_doceditor_docx.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `write()`** on `docx.Document()` (default template). Mapping, which Task 3 mirrors exactly:
  - `h1`–`h3` → styles "Heading 1"–"Heading 3"; `blockquote > p` → "Quote"; `pre` → a paragraph style "Code" (created if missing: Consolas 10 pt, based on Normal); `p` → "Normal".
  - Lists: level = nesting depth (1-based, clamped to 9). Bullets use the "List Bullet" style's abstract numbering, numbers the "List Number" one; each top-level `ul`/`ol` element gets its **own new `w:num`** (with `w:lvlOverride/w:startOverride w:val=1` for numbered lists) and every `li` paragraph gets `numPr` (`ilvl` = level − 1, that `numId`). Text inside `li` before a nested list is the item's paragraph.
  - Tables: `add_table(rows, grid_cols)`, style "Table Grid"; `colspan` → `cell.merge` across, `rowspan` → merge down; cell blocks written recursively into the cell (first paragraph reused).
  - Runs: `b/i/u/s` → bold / italic / underline / strike; span `font-family` → `font.name` (also `w:eastAsia`), `font-size` → `font.size`, `color` → `font.color.rgb`, `background-color` → `w:shd w:val="clear" w:fill=RRGGBB`. Nested inline tags combine.
  - `a href` → `w:hyperlink` with `r:id` from `part.relate_to(url, RT.HYPERLINK, is_external=True)`, its runs styled "Hyperlink" (create the character style if missing: blue, underlined).
  - `img` → `run.add_picture(BytesIO(bytes), width=Emu(px * 9525))` (height likewise when given).
  - `br` → `run.add_break()`; `page-break-before:always` → `paragraph_format.page_break_before = True`; `text-align` → alignment; `margin-left` → `left_indent = Pt(n)`; `hr` → empty paragraph with a bottom border (`w:pBdr/w:bottom w:val="single" w:sz="6"`).
  - An empty fragment writes one empty paragraph.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/doceditor/docx_writer.py tests/test_doceditor_docx.py
git commit -m "Add the document editor's DOCX writer"
```

---

### Task 3: DOCX reader

**Files:**
- Create: `src/umc/doceditor/docx_reader.py`
- Test: `tests/test_doceditor_docx.py`

**Interfaces:**
- Consumes: `write()` (Task 2) for round trips; types (Task 1).
- Produces: `read(path: Path) -> OpenedDocument` (`source_format="docx"`), output already in the subset.

- [ ] **Step 1: Write the failing tests**

```python
CASES = [
    "<h1>T</h1><h2>U</h2><h3>V</h3>",
    '<p style="text-align:justify">a <b>b</b> <i>c</i> <u>d</u> <s>e</s></p>',
    '<p><span style="font-family:Arial;font-size:14pt;color:#ff0000;background-color:#ffcc00">f</span></p>',
    "<blockquote><p>q</p></blockquote><pre>code()</pre>",
    '<p style="margin-left:72pt">i</p><p style="page-break-before:always">n</p><hr>',
    '<p><a href="https://x.org/">x</a></p>',
    '<table><tr><td colspan="2"><p>a</p></td></tr><tr><td><p>b</p></td><td><p>c</p></td></tr></table>',
]


@pytest.mark.parametrize("html", CASES)
def test_round_trip(tmp_path, html):
    expected, _ = normalise_html(html)
    assert read(write(html, {}, tmp_path / "o.docx")).html == expected


def test_nested_lists_round_trip(tmp_path):
    html = "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul><ol><li>d</li></ol>"
    assert read(write(html, {}, tmp_path / "o.docx")).html == normalise_html(html)[0]


def test_list_in_table_cell(tmp_path): ...  # <td><ul><li>a</li><li>b</li></ul></td> survives
def test_picture_round_trip(tmp_path): ...  # img/1.png bytes equal, width="96" kept
def test_unicode_round_trip(tmp_path):
    html = "<p>Blåbærgrød — Привет — 你好 — 😀</p>"
    assert read(write(html, {}, tmp_path / "o.docx")).html == html


def test_warnings(tmp_path):
    d = docx.Document()
    d.add_paragraph("x")
    d.sections[0].header.paragraphs[0].text = "Head"
    d.add_comment(
        d.paragraphs[0].runs or [d.paragraphs[0].add_run("y")], text="note"
    )  # python-docx ≥ 1.2; skip if missing
    p = tmp_path / "w.docx"
    d.save(p)
    assert {W_HEADERS, W_COMMENTS} <= set(read(p).warnings)


def test_tracked_changes_warn(
    tmp_path,
): ...  # inject <w:ins> around a run via lxml → W_TRACKED; text still read
def test_unsupported_picture_warns(
    tmp_path,
): ...  # replace a picture part's blob with bytes Pillow can't open, content type image/x-emf → W_PICTURES, no <img>
def test_plain_docx_has_no_warnings(tmp_path): ...  # write("<p>a</p>") → warnings == []
```
`test_warnings` uses `pytest.importorskip`-style guard: `if not hasattr(d, "add_comment"): pytest.skip(...)`.

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement `read()`** — the mirror of Task 2, walking `document.element.body` in order (paragraphs and tables):
  - Paragraph style (by style name, following `base_style`): "Heading 1–3" → `h1`–`h3` (Heading 4–9 and "Title" → `h3`/`h1`), "Quote" / "Intense Quote" → `blockquote`, "Code" / "HTML Preformatted" / "Source Code" → `pre`; else `p`.
  - Lists: `numPr` (direct or from the style) → look up `numId` → `abstractNum` → `w:lvl[ilvl]/w:numFmt`: `bullet` → `ul`, anything else → `ol`. Consecutive list paragraphs with the same `numId` form one list; `ilvl` gives nesting.
  - Tables: read `w:tr`/`w:tc` XML directly: `w:gridSpan` → `colspan`; `w:vMerge w:val="restart"` starts a cell, a bare `w:vMerge` adds 1 to the `rowspan` of the cell started above in the same grid column and emits nothing.
  - Runs: direct run properties only (`bold`, `italic`, `underline` truthy, `font.strike`, `font.name`, `font.size`, `font.color.rgb`, `w:shd/@w:fill` or `w:highlight` → `#rrggbb` via the Word highlight colour table). Adjacent runs with identical formatting are merged. Inside headings, `font.size` and `bold` are not emitted.
  - `w:hyperlink` (`paragraph.iter_inner_content()`) → `a href` (external address; internal anchors become plain text). `w:br w:type="page"` → the following content starts a new paragraph with `page-break-before:always`; other `w:br` → `br`; `w:tab` → a space.
  - Pictures (`a:blip/@r:embed` → image part): png / jpeg / gif / bmp / webp kept; other types converted to PNG with Pillow; when Pillow fails, skip and add `W_PICTURES`. Size from `wp:extent` (EMU ÷ 9525 → px).
  - Empty bottom-bordered paragraph → `hr`. `left_indent` on non-list paragraphs → `margin-left:{36*round(pt/36)}pt` when ≥ 1 level.
  - Warnings (XML search, each added once): non-empty header/footer of any section that isn't linked to previous → `W_HEADERS`; `w:txbxContent`, `wps:wsp`, `v:shape` → `W_TEXTBOX`; `w:cols/@w:num` > 1 → `W_COLUMNS`; `w:ins`, `w:del`, `w:moveFrom`, `w:moveTo` → `W_TRACKED` (inserted text is kept, deleted text dropped); comments part or `w:commentReference` → `W_COMMENTS`; `w:footnoteReference` / `w:endnoteReference` → `W_NOTES`; `w:fldSimple` / `w:instrText` other than `PAGE` / `NUMPAGES` → `W_FIELDS` (field results kept as text); `w:object` → `W_OBJECTS`.
  - Finally pass the result through `normalise_html` so the output is canonical.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/doceditor/docx_reader.py tests/test_doceditor_docx.py
git commit -m "Add the document editor's DOCX reader"
```

---

### Task 4: Opening any editable document

**Files:**
- Create: `src/umc/doceditor/routes.py`, `src/umc/doceditor/io.py`
- Test: `tests/test_doceditor_io.py`

**Interfaces:**
- Consumes: `read()` (Task 3), `normalise_html()` / `text_length()` (Task 1), `umc.converters.document.DocumentConverter` / `DocumentOptions`, `umc.core.options` (`opts.build`, `opts.defaults`), `umc.tools.htmlpdf.read_text`.
- Produces:
  - `routes.convert_file(src: Path, dst_ext: str, dest: Path, cancel_event: threading.Event | None = None) -> Path` — runs `DocumentConverter` with target `doc:{dst_ext}` and default `DocumentOptions` into a `umc-docedit-` temp folder, moves the result to `dest`. No target → `ConversionError(f"Can't save as {dst_ext.upper()}.")`. `ToolMissingError` passes through.
  - `io.open_document(path: Path, cancel_event: threading.Event | None = None) -> OpenedDocument`.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_markdown(media):
    d = open_document(media / "doc.md")
    assert (
        d.source_format == "md"
        and "<h1>Title</h1>" in d.html
        and "<b>world</b>" in d.html
        and "<ul>" in d.html
    )


def test_open_txt_cp1252(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("Café\nline 2\n".encode("cp1252"))
    assert open_document(p).html == "<p>Café</p><p>line 2</p>"


def test_open_html_with_local_picture(tmp_path): ...  # img next to the file → img/1.png in images
def test_open_docx(tmp_path): ...  # write() then open_document() == read()
def test_open_empty_files(tmp_path):
    (tmp_path / "e.txt").write_bytes(b"")
    docx.Document().save(tmp_path / "e.docx")
    for name in ("e.txt", "e.docx"):
        assert open_document(tmp_path / name).html == "<p></p>"


def test_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "MAX_CHARS", 10)
    p = tmp_path / "big.txt"
    p.write_text("x" * 11)
    with pytest.raises(ConversionError, match="too large to edit here"):
        open_document(p)


def test_open_odt_needs_a_tool(tmp_path, monkeypatch):
    # route planner says Pandoc/LibreOffice are missing → ToolMissingError, not a crash
    ...


@pytest.mark.skipif(
    not (shutil.which("pandoc") or find_soffice()), reason="needs Pandoc or LibreOffice"
)
def test_open_odt_round_trip(
    tmp_path,
): ...  # write DOCX → convert_file to odt → open_document: source_format "odt", heading kept
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement.** `open_document`: refuse `stat().st_size > MAX_FILE_BYTES` with `TOO_LARGE`; `ext = detect(path).ext`; `docx` → `read`; `md` → `markdown.markdown(read_text, extensions=["tables", "fenced_code", "sane_lists"])` → `normalise_html`; `txt` → one `<p>` per line (HTML-escaped; a trailing newline adds no paragraph; empty file → `<p></p>`); `html` → `normalise_html(text, base_dir=path.parent)`; `odt` / `rtf` / `doc` → `convert_file(path, "docx", tmp/…)` then `read`, keeping `source_format = ext`. Empty result → `"<p></p>"`. Then `text_length(html) > MAX_CHARS` → `TOO_LARGE` (read `MAX_CHARS` from the module at call time so the test can patch it). Other extensions → `ConversionError("This file type can't be edited.")`.

- [ ] **Step 4: Run the tests again** — Expected: PASS (ODT round trip skipped without tools).

- [ ] **Step 5: Commit**
```bash
git add src/umc/doceditor/routes.py src/umc/doceditor/io.py tests/test_doceditor_io.py
git commit -m "Open DOCX, ODT, RTF, DOC, Markdown, TXT and HTML for editing"
```

---

### Task 5: Saving to every format

**Files:**
- Create: `src/umc/doceditor/save.py`
- Test: `tests/test_doceditor_save.py`

**Interfaces:**
- Consumes: `write()` (Task 2), `convert_file()` (Task 4), `umc.converters.document.plan`, `SOFFICE`, `LABELS`, `reachable`, `TEXT_TARGETS`, `umc.tools.pdf.layout_html`, `htmlpdf._page`.
- Produces:
  - `save_formats() -> list[tuple[str, str]]` — `(ext, label)`: `docx` "Word (DOCX)", `odt` "OpenDocument (ODT)", `rtf` "Rich Text (RTF)", `doc` "Word 97–2003 (DOC)", `pdf` "PDF", `md` "Markdown", `txt` "Plain text (TXT)", `html` "Web page (HTML)", then every other ext in `TEXT_TARGETS` that is in `reachable("docx")`, labelled `LABELS.get(ext, ext.upper())`.
  - `save_document(content: SaveContent, dest: Path, fmt: str, cancel_event: threading.Event | None = None) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
CONTENT = SaveContent(html='<h1>Hi</h1><p>Blåbær <b>bold</b><img src="img/1.png" width="20"></p>',
                      images={"img/1.png": PNG_1PX}, markdown="# Hi\n\nBlåbær **bold**![](img/1.png)\n",
                      plain_text="Hi\nBlåbær bold\n")

def test_save_docx_reads_back(tmp_path):
    out = save_document(CONTENT, tmp_path / "a.docx", "docx")
    assert read(out).html.startswith("<h1>Hi</h1>")

def test_save_html_is_self_contained(tmp_path):
    text = save_document(CONTENT, tmp_path / "a.html", "html").read_text("utf-8")
    assert "data:image/png;base64," in text and "<h1>Hi</h1>" in text

def test_save_txt_md_unicode(tmp_path):
    assert save_document(CONTENT, tmp_path / "a.txt", "txt").read_text("utf-8") == "Hi\nBlåbær bold\n"
    md = save_document(CONTENT, tmp_path / "a.md", "md").read_text("utf-8")
    assert "![](a_files/1.png)" in md and (tmp_path / "a_files" / "1.png").exists()

def test_save_pdf_without_tools(tmp_path, monkeypatch):
    # force the built-in route even when LibreOffice is installed on this machine
    monkeypatch.setattr(save_mod, "_libreoffice_pdf", lambda: False)
    out = save_document(CONTENT, tmp_path / "a.pdf", "pdf")
    with pymupdf.open(out) as d:
        assert "Blåbær" in d[0].get_text()

def test_save_replaces_existing_file(tmp_path): ...    # dest exists → replaced, no "(1)" copy, no leftover *.saving
def test_save_locked_file_message(tmp_path, monkeypatch):
    monkeypatch.setattr(save_mod.os, "replace", raise_(PermissionError()))
    (tmp_path / "a.docx").write_bytes(b"old")
    with pytest.raises(ConversionError, match="may be open in another program"):
        save_document(CONTENT, tmp_path / "a.docx", "docx")
    assert (tmp_path / "a.docx").read_bytes() == b"old"

def test_save_formats_order():
    ids = [e for e, _ in save_formats()]
    assert ids[:8] == ["docx", "odt", "rtf", "doc", "pdf", "md", "txt", "html"] and "epub" in ids
@pytest.mark.skipif(no pandoc and no soffice, ...)
def test_save_odt(tmp_path): ...                       # opens again with open_document, heading kept
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement `save_document`.** Everything is written inside a `umc-docedit-` temp folder first, then copied to `dest.with_name(dest.name + ".saving")` and moved over `dest` with `os.replace` (same folder, so it works across drives); the `.saving` file is removed on failure. `PermissionError` / `OSError` while writing `dest` → `ConversionError(f"Couldn't save “{dest.name}” — it may be open in another program.")`.
  - `docx` → `write`. `html` → `htmlpdf._page(dest.stem, html)` with each `img/N.ext` replaced by a `data:` URI. `txt` → `content.plain_text` (UTF-8, `\n` line ends). `md` → `content.markdown` with `img/N.ext` rewritten to `{stem}_files/N.ext` and the pictures written into that folder.
  - `pdf` → if `_libreoffice_pdf()` (true when `plan("docx", "pdf")` returns a route whose first edge engine is `SOFFICE`): DOCX then `convert_file(…, "pdf")`; else pictures written to the temp folder and `pdf.layout_html(html, css, [tmp], out, A4 (595, 842), margin 56.7 pt, dest.stem)` with the same CSS `htmlpdf.html_to_pdf` uses.
  - Any other format → DOCX in the temp folder, then `convert_file(docx, fmt, out, cancel_event)`.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/doceditor/save.py tests/test_doceditor_save.py
git commit -m "Save edited documents to every document format"
```

---

### Task 6: The text view and QTextDocument export

**Files:**
- Create: `src/umc/gui/doceditor/__init__.py`, `src/umc/gui/doceditor/text_view.py`, `src/umc/gui/doceditor/html_export.py`
- Test: `tests/test_gui_doceditor.py`

**Interfaces:**
- Consumes: `OpenedDocument`, `SaveContent`, `next_image_name`, `normalise_html` (Task 1).
- Produces:
  - `class DocTextView(QTextEdit)`: `load(opened: OpenedDocument) -> None` (resets undo and the modified flag), `images: DocImages`, `insert_image(path: Path) -> None`, `content() -> SaveContent`, constants `PAGE_WIDTH = 794` (px, A4 at 96 dpi), `PAGE_MARGIN = 76`.
  - Custom block property `QUOTE_PROPERTY = QTextFormat.UserProperty + 1` (bool) marks quote paragraphs.
  - `html_export.document_to_html(doc: QTextDocument) -> str` (the subset, normalised).
  - `formatting` display rules shared with Task 7: `HEADING_PT = {1: 20, 2: 16, 3: 13}`, code font `"Consolas"` 10 pt.

- [ ] **Step 1: Write the failing tests** (pytest-qt, offscreen):

```python
SUBSET_CASES = CASES + [  # import CASES from test_doceditor_docx
    "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul>",
    '<p>x<img src="img/1.png" width="20" height="20"></p>',
]


@pytest.mark.parametrize("html", SUBSET_CASES)
def test_load_then_export_is_identity(qtbot, html):
    v = DocTextView()
    qtbot.addWidget(v)
    v.load(OpenedDocument(html, {"img/1.png": PNG_1PX}, "docx"))
    assert v.content().html == normalise_html(html)[0]


def test_export_nested_list_in_table(
    qtbot,
): ...  # <td><ul><li>a<ul><li>b</li></ul></li></ul></td> survives
def test_load_resets_undo_and_modified(
    qtbot,
): ...  # after load: not isModified(), not isUndoAvailable()
def test_insert_image_adds_resource(
    qtbot, tmp_path
): ...  # images gets "img/1.png", content().html has <img
def test_content_has_markdown_and_text(
    qtbot,
): ...  # content().plain_text == "T\nx", markdown startswith "# T"
def test_page_column_width(
    qtbot,
): ...  # v.resize(1400, 800): viewport width == PAGE_WIDTH (+/- scrollbar)
```

- [ ] **Step 2: Run** `.venv\Scripts\pytest tests/test_gui_doceditor.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `load`: add each picture as `QTextDocument.ImageResource` under `QUrl(name)` (`QImage.fromData`), then, before `setHtml`, replace every `<blockquote><p …>` with `<p … style="margin-left:40px;margin-right:40px">`; after `setHtml`, fix up the blocks: a block with exactly those margins becomes a quote block (`QUOTE_PROPERTY` true, margins kept for display), any other block's `leftMargin` (from `margin-left`) becomes `indent = round(px / 48)` with `leftMargin 0`, headings get `HEADING_PT` size + bold as their char format, `pre` blocks get the code font. Then `document().clearUndoRedoStacks()` and `setModified(False)`.
  - Page column: white viewport (`objectName "DocPage"`, a `style.qss` rule `#DocPage { background: white; color: black; }`), `document().setDocumentMargin(PAGE_MARGIN)`, and on `resizeEvent` `setViewportMargins(side, 16, side, 16)` with `side = max(16, (width - PAGE_WIDTH) // 2)`.
  - Page breaks: in `paintEvent`, after `super()`, draw a dashed `PALETTE muted` line with the label "Page break" above each block whose `pageBreakPolicy()` has `PageBreak_AlwaysBefore`.
  - `html_export.document_to_html`: iterate `rootFrame()` (`QTextFrame.iterator`) — frames that are `QTextTable` become `table` (cells via `cellAt(r, c)`, emitting each spanning cell once with `rowSpan` / `columnSpan`, and recursing into the cell's blocks); blocks become `h1`–`h3` (`headingLevel`), `blockquote` (`QUOTE_PROPERTY`), `pre` (`nonBreakableLines()`), `hr` (`BlockTrailingHorizontalRulerWidth` property) or `p`; consecutive blocks in a `QTextList` become nested `ul` / `ol` by `QTextListFormat.indent()` and style (`ListDisc`/`ListCircle`/`ListSquare` → `ul`). Fragments: only properties the char format **has** (`hasProperty`) are emitted — `FontWeight ≥ 600` → `b`, italic, underline, strike-out, `FontFamilies`, `FontPointSize`, `ForegroundBrush`, `BackgroundBrush`, anchors → `a href`; inside headings, size and weight are skipped, inside code blocks the font family. Image fragments (`QTextImageFormat`) → `img` with `name`, `width`, `height` when set. Block alignment / indent / page break → the paragraph styles. Run the result through `normalise_html`.
  - `content()` → `SaveContent(document_to_html(doc), {referenced pictures only}, doc.toMarkdown(), doc.toPlainText() + "\n" if non-empty)`.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/gui/doceditor/__init__.py src/umc/gui/doceditor/text_view.py src/umc/gui/doceditor/html_export.py src/umc/gui/style.qss tests/test_gui_doceditor.py
git commit -m "Add the document editor's text view"
```

---

### Task 7: Formatting operations and the formatting bar

**Files:**
- Create: `src/umc/gui/doceditor/formatting.py`, `src/umc/gui/doceditor/format_bar.py`
- Test: `tests/test_gui_doceditor.py`

**Interfaces:**
- Consumes: `DocTextView`, `QUOTE_PROPERTY`, `HEADING_PT` (Task 6).
- Produces (`formatting.py`, every function takes the view's `QTextCursor` first and wraps its change in one `beginEditBlock` / `endEditBlock` = one undo step):
  `set_block_style(c, style: str)` with `style` ∈ `"normal", "h1", "h2", "h3", "quote", "code"`; `block_style(c) -> str`;
  `toggle(c, prop: str)` with `prop` ∈ `"bold", "italic", "underline", "strike"`; `is_on(c, prop) -> bool`;
  `set_font_family(c, family: str)`, `set_font_size(c, pt: float)`, `set_color(c, color: QColor)`, `set_highlight(c, color: QColor | None)`;
  `set_alignment(c, align: str)` ∈ `"left", "center", "right", "justify"`;
  `toggle_list(c, kind: str)` ∈ `"bullet", "number"`; `change_indent(c, delta: int)` (list level inside lists, block indent 0–8 elsewhere);
  `insert_link(c, url: str, text: str | None)`, `insert_table(c, rows: int, cols: int)`, `table_op(c, op: str)` with `op` ∈ `"row_above", "row_below", "col_left", "col_right", "del_row", "del_col", "del_table", "merge", "split"`;
  `insert_page_break(c)`, `insert_hr(c)`, `clear_formatting(c)` (character formatting and links removed, block becomes Normal; lists and tables kept).
- Produces (`format_bar.py`): `class FormatBar(QWidget)` with `__init__(view: DocTextView)`, `refresh() -> None` (button states from the cursor), attributes `style_box`, `font_box`, `size_box`, `buttons: dict[str, QToolButton]` keyed by `"bold", "italic", "underline", "strike", "left", "center", "right", "justify", "bullet", "number"`, and `table_menu: QMenu` (enabled only inside a table).

- [ ] **Step 1: Write the failing tests**

```python
def test_bold_and_heading_export(
    view,
):  # view fixture: DocTextView loaded with "<p>hello world</p>"
    c = view.textCursor()
    c.select(QTextCursor.WordUnderCursor)
    view.setTextCursor(c)
    toggle(c, "bold")
    set_block_style(c, "h1")
    assert view.content().html == "<h1>hello world</h1>"  # bold inside headings isn't emitted
    set_block_style(c, "normal")
    assert view.content().html == "<p><b>hello</b> world</p>"


def test_single_undo_step(
    view,
): ...  # set_block_style then view.undo() → back to "<p>hello world</p>"
def test_lists_and_indent(view): ...  # toggle_list "bullet", change_indent +1 → nested ul in export
def test_table_ops(view):
    insert_table(view.textCursor(), 2, 2)
    table_op(view.textCursor(), "row_below")
    assert view.content().html.count("<tr>") == 3


def test_clear_formatting_keeps_list(view): ...
def test_format_bar_follows_cursor(qtbot, view):
    bar = FormatBar(view)
    qtbot.addWidget(bar)
    # put cursor inside bold text → bar.buttons["bold"].isChecked(); move to plain text → not checked


def test_table_menu_only_in_table(qtbot, view): ...
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement `formatting.py`, then `FormatBar`** (widgets laid out with the existing `umc.gui.widgets.flow_layout.FlowLayout`; style names shown as "Normal", "Heading 1", "Heading 2", "Heading 3", "Quote", "Code"; font box = `QFontComboBox`; size box = editable `QComboBox` 8–72; colour buttons open `QColorDialog`; link button asks with `QInputDialog` for the address; picture button calls `view.insert_image` after a `QFileDialog` for PNG / JPG / GIF / BMP / WEBP; table button asks rows × columns in a small dialog, default 3 × 3). `refresh` is connected to `view.cursorPositionChanged` and `currentCharFormatChanged`. The shortcuts from the spec are installed by the page in Task 9 and call these functions.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/gui/doceditor/formatting.py src/umc/gui/doceditor/format_bar.py tests/test_gui_doceditor.py
git commit -m "Add document formatting and the formatting bar"
```

---

### Task 8: Find & replace

**Files:**
- Create: `src/umc/gui/doceditor/find_bar.py`
- Test: `tests/test_gui_doceditor.py`

**Interfaces:**
- Consumes: `DocTextView` (Task 6).
- Produces: `class FindBar(QWidget)` with `__init__(view: DocTextView)`, `open(replace: bool) -> None`, `find(text: str, backward: bool = False, case: bool = False, whole: bool = False) -> bool` (wraps around), `replace(text: str, new: str, case=False, whole=False) -> bool`, `replace_all(text: str, new: str, case=False, whole=False) -> int` (one undo step), and a `status: QLabel` showing `"No matches."` / `f"Replaced {n}."`.

- [ ] **Step 1: Write the failing tests**

```python
def test_find_wraps(qtbot, view_text):  # loaded "<p>a cat. A cat.</p>"
    bar = FindBar(view_text)
    assert bar.find("cat") and bar.find("cat") and bar.find("cat")  # third wraps to the first
    assert not bar.find("dog") and bar.status.text() == "No matches."


def test_replace_all_case_and_whole_word(qtbot, view_text):
    bar = FindBar(view_text)
    assert bar.replace_all("a", "the", case=True, whole=True) == 1
    assert view_text.toPlainText() == "the cat. A cat."
    view_text.undo()
    assert view_text.toPlainText() == "a cat. A cat."
```

- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** with `QTextDocument.find` and `FindCaseSensitively` / `FindWholeWords` / `FindBackward` flags; the bar is hidden by default, Esc hides it and returns focus to the view; Enter = find next, Shift+Enter = find previous.
- [ ] **Step 4: Run the tests again** — Expected: PASS.
- [ ] **Step 5: Commit**
```bash
git add src/umc/gui/doceditor/find_bar.py tests/test_gui_doceditor.py
git commit -m "Add find and replace to the document editor"
```

---

### Task 9: The document editor page (open, banner, save, leave)

**Files:**
- Create: `src/umc/gui/doceditor/doc_editor_page.py`
- Test: `tests/test_gui_doceditor.py`

**Interfaces:**
- Consumes: `open_document` (Task 4), `save_document`, `save_formats` (Task 5), `DocTextView` (6), `FormatBar`, `formatting` (7), `FindBar` (8), `warning_text`, `EDITABLE_DOCUMENT_EXTS` (1), `umc.gui.tool_dialog.ToolMissingDialog`.
- Produces: `class DocEditorPage(QWidget)` with signals `back_requested`, `opened`, `save_done`; methods `open_path(path: Path) -> None` (asynchronous; emits `opened` on success), `drop_paths(paths: list[Path]) -> None`, `save() -> bool`, `save_as() -> bool`, `maybe_leave() -> bool`; attributes `view`, `format_bar`, `find_bar`, `banner` (`QFrame` with a label and a close button), `message: QLabel`, `status: QLabel`, `source: Path | None`, `source_format: str | None`, `saving: bool`, `warnings: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_docx_shows_text(qtbot, page, tmp_path):  # page fixture: DocEditorPage added to qtbot
    p = write("<h1>Hi</h1><p>there</p>", {}, tmp_path / "a.docx")
    with qtbot.waitSignal(page.opened, timeout=10000):
        page.open_path(p)
    assert page.view.toPlainText() == "Hi\nthere" and not page.banner.isVisible()
    assert page.status.text() == "2 words · 7 characters"

def test_banner_for_header(qtbot, page, docx_with_header): ...   # banner visible with warning_text([W_HEADERS])
def test_save_writes_back_same_format(qtbot, page, tmp_path, monkeypatch): ...
    # open a.md, type text, page.save() (wait for save_done) → a.md contains it, not modified, no dialog
def test_save_with_warnings_asks(qtbot, page, docx_with_header, monkeypatch): ...
    # QMessageBox answer patched to Cancel → file unchanged, still modified
def test_save_as_pdf(qtbot, page, tmp_path, monkeypatch): ...   # QFileDialog patched → .pdf exists and has the text
def test_failed_save_stays_unsaved(qtbot, page, tmp_path, monkeypatch):
    # save_document patched to raise ConversionError("Couldn't save …") → message shows it, isModified() still True
def test_edits_during_save_stay_unsaved(qtbot, page, tmp_path, monkeypatch): ...
def test_back_with_changes_asks(qtbot, page, monkeypatch): ...  # Cancel → maybe_leave() False; Discard → True
def test_drop_second_kind_of_file(qtbot, page, media): ...       # drop_paths([png]) → message "Only one document can be edited at a time."
def test_open_tool_missing_shows_dialog(qtbot, page, tmp_path, monkeypatch): ...
    # open_document patched to raise ToolMissingError → ToolMissingDialog.exec patched, called once with the tool name
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Layout: top bar (Back to Convert · Undo · Redo · Save · Save as…, same object names / `Small` buttons as `EditorPage._build_top_bar`), `FormatBar`, `FindBar`, banner, `DocTextView`, bottom row with `message` (left) and `status` (right: `f"{words} words · {chars} characters"`, plus `" · Unsaved changes"` while modified).
  - Opening runs `open_document` on a `QThread` worker (`opened_doc(OpenedDocument)`, `failed(str)`, `tool_missing(tool, url, message)`), the view disabled with `message` "Opening …". On `tool_missing`, after the thread finished, show `ToolMissingDialog(tool, url, message, parent=self)`; if `exec()` is accepted, open again. Failures show in `message` (error style).
  - Banner: visible when `warnings` is non-empty, text = `warning_text(warnings)`.
  - Save: `source_format` + `source`; if `warnings` and this is the first Save over the opened file, ask `QMessageBox` "Parts of this document will be lost ({', '.join(warnings)}). Save anyway?" with Save / Save as… / Cancel. Save as: `QFileDialog.getSaveFileName` with filters from `save_formats()` (default = `source`), unknown suffix → the chosen filter's ext appended; afterwards Save repeats that path and format. Save as never asks the warning question.
  - Saving runs `save_document(view.content(), path, fmt)` on a `QThread`; record `view.document().revision()` at start and only call `setModified(False)` on success if the revision is unchanged. `ToolMissingError` → `ToolMissingDialog`, retry on accept. References kept until `QThread.finished`.
  - `maybe_leave` / `_ask_save_changes` like `EditorPage`, text "Save changes to this document?".
  - `drop_paths`: exactly one path whose detected ext is in `EDITABLE_DOCUMENT_EXTS` → `maybe_leave()` then `open_path`; otherwise `message` "Only one document can be edited at a time."
  - Shortcuts (spec list, minus zoom): Ctrl+B/I/U, Ctrl+Shift+X, Ctrl+Alt+1/2/3/0, Ctrl+L/E/R/J, Ctrl+Shift+L, Ctrl+K, Ctrl+Enter, Ctrl+Space, Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z, Ctrl+F / Ctrl+H, Ctrl+S / Ctrl+Shift+S.

- [ ] **Step 4: Run the tests again** — Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/umc/gui/doceditor/doc_editor_page.py tests/test_gui_doceditor.py
git commit -m "Add the document editor page"
```

---

### Task 10: Convert | Edit for documents in the main window

**Files:**
- Modify: `src/umc/gui/main_window.py` (`edit_available`, `enter_edit`, `leave_edit`, `handle_drop`, `closeEvent`, the mode bar tooltip)
- Test: `tests/test_gui_doceditor.py`, keep `tests/test_gui_editor.py` and `tests/test_gui.py` green

**Interfaces:**
- Consumes: `DocEditorPage` (Task 9), `EDITABLE_DOCUMENT_EXTS`.
- Produces: `MainWindow.edit_kind() -> str | None` (`"image"` when every file is an image — unchanged rule —, `"document"` for exactly one file whose ext is editable, else `None`); `edit_available()` returns `edit_kind() is not None`; attribute `doc_editor: DocEditorPage | None`; helper `_current_editor() -> QWidget | None` replacing the `stack.currentIndex() == 1` checks.

- [ ] **Step 1: Write the failing tests**

```python
def test_switch_for_one_document(qtbot, window, media):
    window.add_files([str(media / "doc.md")])
    assert window.mode_bar.isVisible()
    window.add_files([str(media / "notes.txt")])
    assert not window.mode_bar.isVisible()  # two documents


def test_no_switch_for_pdf(qtbot, window, media): ...
def test_edit_opens_document_editor(qtbot, window, media):
    window.add_files([str(media / "doc.md")])
    window.enter_edit()
    qtbot.waitSignal(window.doc_editor.opened, timeout=10000)
    assert (
        window.stack.currentWidget() is window.doc_editor
        and "Title" in window.doc_editor.view.toPlainText()
    )


def test_back_returns_to_convert(qtbot, window, media): ...
def test_images_still_open_image_editor(qtbot, window, media): ...
```
(`window` = the fixture `tests/test_gui.py` uses; reuse it via `conftest` import or copy its three lines.)

- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement**: branch `enter_edit` on `edit_kind()`; the doc editor page is created on first use, connected to `leave_edit`, `open_path` called, page shown and window enlarged to ≥ 1100 × 720 at once (errors appear on the page); the Edit tooltip is "Open the document in the document editor" for documents and the existing text for images; `handle_drop` and `closeEvent` use `_current_editor()` and call `drop_paths` / `maybe_leave` on the document page.
- [ ] **Step 4: Run** `.venv\Scripts\pytest tests/test_gui.py tests/test_gui_editor.py tests/test_gui_doceditor.py -v` — Expected: PASS.
- [ ] **Step 5: Commit**
```bash
git add src/umc/gui/main_window.py tests/test_gui_doceditor.py
git commit -m "Offer Edit for a single text document"
```

---

### Task 11: Self-test, docs and full verification

**Files:**
- Modify: `src/umc/selftest.py` (add `_doc_editor_check` and its entry in `steps`), `README.md` (row after the image editor rows), `CLAUDE.md` (architecture tree + roadmap 10.3), `docs/superpowers/specs/2026-11-10-document-editor-design.md` (status line → "implemented (desktop)")

- [ ] **Step 1: Add `_doc_editor_check(d: Path) -> list[Path]`**: write `"<h1>Self-test</h1><table><tr><td><p>a</p></td></tr></table><p><img src=\"img/1.png\" width=\"8\"></p>"` with a Pillow PNG through `save_document(..., "docx")` and `"pdf"`, read the DOCX back with `open_document`, raise `RuntimeError` unless the heading, the table and one picture survive and the PDF text contains "Self-test"; register it in `steps` as `("document editor", …)` next to the image editor step.
- [ ] **Step 2: Run** `.venv\Scripts\python -m umc --self-test selftest-report.txt` — Expected: exit 0 and the report lists "document editor: OK".
- [ ] **Step 3: Docs.** README row: `| **Document editor**: Convert \| Edit for one DOCX, ODT, RTF, DOC, Markdown, TXT or HTML file · headings, fonts, colours, highlight, lists, tables, pictures, links, page breaks · find & replace · undo · save back or as PDF and every document format | ✅ | 🔜 |`. CLAUDE.md: add `doceditor/` (Qt-free) and `gui/doceditor/` to the architecture tree, and roadmap line `3. ✅ Document editor, first version (desktop) — spec docs/superpowers/specs/2026-11-10-document-editor-design.md: … Not yet: website, PDF editing, headers / footers, comments, tracked changes.`
- [ ] **Step 4: Full verification** — `ruff check . && ruff format --check .`, `.venv\Scripts\pytest` (everything green, tool-dependent tests may skip), and `.venv\Scripts\python scripts/convert_matrix.py --out matrix-out` (no new failures vs. `main`). After landing (Step 5), trigger "Build Windows executable" (Actions → Run workflow on `main`) and confirm its `--self-test` step passes.
- [ ] **Step 5: Commit and land**
```bash
git add src/umc/selftest.py README.md CLAUDE.md docs/superpowers/specs/2026-11-10-document-editor-design.md
git commit -m "Add document editor self-test step and document the editor"
git rebase main
git -C G:\Claude\Universal-Media-Converter merge --ff-only doc-editor
git -C G:\Claude\Universal-Media-Converter push origin main
```
