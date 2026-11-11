# Document editor (desktop, then website) — design

Date: 2026-11-10 · Roadmap item 10 (text / document editing) · Status: implemented (desktop)

## Goal

When a text document is added to the app, the user can choose **Convert | Edit**. Convert is
the existing converter, unchanged. Edit opens a Word-like rich-text editor in the same window.
Desktop app first (phase 1); the website follows with the same feature list (phase 2).

## Decisions (agreed with the owner)

| Question | Decision |
|---|---|
| Which documents | Text documents: DOCX, ODT, RTF, DOC, MD, TXT, HTML (`htm` too). PDF editing (add text, highlight, sign) is a later, separate step. Spreadsheets, presentations, EPUB and the other document formats are not edited. |
| How faithful for Word files | "Normal formatting" survives open → save: headings, bold / italic / underline / strikethrough, fonts, sizes, colours, highlight, lists, tables, pictures, links, alignment, page breaks. Fancy layout (headers / footers, text boxes, columns, tracked changes, comments, footnotes) may be lost — the editor warns on open. |
| Saving | **Save** writes back to the format the file came from. **Save as…** offers every format the converter can make from a document, including PDF. |
| Desktop architecture | Qt's own rich-text editor (`QTextEdit` / `QTextDocument`, built-in undo) for editing; a Qt-free DOCX reader / writer on `python-docx` (MIT) that exchanges a small, defined HTML subset with the editor. |
| Website architecture | ProseMirror (MIT) editor loaded only when Edit is clicked; a hand-written DOCX reader / writer on fflate (like the XLSX one); Pandoc-wasm for ODT / RTF; MuPDF for PDF. |

Rejected: a self-written document model (months of work before it beats Qt's), and embedding the
web editor in the desktop app via QtWebEngine (~200 MB extra, not in PySide6-Essentials).
mammoth (used by the converter) is not used for editing: it drops fonts, colours and alignment.

## User flow

1. The user adds files as today. The **Convert | Edit** switch appears when every file is an
   image (image editor, unchanged) **or** when exactly one file is added and its extension is
   in `EDITABLE_DOCUMENT_EXTS = {"docx", "odt", "rtf", "doc", "md", "txt", "html", "htm"}`.
   Convert is selected by default.
2. Clicking **Edit** switches the window's `QStackedWidget` to the document editor page (a
   separate page from the image editor, created on first use) and enlarges the window to at
   least 1100 × 720, as the image editor does.
3. Dropping another file while the document editor is open: an editable document replaces the
   current one (asking "Save changes?" first when there are unsaved edits); anything else shows
   a plain message ("Only one document can be edited at a time.").
4. **Back to Convert** returns to the converter; with unsaved changes it asks "Save changes?"
   (Save / Discard / Cancel).

## Editor layout

- **Top bar:** Back to Convert · Undo · Redo · Save · Save as…
- **Formatting bar** (one row, wraps with the existing `FlowLayout` on narrow windows):
  paragraph style (Normal, Heading 1, Heading 2, Heading 3, Quote, Code) · font family · font
  size · Bold · Italic · Underline · Strikethrough · text colour · highlight colour · align
  left / centre / right / justify · bullet list · numbered list · decrease / increase indent ·
  link · picture · table · page break · horizontal line · clear formatting. Buttons show the
  state at the cursor (e.g. Bold is checked inside bold text).
- **Table menu** (when the cursor is in a table): insert row above / below, insert column
  left / right, delete row / column / table, merge / split cells.
- **Centre:** the document as a white page-width column (A4 width) on the window
  background, scrolling continuously. Page breaks are drawn as a thin dashed line labelled
  "Page break"; real pages only exist in PDF output.
- **Find & replace bar** (Ctrl+F / Ctrl+H), slides in above the page: find next / previous,
  match case, whole word, replace, replace all.
- **Status bar:** word and character count; "Unsaved changes" marker.
- Theme: existing `style.qss` tokens; the page itself stays white with black text in dark mode
  (like Word), so colours in the document look as they will when printed.

### Shortcuts

Ctrl+B / I / U · Ctrl+Shift+X strikethrough · Ctrl+Alt+1 / 2 / 3 headings · Ctrl+Alt+0 Normal ·
Ctrl+L / E / R / J alignment · Ctrl+Shift+L bullet list · Ctrl+K link · Ctrl+Enter page break ·
Ctrl+Space clear formatting · Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) · Ctrl+F / Ctrl+H ·
Ctrl+S / Ctrl+Shift+S.

No zoom in this version: `QTextEdit` can't scale text that has an explicit point size.

## The exchange format (editor HTML)

The Qt-free code and the editor talk through one HTML subset, documented in
`src/umc/doceditor/html_subset.py` and used by every reader and writer:

- Blocks: `p`, `h1`–`h3`, `blockquote`, `pre`, `ul` / `ol` with `li` (nesting = list level),
  `table` / `tr` / `td` (`colspan` / `rowspan`), `hr`, and `<p style="page-break-before:always">`.
  `<table style="border:none">` is a table without borders (plain `<table>` has borders).
  Empty list items (`<li></li>`) are kept; text after a nested list inside an item becomes the
  next item at the same level.
- Text: runs of spaces and line breaks collapse to one space, but tabs (`\t`) and no-break
  spaces (U+00A0) are kept as they are, so indentation and aligned columns survive. Readers
  write a space that follows a space (or starts a line) as U+00A0; writers turn it back into
  an ordinary space (DOCX: `xml:space="preserve"`; TXT through `toPlainText()`). `pre` keeps
  everything. The editor's export applies the same rule to typed and pasted text (a space at
  the start of a block or after another space is written as U+00A0), so spaces typed in the
  editor survive every format. An empty first paragraph of a table cell loads as the cell's one
  empty block (Qt would otherwise add a block per save).
- Inline: `b` / `strong`, `i` / `em`, `u`, `s`, `a href`, `img src width height`, `br`, and
  `span style` with `font-family`, `font-size` (pt), `color`, `background-color`. CSS
  emphasis on spans (`font-weight` bold / ≥ 600, `font-style` italic / oblique,
  `text-decoration(-line)` underline / line-through) becomes `b` / `i` / `u` / `s`.
- Paragraph style: `text-align`, `margin-left` (indent, in multiples of 36 pt).
- Pictures are referenced as `src="img/N.ext"` with the bytes kept beside the HTML in a
  `DocImages` map; the GUI loads them as `QTextDocument` resources.

The GUI never parses Qt's own `toHtml()` (inline styles, `-qt-` properties, flat lists with
`-qt-list-indent`): `gui/doceditor/html_export.py` walks the `QTextDocument` (frames, tables,
blocks, lists, fragments) and writes the subset directly, so the writers only ever see it.

## Opening (`src/umc/doceditor/io.py`, no Qt)

`open_document(path) -> OpenedDocument(html, images, source_format, warnings)`:

| Source | How |
|---|---|
| DOCX | `docx_reader.read(path)` — python-docx: paragraphs (style → heading / quote / code, alignment, indent, page break before), runs (bold, italic, underline, strike, font, size, colour, highlight — direct formatting, else the run's character style and its `basedOn` chain; never paragraph styles or document defaults, which a saved-over file keeps itself), tabs (`\t`), tables without visible borders → `border:none` (a table counts as bordered when any cell has a visible `w:tcBorders` edge, as LibreOffice writes them, else by its own `tblBorders`, else by its table style), hyperlinks, numbering (`numPr` → `ul` / `ol` by the numbering format, level), tables (merged cells), inline pictures. |
| MD | Python-Markdown → HTML (already a dependency), normalised to the subset. A front-matter block (a first line `---`, a second line `key:`, up to a later `---` / `...` line; otherwise a leading `---` is a horizontal rule) is kept verbatim in `OpenedDocument.front_matter`, not rendered. |
| TXT | Read with the existing `htmlpdf.read_text` (UTF-8, Windows-1252 fallback); one `p` per line, leading / repeated spaces as U+00A0, tabs kept. |
| HTML | Parsed with lxml and normalised to the subset; `data:` and local relative images are loaded, remote ones are left out (no network access when opening a file; network-share, drive and absolute paths are refused before the disk is touched). The `<title>` is kept in `OpenedDocument.title`. |
| ODT, RTF, DOC | Converted to DOCX first with `convert_document` (the existing route planner: Pandoc or LibreOffice). A missing tool raises `ToolMissingError`, so the usual "Install it for me" dialog appears and the open is retried after install. Runs on a worker thread with a busy indicator. |

**Warnings on open** (DOCX, and formats converted through DOCX): the reader notes headers /
footers, text boxes / shapes, multiple columns, tracked changes, comments, footnotes /
endnotes, fields other than page numbers, and embedded objects. If any are found, a banner at
the top of the page says, in plain words, "This document has parts the editor can't keep:
headers and footers, comments. They will be lost if you save over the original — use Save as
to keep a copy." The banner can be closed.

Further warnings: Word lists numbered with letters or Roman numerals ("numbering with letters
or Roman numerals" — they open as ordinary numbered lists); HTML and Markdown that lose page
styles, classes or scripts, pictures from the web, or embedded HTML (iframes, forms, video …);
Markdown footnotes and block-level raw HTML ("footnotes and other Markdown extras"); Markdown
inline code spans, level 4–6 headings and picture alt texts outside fenced code ("inline code,
small headings and picture descriptions" — they open as plain text, level-3 headings and
pictures without a description). They use the same banner and the same "Save anyway?" question.

Limits: files over 50 MB or documents over 2 000 000 characters are refused with
"This document is too large to edit here."

## Saving (`src/umc/doceditor/save.py`, no Qt — except the Qt-native writers below)

- **Save** — writes back to the source path and format. For ODT / RTF / DOC (opened through
  DOCX) it writes DOCX to a temporary folder and converts it back with `convert_document`; if
  the needed tool is missing, the install dialog appears. When the file had warnings (above),
  the first Save asks "Parts of this document will be lost (…). Save anyway?" with Save /
  Save as… / Cancel.
- **Save as…** — file dialog listing DOCX, ODT, RTF, DOC, PDF, MD, TXT, HTML, EPUB and every
  other target the document converter can make from DOCX. The system dialog's replace prompt
  is the only way to overwrite; the editor never overwrites silently. After Save as, Save
  repeats that path and format.

| Target | Writer |
|---|---|
| DOCX | `docx_writer.write(html, images, dest, template)` — python-docx, the exact mirror of the reader (headings → built-in Heading styles, lists → a numbering definition per list type, tables with explicit borders or none, pictures, links, run formatting). When the document came from a DOCX, `SaveContent.template` is that file: its body is replaced and its styles, document defaults, page size / orientation / margins, headers / footers, theme and numbering definitions are kept (styles the writer needs and the template lacks are copied in). Without a template (new, ODT / RTF / DOC sources) python-docx's default template is used. |
| HTML | The subset HTML in a small standalone page (`htmlpdf._page`, titled with the original `<title>` or else the file name), pictures embedded as `data:` URIs so the file is self-contained. |
| MD | Our own Qt-free writer, `md_writer.to_markdown(html)`, which writes exactly what `open_document` reads back to the same subset (headings, `**` / `*`, raw `<u>` / `<s>` / `<span style>` / `<br>`, lists indented 4 spaces per level, `>` quotes, fenced code, `---`, pipe tables, escaped text, leading no-break spaces as `&nbsp;`, every tab as `&#9;`; pictures with a width or height as raw `<img src width height>`, their `src` pointing into the `name_files/` folder like `![](…)` links). Front matter is written back first, unchanged. Pictures are written next to the `.md` file in a `name_files/` folder and linked. Alignment, indents and page breaks have no Markdown form. |
| TXT | `QTextDocument.toPlainText()` (GUI side), UTF-8. |
| PDF | HTML → PDF through the existing built-in `pdf.layout_html` route (PyMuPDF Story), so PDF works with nothing installed; LibreOffice from DOCX is used instead when it is installed (better layout), exactly as the converter's route planner already decides. |
| ODT, RTF, DOC, EPUB, … | DOCX to a temporary folder, then `convert_document` to the target. |

Temporary folders: `tempfile.TemporaryDirectory(prefix="umc-docedit-")`. Saving runs on a
`QThread` (like the image editor) and the document stays editable; edits made during a save
keep the "unsaved" marker. A failed save leaves the document untouched and unsaved, with a
plain-language message; details go to the log.

## Code layout

```
src/umc/doceditor/          # Qt-free
  html_subset.py            # the allowed tags / styles, normalise_html(), normalise_html_report()
  md_writer.py              # subset HTML → Markdown (read back by open_document unchanged)
  docx_reader.py            # DOCX → subset HTML + images + warnings (python-docx)
  docx_writer.py            # subset HTML + images → DOCX (python-docx)
  io.py                     # open_document(): per-format readers, convert-through-DOCX
  save.py                   # save_document(): per-format writers, convert-from-DOCX
src/umc/gui/doceditor/      # Qt
  doc_editor_page.py        # page layout, top bar, open / save wiring, banner, status bar
  text_view.py              # QTextEdit subclass: page column, page-break lines, images
  html_export.py            # QTextDocument → subset HTML (+ Markdown / plain text)
  format_bar.py             # formatting bar + table menu, state follows the cursor
  find_bar.py               # find & replace
src/umc/gui/main_window.py  # Convert | Edit also for one editable document
```

New dependency: `python-docx>=1.1` (MIT; uses lxml, already installed). `packaging/umc.spec`
must include its default template (`docx/templates/*`) as data.

## Testing

- `tests/test_doceditor_docx.py` (pure Python): for every feature in the subset, write → read
  round-trips (headings, each run property, lists with levels, tables with merged cells,
  pictures, links, alignment, indent, page breaks); reading a DOCX made by LibreOffice /
  Word-style fixtures generated in `conftest.py`; warnings are reported for headers, comments
  and tracked changes.
- `tests/test_doceditor_io.py`: MD / TXT / HTML open into the subset; load → export through
  the `QTextDocument` gives back the same subset (GUI test); save to each target opens again (PDF
  has the text, DOCX re-reads equal); ODT / RTF round-trip skipped when Pandoc / LibreOffice
  are missing; size limits.
- `tests/test_gui_doceditor.py` (pytest-qt, offscreen): the switch appears for one DOCX / MD /
  TXT and not for two documents or a PDF; Edit opens the text; Bold and Heading 1 buttons
  change the document and follow the cursor; insert table and add a row; find & replace all;
  Save writes the same format; Save as PDF and DOCX produce files that open; Back with unsaved
  changes asks; the warning banner appears for a DOCX with a header.
- `src/umc/selftest.py`: a step that writes a DOCX with a heading, a table and a picture
  through `docx_writer`, reads it back with `docx_reader` and checks the text — catches a
  missing python-docx template in the built `.exe`.
- Lint and the whole existing test suite stay green; run `scripts/convert_matrix.py` once,
  since document routes are reused.

## Phase 2 — website (`web/src/doceditor/`)

Same feature list, layout and shortcuts as the desktop, so the README row can say ✅ in both
columns when done.

- **Shell:** the Convert | Edit switch in `web/src/ui/app.ts`, shown for one editable document
  (same extension list). The whole editor is a dynamic `import()` loaded on the first click
  of Edit, so the page bundle stays ~100 KB.
- **Editor:** ProseMirror (`prosemirror-state`, `-view`, `-model`, `-transform`, `-commands`,
  `-history`, `-keymap`, `-inputrules`, `-tables`, `-schema-list`; all MIT) with a schema that
  mirrors the HTML subset (marks: bold, italic, underline, strike, link, font, size, colour,
  highlight; nodes: paragraph with align / indent, heading 1–3, blockquote, code block, lists,
  table, image, horizontal rule, page break). It runs on the page (it needs the DOM), not in
  the worker. Tiptap was not chosen: it is a wrapper we don't need, and some of its extensions
  are paid.
- **Opening:** DOCX via `web/src/doceditor/docx.ts`, a hand-written reader on fflate +
  `DOMParser` with the same rules as the desktop reader (and the same warnings); MD via
  `prosemirror-markdown` (MIT) / markdown-it; TXT and HTML via `DOMParser` into the schema;
  ODT / RTF via Pandoc-wasm (→ DOCX) in the worker, which downloads Pandoc only then; DOC is
  not offered on the web (needs LibreOffice, as in the converter).
- **Saving:** a "Save" button downloads the file in its original format; "Save as" offers DOCX,
  ODT, RTF (Pandoc-wasm from DOCX), PDF (subset HTML → the existing `htmlToPdf` with MuPDF),
  MD, TXT, HTML. Writing runs in the worker; the DOCX writer mirrors the reader.
- **Shared rules:** both apps' readers and writers are tested against the same DOCX fixtures
  (generated by `e2e/global-setup.ts` via python3 + `docx_writer`, as the RAW / PSD samples
  are), so a file saved by one app opens identically in the other.
- **Tests:** vitest for the DOCX reader / writer and schema ↔ HTML; Playwright e2e: open a
  DOCX, make text bold, add a table row, save DOCX and PDF, re-open the DOCX and check.

## Docs

Same commit as each phase: README "What works where" row for the document editor (desktop ✅,
website "coming later", then ✅ after phase 2), and roadmap item 10 lines in `CLAUDE.md`.

## Later (not in this design)

PDF editing (add text boxes, highlight, draw, sign on top of pages), spreadsheet editing,
comments and tracked changes, headers / footers, real page view with page numbers, spell check.
