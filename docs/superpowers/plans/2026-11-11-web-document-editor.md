# Document Editor (website) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The website gets the same Word-like document editor as the desktop app. You reach it through the Convert | Edit switch with one DOCX / ODT / RTF / MD / TXT / HTML file, and it behaves the same: same formatting, same warnings, the same saved files.

**Architecture:** A port of the desktop's Qt-free `src/umc/doceditor/` to TypeScript in `web/src/doceditor/`.
- It has the same canonical HTML subset, normaliser, DOCX reader/writer (fflate + DOMParser), Markdown writer and warnings.
- It runs **on the page**, because DOMParser/XMLSerializer aren't available in workers.
- The editor is ProseMirror, with a schema that mirrors the subset.
- Only Pandoc (ODT/RTF in and out) and MuPDF (PDF) go through the existing conversion worker, as ordinary converter jobs.
- Desktop and web are kept identical by shared test vectors: a JSON file generated from the desktop code, which both test suites check against.

**Tech Stack:** TypeScript + Vite (no UI framework), ProseMirror (MIT), markdown-it (MIT), fflate (already a dependency), vitest with jsdom (MIT, dev only) for DOM-based logic tests, Playwright e2e.

**Spec:** `docs/superpowers/specs/2026-11-10-document-editor-design.md` (Phase 2, and the desktop sections it mirrors: exchange format, warnings, Opening, Saving).

## Global Constraints

- Work in `.claude/worktrees/web-doc-editor` (branch `feat/web-doc-editor`). Commits are authored by darkbepo: imperative subject, **no Co-Authored-By, no AI/Claude mentions**. `git add` only the task's files. Never push or merge.
- **New dependencies (owner approves them with this plan):**
  - dependencies: `prosemirror-model`, `prosemirror-state`, `prosemirror-view`, `prosemirror-transform`, `prosemirror-commands`, `prosemirror-history`, `prosemirror-keymap`, `prosemirror-schema-list`, `prosemirror-tables`, `prosemirror-search`, `markdown-it`
  - devDependencies: `jsdom`, `@types/markdown-it`, `@types/jsdom`
  - All are MIT. Use current releases with `^` ranges, and record the exact versions installed in the Task 1 report.
- The whole editor is loaded with `import()` on the first click of Edit. **The page bundle must not grow:** `npm run build`'s main entry chunk stays within +2 KB of `main`'s.
- Editable on the web: `WEB_EDITABLE_DOCUMENT_EXTS = ["docx", "odt", "rtf", "md", "txt", "html"]` (`htm` maps to `html` in detect). **DOC is not offered**, because it needs LibreOffice.
- Edit is enabled for every-file-an-image (the image editor, unchanged) or for **exactly one** editable document. The tooltip texts are the desktop's:
  - "Open the document in the document editor"
  - "Open the images in the image editor (each image becomes a layer)"
  - "Add images, or one text document, to edit them"
- Limits, copied verbatim from desktop `types.py`: 50 MB file size, 2 000 000 characters, message `"This document is too large to edit here."`
- Warning constants and `warningText()` wording must equal desktop `types.py` exactly. A vitest test reads `src/umc/doceditor/types.py` and compares them.
- The canonical subset is the desktop's (`html_subset.py` docstring and spec "The exchange format").
  - Includes tabs, NBSP for repeated or leading spaces, and `<table style="border:none">`.
  - The web normaliser must produce **byte-identical** output for every shared vector.
- Never overwrite silently. Saving downloads a file; "Save to a file…" (Chrome/Edge `showSaveFilePicker`) writes to a file the user picked, and Ctrl+S then overwrites that same file, exactly as the web image editor does.
- Plain-language errors on the page; details go to `console.error`.
- Checks before every commit: `npx tsc --noEmit`, `npm test` and `npm run e2e`, all in `web/`. Use the Edge config locally when Playwright's Chromium is missing: `npx playwright test -c playwright.edge.local.config.ts`. Add `ruff` + `pytest` when Python files change.

## Review Focus

1. **A DOCX saved by the website must open identically on the desktop, and the other way round.** Task 11's cross-app e2e step runs the desktop `docx_reader` on a DOCX the website downloaded, and the web reader on a DOCX the desktop wrote (`test_cross_app_docx`).
2. **Saving over the original three times must not change anything** (the desktop's worst bugs were things that got worse on every save): TXT indentation and tabs, Markdown fences and quotes, empty table cells, typed double spaces. Pinned in Task 7 (`load → export` identity over the shared CASES) and Task 11 (`three saves stay identical`).
3. **Big or odd files must not freeze the tab:** a 2-million-character TXT, a DOCX with 300 pictures, deeply nested HTML. The limits are checked before parsing (Task 5 `refuses large files before reading`), and the normaliser is iterative or has a depth cap (Task 2 `deep nesting keeps text`).
4. **Pandoc downloads ~16 MB only when it's needed:** opening or saving DOCX, MD, TXT or HTML must not fetch it. Task 11 asserts that no `pandoc` network request happens in the DOCX round trip (`no pandoc download for docx`).
5. **Leaving with unsaved edits:** the Convert button, closing the tab (`beforeunload`) and dropping another file all ask first. Task 10 `leave asks when dirty`, `beforeunload set while dirty`.

---

## File structure

```
web/src/doceditor/                 # logic — uses DOM APIs (DOMParser/XMLSerializer), no UI
  types.ts                          # OpenedDocument, SaveContent, DocImages, constants, warningText, nextImageName
  subset.ts                         # normaliseHtml / normaliseHtmlReport / textLength / htmlTitle (port of html_subset.py)
  docx-read.ts                      # readDocx(bytes) → OpenedDocument (port of docx_reader.py)
  docx-write.ts                     # writeDocx(html, images, template?) → Uint8Array (port of docx_writer.py)
  markdown.ts                       # mdToHtml (markdown-it) + toMarkdown (port of md_writer.py) + splitFrontMatter
  open.ts                           # openDocument(file, convert) → OpenedDocument (port of io.py)
  save.ts                           # saveDocument(content, fmt, convert) → Blob; SAVE_FORMATS (port of save.py)
web/src/ui/doceditor/
  schema.ts                         # ProseMirror schema mirroring the subset
  convert.ts                        # subset HTML ↔ ProseMirror doc (parse with preserveWhitespace, export + NBSP rule)
  commands.ts                       # formatting / list / table / insert commands (the desktop formatting.py set)
  view.ts                           # DocView: EditorView setup, plugins, page column, paste handling
  formatbar.ts                      # formatting bar + table menu
  findbar.ts                        # find & replace on prosemirror-search
  page.ts                           # DocEditorPage: top bar, banner, status, open/save/leave; mountDocEditor()
web/src/ui/app.ts                   # Edit switch: document kind
web/src/styles.css                  # doc editor styles (page column, banner, bars)
web/tests/doceditor-*.test.ts       # vitest (jsdom environment per file)
web/e2e/doceditor.spec.ts           # Playwright
tests/fixtures/doceditor-vectors.json  # shared vectors (text), generated from the desktop code
scripts/make_doceditor_vectors.py   # regenerates the vectors
tests/test_doceditor_vectors.py     # desktop: the JSON matches what the desktop code produces now
```

## Shared vectors (`tests/fixtures/doceditor-vectors.json`)

This file is UTF-8 JSON, generated by `scripts/make_doceditor_vectors.py` from the desktop code. Its shape is:

```json
{ "normalise": [{"in": "<html…>", "out": "<p>…</p>", "warnings": ["…"]}],
  "markdown":  [{"md": "# T\n…", "html": "<h1>T</h1>…", "md_out": "# T\n…"}],
  "txt":       [{"txt": "def f():\n    return 1\n", "html": "<p>def f():</p>…"}],
  "cases":     ["<h1>T</h1>…"] }
```

- The `normalise` inputs are the desktop test inputs. They include the sanitising cases, whitespace, CSS emphasis, nested blocks, empty `li`, text after a nested list, and `border:none`.
- `markdown` covers README-like files: fences, a quote, nested lists, a table, front matter, underline and strike, sized pictures, and a `---` rule at the start.
- `cases` is `tests/doceditor_samples.CASES`.
- Both apps assert every entry: desktop in `tests/test_doceditor_vectors.py`, web in `web/tests/doceditor-vectors.test.ts`.

---

### Task 1: Dependencies, shared vectors and types

**Files:**
- Modify: `web/package.json`, `web/package-lock.json` (add the dependencies listed in Global Constraints)
- Create:
  - `scripts/make_doceditor_vectors.py`
  - `tests/fixtures/doceditor-vectors.json`
  - `tests/test_doceditor_vectors.py`
  - `web/src/doceditor/types.ts`
  - `web/tests/doceditor-types.test.ts`

**Interfaces:**
- Produces (`types.ts`):
  - `type DocImages = Map<string, Uint8Array>` (keys `"img/N.ext"`)
  - `interface OpenedDocument { html: string; images: DocImages; sourceFormat: string; warnings: string[]; frontMatter: string; title: string; template: Uint8Array | null }` (`template` = the original DOCX bytes when the source was DOCX)
  - `interface SaveContent { html: string; images: DocImages; plainText: string; frontMatter: string; title: string; template: Uint8Array | null }`
  - Constants: `WEB_EDITABLE_DOCUMENT_EXTS`, `MAX_FILE_BYTES`, `MAX_CHARS`, `TOO_LARGE`, and every `W_*` constant from desktop `types.py` with identical names and strings
  - `warningText(warnings: string[]): string`
  - `nextImageName(images: DocImages, ext: string): string`

- [ ] **Step 1: Write `scripts/make_doceditor_vectors.py`** (desktop venv; `sys.path` → `src`). It builds the JSON shape above by calling:
  - `normalise_html_report` for the normalise inputs;
  - `io.open_document` on temp `.md` / `.txt` files for markdown/txt `html`;
  - `md_writer.to_markdown` for `md_out`;
  - `tests.doceditor_samples.CASES` for cases.

  It writes the file with `json.dump(..., ensure_ascii=False, indent=1)` + newline. Run it from the main checkout's venv, or install `-e .` in this worktree's venv (the setup script does).
- [ ] **Step 2: Write `tests/test_doceditor_vectors.py::test_vectors_match_desktop`**. It regenerates the vectors in memory and asserts they equal the committed JSON, with the message `"run scripts/make_doceditor_vectors.py"`.
- [ ] **Step 3: Write `web/tests/doceditor-types.test.ts`**:

```ts
test("warning strings match the desktop", () => {
  const py = readFileSync(new URL("../../src/umc/doceditor/types.py", import.meta.url), "utf8");
  for (const [name, value] of Object.entries(WARNINGS)) expect(py).toContain(`${name} = ${JSON.stringify(value)}`);
  expect(py).toContain(`TOO_LARGE = ${JSON.stringify(TOO_LARGE)}`);
});
test("warningText", () => {
  expect(warningText([W_HEADERS, W_COMMENTS])).toBe("This document has parts the editor can't keep: headers and footers, comments. They will be lost if you save over the original — use Save as to keep a copy.");
  expect(warningText([])).toBe("");
});
test("nextImageName", () => { /* empty map → "img/1.png"; after img/1.png and img/2.jpg → "img/3.png" */ });
```

  (`WARNINGS` is a `Record<string, string>` exported from types.ts that lists every `W_*`. Check the desktop's quoting style first: if `types.py` uses single quotes or wraps lines, compare parsed values with a regex instead.)
- [ ] **Step 4: Run** `pytest tests/test_doceditor_vectors.py` and `npm test -- doceditor-types`. Expected: RED, then GREEN after implementing.
- [ ] **Step 5: Commit** `Add shared document editor vectors, web types and editor dependencies`.

---

### Task 2: The subset normaliser (port of `html_subset.py`)

**Files:**
- Create: `web/src/doceditor/subset.ts`, `web/tests/doceditor-subset.test.ts` (first line `// @vitest-environment jsdom`)

**Interfaces:**
- Consumes: Task 1 types.
- Produces:
  - `normaliseHtmlReport(html: string, opts?: { resolveImage?: (src: string) => Uint8Array | null }): { html: string; images: DocImages; warnings: string[] }`
  - `normaliseHtml(html, opts?) → { html; images }`
  - `textLength(html: string): number`
  - `htmlTitle(html: string): string`

- [ ] **Step 1: Tests.**
  - `test.each(vectors.normalise)`: `normaliseHtmlReport(v.in)` gives `html === v.out` and warnings equal to `v.warnings`.
  - `test("idempotent")` over every `v.out`.
  - `test("deep nesting keeps text")`: 600 nested `<div>` around `x` → contains `x`.
  - `test("unreadable input throws a plain error")`.
  - `test("images: data: kept, remote dropped, resolveImage used for relative paths, never for \\\\host or //host or C: or /abs")`.
- [ ] **Step 2: Run** `npm test -- doceditor-subset`. Expected: FAIL.
- [ ] **Step 3: Implement as a faithful port of the desktop module.** Read `src/umc/doceditor/html_subset.py` and its docstring. Same tag and attribute rules, style order, colour normalisation, px → pt, whitespace rules (NBSP, tabs, `pre`), CSS emphasis, block-in-inline `<br>`, list and `li` handling, `border:none`, warnings, and UNC/absolute rejection before any resolve.
  - Parse with `new DOMParser().parseFromString(html, "text/html")`. Serialise your own tokens; never use `innerHTML`, whose output differs from lxml.
  - Walk iteratively, or recursively with a depth cap of 20 000.
  - The web has no file system: relative images are resolved only through `opts.resolveImage` (used by drag-dropped HTML with sibling files — none for now, so it is simply absent).
- [ ] **Step 4: Run.** Expected: PASS, every vector byte-identical.
- [ ] **Step 5: Commit** `Port the document editor's HTML subset to the website`.

---

### Task 3: DOCX writer (port of `docx_writer.py`)

**Files:**
- Create: `web/src/doceditor/docx-write.ts`, `web/tests/doceditor-docx.test.ts` (jsdom)

**Interfaces:**
- Consumes: `normaliseHtml` (Task 2), types.
- Produces: `writeDocx(html: string, images: DocImages, template?: Uint8Array | null): Uint8Array`. It is a complete package built with fflate:
  - `[Content_Types].xml`, `_rels/.rels`, `word/document.xml`, `word/styles.xml`, `word/numbering.xml`, `word/_rels/document.xml.rels`, `word/media/*`, and docProps.
  - It needs a minimal built-in template: put the XML strings in `web/src/doceditor/docx-template.ts`. Its styles are Normal, Heading 1–3, Quote, Code, List Bullet, List Number, Table Grid and Hyperlink, with python-docx's default values.

- [ ] **Step 1: Tests.** Unzip the result with fflate and parse it with DOMParser `"application/xml"`. Mirror the desktop `test_doceditor_docx.py` writer tests:
  - headings use style ids Heading1–3;
  - run properties b/i/u/strike/rFonts/sz (half-points)/color/`w:shd w:fill`;
  - each top-level list gets its own `w:num`, and a mixed nested list gets its own `w:num`;
  - table `gridSpan`/`vMerge`, nested tables;
  - picture `wp:extent` = px × 9525;
  - a hyperlink relationship;
  - `pageBreakBefore`; the hr bottom border; `w:ind w:left` = 720 × levels (twips);
  - `\t` becomes `w:tab`, NBSP becomes a space with `xml:space="preserve"`;
  - `border:none` gives all-none `tblBorders`;
  - an empty `li` is kept;
  - a bad picture is skipped without throwing.
  - Template: write with a template DOCX that has A4 landscape `w:sectPr`, a custom Normal font and a header part. The output keeps that `sectPr`, styles.xml and header, and the body holds only the new content.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Port the mapping and numbering logic from `docx_writer.py`, including the 9-level abstractNum per top-level list.
  - XML is built with `document.implementation.createDocument` + `XMLSerializer` (namespaced `createElementNS`).
  - Template: unzip the template, keep every part except `word/document.xml` (body cleared except the last `w:sectPr`), add Hyperlink/Code styles if they are missing, append new numbering and relationships, and zip again.
  - The output is deterministic (no timestamps in docProps beyond a fixed value) so that tests can compare.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Port the DOCX writer to the website`.

---

### Task 4: DOCX reader (port of `docx_reader.py`)

**Files:**
- Create: `web/src/doceditor/docx-read.ts`
- Modify: `web/tests/doceditor-docx.test.ts`

**Interfaces:**
- Consumes: Task 2, Task 3 (round trips), types.
- Produces: `readDocx(bytes: Uint8Array): OpenedDocument` (`sourceFormat "docx"`, `template = bytes`). It throws `Error("This Word document could not be opened.")` on a broken package.

- [ ] **Step 1: Tests.**
  - `test.each(vectors.cases)`: `readDocx(writeDocx(c, imgs)).html === normaliseHtml(c).html`.
  - Port every desktop reader test (`tests/test_doceditor_docx.py`) by building the DOCX XML by hand in the test with a small `makeDocx(bodyXml, extraParts)` helper:
    - unicode, tabs and space runs, nested and mixed lists, a list in a table cell, pictures. PNG/JPG/GIF/BMP/WebP are kept. Any other format is skipped with `W_PICTURES` (deliberate difference: the desktop converts TIFF/EMF to PNG with Pillow; the web has no such decoder in the reader). Name the test `unsupported_picture_skipped_on_web`;
    - highlight, style-only numbering, `numFmt none`, lowerLetter → `W_LISTSTYLE`;
    - block wrappers (customXml / ins / moveTo / smartTag / sdt), del / moveFrom dropped;
    - vMerge by hand, tcBorders-only tables are bordered, a "Strong" character style becomes `b`;
    - every warning (headers, text box, columns, tracked, comments, notes, fields, objects).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** as a port of `docx_reader.py`, walking `word/document.xml` with DOMParser and resolving styles from `styles.xml` and numbering from `numbering.xml`. Pictures are read from `word/media` through the relationships, and a non-web format → skip + `W_PICTURES`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Port the DOCX reader to the website`.

---

### Task 4b: Desktop — read Markdown with markdown-it-py (same parser as the website)

Owner decision (2026-11-11): both apps read Markdown with markdown-it, GitHub style.
- Reason: Python-Markdown needs 4-space indents, so a README with 2-space nested lists (`- one\n  - two`) is read flat and its structure changes when saved over.
- markdown-it-py (MIT, Executable Books) is the official Python port of markdown-it.

**Files:**
- Modify:
  - `pyproject.toml` (add `"markdown-it-py>=3.0",`)
  - `src/umc/doceditor/io.py` (the MD branch only)
  - `packaging/umc.spec` (`collect_submodules("markdown_it")` in hiddenimports if needed)
  - `src/umc/selftest.py` (the document editor step also opens a small MD with a 2-space nested list and a table)
  - `scripts/make_doceditor_vectors.py` + the regenerated `tests/fixtures/doceditor-vectors.json`
  - `tests/test_doceditor_io.py`
- The converter's own MD → HTML edge (`tools/htmlpdf.markdown_to_html`, Python-Markdown) is NOT changed in this task.

**Interfaces:**
- Produces: `umc.doceditor.io.md_to_html(text: str) -> str`, which is `MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).render(text)`.
- The website's `mdToHtml` (Task 5) must use the same configuration: `new MarkdownIt("commonmark", { html: true }).enable(["table", "strikethrough"])`.

- [ ] **Step 1: Tests** (`tests/test_doceditor_io.py`):
  - `test_two_space_nested_list` — `- one\n  - two\n    - three\n` opens as `<ul><li>one<ul><li>two<ul><li>three</li></ul></li></ul></li></ul>` and survives 3 save-over cycles unchanged;
  - the existing MD tests still pass (fences, quote, table, front matter, `<u>` / `<s>`, sized picture, a `---` rule at the start, `W_MDEXTRA` / `W_MDFORMAT`);
  - `md_writer` output re-reads to the same HTML (its 4-space nested lists are valid CommonMark).
- [ ] **Step 2: Run** — Expected: FAIL on the 2-space test.
- [ ] **Step 3: Implement**:
  - the MD branch of `open_document` uses `md_to_html`, then `normalise_html_report`;
  - add a nested-list vector with 2-space indents;
  - regenerate the vectors (the expected HTML changes for the markdown section only).
  - Check whether `md_writer` must change for CommonMark. Examples: escaping, a `1)`-style marker, hard breaks, lazy continuation lines. If it does, fix it and keep the 3-cycle stability tests green.
- [ ] **Step 4: Run** the full `pytest`, `ruff check .`, `ruff format --check .` and `python -m umc --self-test <path>` — Expected: PASS.
- [ ] **Step 5: Commit** `Read Markdown with markdown-it in the document editor`.

---

### Task 4c: Desktop — the converter reads Markdown with markdown-it too

The converter's built-in MD → HTML edge (`tools/htmlpdf.markdown_to_html`, used for MD → HTML/PDF when Pandoc isn't installed) still uses Python-Markdown. A README converted to PDF would therefore nest 2-space lists differently from the editor. After this task, Python-Markdown is no longer used anywhere.

**Files:**
- Modify:
  - `src/umc/tools/htmlpdf.py` (`markdown_to_html`)
  - `pyproject.toml` (remove `"Markdown>=3.6"` once nothing imports `markdown`)
  - `packaging/umc.spec` (drop the `markdown.extensions.*` hidden imports; add `markdown_it` if Task 4b didn't)
  - `src/umc/selftest.py` (the existing `markdown_to_html` step keeps working)
  - the converter's MD tests (find them with `grep -rn "markdown_to_html\|doc:pdf.*md\|\.md" tests/test_document*.py`)
- Python-Markdown's `toc` extension gave headings `id` attributes, so in-document links (`[see](#usage)`) work in the PDF. Keep that with a small post-pass, so no new plugin dependency is needed. The id is the heading text, lowercased, with non-word characters turned into `-`, runs of `-` collapsed, ends trimmed, and duplicates suffixed `-1`, `-2`.

**Interfaces:**
- Consumes: `umc.doceditor.io.md_to_html` (Task 4b). Reuse it, with no second configuration.
- Produces: `markdown_to_html(src, dest)` unchanged in signature.

- [ ] **Step 1: Tests:**
  - `test_md_to_pdf_two_space_nested_list` — MD → PDF with Pandoc unavailable (monkeypatch the route like the existing tests) gives a PDF whose text order shows "two" nested under "one". It's enough to check that the HTML intermediate has `<li>one<ul><li>two`.
  - `test_heading_ids_for_links` — `# Usage` gets `id="usage"`, and a duplicate heading gets `id="usage-1"`.
  - Every existing converter MD test still passes.
- [ ] **Step 2: Run** — Expected: FAIL on the new tests.
- [ ] **Step 3: Implement**:
  - `markdown_to_html` = `md_to_html` + the heading-id pass, wrapped in `_page`.
  - Remove the `Markdown` dependency and its spec entries.
  - Grep that nothing imports `markdown` any more (`grep -rn "^import markdown\|from markdown " src tests scripts`).
- [ ] **Step 4: Run** the full `pytest`, ruff, self-test, and `python scripts/convert_matrix.py --out <scratchpad>/matrix` (MD rows must not newly fail) — Expected: PASS.
- [ ] **Step 5: Commit** `Read Markdown with markdown-it in the converter too`.

---

### Task 4d: Desktop — the HTML normaliser must not crash on odd input

Found while porting (Task 2 report): `normalise_html` raises instead of cleaning in two cases, and the web port handles both.
- A link or picture whose URL makes `urllib.parse` raise, e.g. an invalid IPv6 host `http://[::1/x`.
- An `<html>` document with no `<body>`.

On the desktop, opening such a file shows an error instead of the document.

**Files:**
- Modify:
  - `src/umc/doceditor/html_subset.py`
  - `tests/test_doceditor_io.py`
  - `scripts/make_doceditor_vectors.py` + `tests/fixtures/doceditor-vectors.json` (add both inputs as normalise vectors)

- [ ] **Step 1: Tests:**
  - `test_bad_url_is_dropped_not_fatal`: `<p><a href="http://[::1/x">t</a><img src="http://[::1/x.png"></p>` gives `<p>t</p>`, and the remote-picture warning is shown when the picture was dropped.
  - `test_html_without_body`: `<html><head><title>T</title></head></html>` gives an empty document and title "T", without raising. `<html><p>x</p></html>` gives `<p>x</p>`.
  - Each output must equal what the web port (Task 2, `web/src/doceditor/subset.ts`) produces. Run both, and if they differ, align the desktop with the web behaviour described in the Task 2 report.
  - `test_indent_is_capped`: `<p style="margin-left:1000000000000000000000000000000pt">x</p>` (31 digits) gives `<p style="margin-left:720pt">x</p>`. Exponent forms like `1e30pt` aren't lengths in either app, so they give no margin. Indent levels are capped at 20 (20 × 36 pt), the same cap the web port uses, so both apps print valid CSS.
  - `test_table_first_in_cell` (found in the Task 3 cross-check, desktop `docx_writer._write_table`): when a table comes first in a cell, python-docx keeps the cell's unused first paragraph. That gives an extra `<p></p>` before the nested table, or moves the text that follows the table in front of it. For `<table><tr><td><table><tr><td><p>in</p></td></tr></table><p>after</p></td></tr></table>`, write → read must give back exactly that HTML (nested table first, then "after"), over 3 cycles. Do as the web writer does (`web/src/doceditor/docx-write.ts`): drop the unused first paragraph when the cell starts with a table, and keep the trailing paragraph Word requires after a table only when nothing follows it.
  - `test_style_id_collision`: found in the Task 3 review, and the desktop shares the gap. The writer adds "Hyperlink" / "Code" styles by name without checking that the style **id** `Hyperlink` / `Code` is free. A template whose differently named style already uses that id gets a duplicate styleId. Pick a free id, as the web writer does after its fix.
  - `test_li_whitespace_edges` (found in the Task 4b re-review, both apps). `<li><span> </span><p>x</p></li>` should give `<li>x</li>`, not a leading `<br>`. `<li>a <p>b</p></li>` should give `a<br>b`, with no trailing space before the break. Change `html_subset.py` and `web/src/doceditor/subset.ts` together, and add both as shared normalise vectors.
  - `test_c1_character_references`: `<p>it&#146;s &#150; &#128;5</p>` gives `<p>it’s – €5</p>`. Old Word / Windows HTML writes curly quotes and dashes as C1 references; HTML5 and the web port map them through Windows-1252, but the desktop drops them, which silently loses text.
- [ ] **Step 2: Run** — Expected: FAIL (desktop raises).
- [ ] **Step 3: Implement:** map numeric references `&#128;`–`&#159;` (decimal and hex) to their Windows-1252 characters before parsing, as HTML5 does. Catch `ValueError` from URL parsing (treat as unusable: drop the link target and keep its text, drop the picture), and handle a missing body. Regenerate the vectors, then run `npm test -- doceditor` in `web/`; both apps must pass the new vectors.
- [ ] **Step 4: Run** the full `pytest`, ruff, and `npm test` — Expected: PASS.
- [ ] **Step 5: Commit** `Don't fail on bad URLs or a missing body when cleaning HTML`.

---

### Task 4e: Desktop — the DOCX reader must not crash on unusual Word files

Found in the Task 4 port (report: `task-4-report.md`, "more lenient" list). The web reader opens these files, but the desktop shows an error or crashes:
- **A list paragraph with no numbering part:** python-docx `NumberingPart.new()` raises `NotImplementedError`.
- **A relationship pointing to a missing part** (a picture or header): python-docx refuses to open the file.
- **Invalid numbers** in `w:sz`, `gridSpan`, `w:numId` / `ilvl` or `wp:extent`: `int()` raises `ValueError`.
- **Part names differing only in case or percent-encoding** from their relationship target.

**Files:**
- Modify: `src/umc/doceditor/docx_reader.py`, `tests/test_doceditor_docx.py`

- [ ] **Step 1: Tests** (hand-built DOCX zips, one per case). Each opens without raising, and the HTML equals what `web/src/doceditor/docx-read.ts` produces for the same file. Expected results:
  - list without a numbering part → a bullet list;
  - missing picture → `W_PICTURES`, rest kept;
  - missing header → skipped;
  - bad numbers → the attribute is ignored;
  - case or encoding mismatch → the part is found.

  To get the expected HTML, run the web reader on the same bytes in a throwaway vitest (or copy the expectation from the web test of the same case).
- [ ] **Step 2: Run** — Expected: FAIL (desktop raises).
- [ ] **Step 3: Implement.**
  - Guard the numbering lookup (no numbering part = bullet).
  - Catch missing related parts per relationship.
  - Parse integers with a safe helper that returns None.
  - If python-docx itself refuses the package because of a dangling relationship, repair the zip in memory first: drop the dangling relationships and fix the case of part names, then hand it to python-docx.
- [ ] **Step 4: Run** the full `pytest` and ruff — Expected: PASS.
- [ ] **Step 5: Commit** `Open unusual Word files instead of failing`.

Also in this task: when the desktop writer can't use the original file as the template, tell the user rather than only logging it. Show the save message "Couldn't keep the original file's layout; it was saved with default styles." — the same text the web shows. Test via `save_document` returning or raising a notice that DocEditorPage displays.

---

### Task 4f: Both apps — keep or warn about Word content the readers drop

Found by the Task 4 review: 28 probe files read identically by both apps, which also showed shared losses. Each item below disappears **without a warning** on both desktop and web, and saving over the original then deletes it:
- **Table rows or cells wrapped in `w:sdt` / `w:customXml`.** These are repeating-section content controls, common in forms and templates. Both readers only walk direct `w:tr` / `w:tc` children.
- **Inline `w:dir` / `w:bdo`** (right-to-left text).
- **`w:ruby`** (the base text is lost).
- **`mc:AlternateContent` holding runs or paragraphs** (the `mc:Fallback` is never read).
- **Equations** (`m:oMath` / `m:oMathPara`): "E = mc²" reads as "E =".
- **`w:altChunk`** (embedded HTML or RTF).
- **`w:sym`** symbol characters.

**Files:**
- Modify: `src/umc/doceditor/docx_reader.py`, `web/src/doceditor/docx-read.ts`, `src/umc/doceditor/types.py` + `web/src/doceditor/types.ts` (new warning constant), `tests/test_doceditor_docx.py`, `web/tests/doceditor-docx.test.ts`
- Also: `scripts/make_doceditor_vectors.py` (add a `docx` section of hand-built XML bodies with expected html/warnings, asserted by both apps)

**Rulings:**
- **Keep:** read through sdt / customXml at row and cell level; read through `w:dir` / `w:bdo` (keep the text); read the ruby base text (`w:rubyBase`), dropping the annotation; read `mc:Fallback` content, or `mc:Choice` when there is no Fallback.
- **Keep as text when possible:** `w:sym` → the character for its `w:char` (hex code, Unicode or PUA as is).
- **Headings in a file with no styles part, or with a pStyle id that isn't defined there:** both readers currently read Heading 1–3 / Title / Quote as plain `<p>` (python-docx's fallback styles have no headings). Recognise Word's built-in style ids directly when the style isn't found:
  - `Heading1`–`Heading3` → h1–h3, `Heading4`–`Heading9` → h3, `Title` → h1;
  - `Quote` / `IntenseQuote` → blockquote;
  - `Code` / `HTMLPreformatted` / `SourceCode` → pre;
  - `ListBullet*` / `ListNumber*` → lists when numbering is missing.

  Test: a DOCX without styles.xml whose paragraphs use these ids.
- **Web only, `docx-package.ts` `decodeXml`:** an XML declaration labelled `ucs-2`, `unicode` or `x-user-defined` is mapped by TextDecoder to UTF-16LE or odd bytes, which garbles a part that is really UTF-8. Treat those labels like declared UTF-16 without a BOM (read as UTF-8), with a test.
- **HTML cleanup corner cases (from Task 4d):**
  - check `&#13;` (CR as a reference) gives the same text in both apps;
  - `<p><xmp>…</xmp></p>`: the web adds an extra empty paragraph, which should match the desktop;
  - the raw-text and `<pre` pre-scans in both normalisers react to a tag name written inside an attribute value (`<a title="<xmp>">`) — make them skip quoted attribute values.

  Each becomes a shared normalise vector.
- **Zip-bomb guard (both apps, from the Task 4e review):** neither reader caps the unpacked size of a DOCX; the file-size limit is only on the compressed bytes. Sum the zip entries' declared uncompressed sizes before reading, and stop decompressing past the limit, which also catches lying headers. Refuse above 500 MB with `TOO_LARGE`. Test with a small zip whose entry claims or inflates to more than the limit.
- **Can't keep → warn:** equations and altChunk. Add `W_EQUATIONS = "equations and embedded documents"` to both `types.py` and `types.ts`, raised when `m:oMath` / `m:oMathPara` / `w:altChunk` occur. The text around them is kept.

- [ ] **Step 1: Tests (both apps, same inputs):**
  - an sdt-wrapped row and cell keep their text;
  - an rtl `w:dir` run keeps its text;
  - ruby keeps the base text;
  - AlternateContent uses the Fallback;
  - `w:sym w:char="F04A"` is kept;
  - an equation paragraph gives `W_EQUATIONS` and keeps "E =";
  - altChunk gives `W_EQUATIONS`.

  Add each as a shared `docx` vector so both suites assert the same html and warnings.
- [ ] **Step 2: Run** — Expected: FAIL in both.
- [ ] **Step 3: Implement** in both readers.
- [ ] **Step 4: Run** the full `pytest`, ruff, `npm test`, `tsc` — Expected: PASS.
- [ ] **Step 5: Commit** `Keep content-control rows, right-to-left text, ruby and fallbacks; warn about equations`.

---

### Task 4g: Split the oversized document-editor modules (both apps, behaviour unchanged)

Four files have grown past the size the project keeps modules at: `subset.ts` 1227 lines, `docx-read.ts` 931, `html_subset.py` 927 and `docx_reader.py` 913. Following the Task 4f review, move code without changing it. The shared vectors and the existing tests are the safety net, so every one stays green with **no expectation changed**.
- **Pre-parse scanner, web:** `subset.ts` → new `web/src/doceditor/subset-scan.ts` (`TAG_START`, `tagEnd`, `nextTag`, `html5Refs`, `libxmlLike`, `P_MARK`, `keepCarriageReturns` and related helpers).
- **Pre-parse scanner, desktop:** `html_subset.py` → new `src/umc/doceditor/html_scan.py` (`_html5_text`, `_tag_end`, `_num_refs`, the special-tag tables).
- **DOCX package opening, desktop:** `docx_reader.py` → new `src/umc/doceditor/docx_package.py` (`_repaired`, `_check_unpacked_size`, `_open_package`), mirroring the web's `docx-package.ts`.
- **Style and list helpers, web:** `docx-read.ts` → new `web/src/doceditor/docx-styles.ts` (`BUILTIN_*`, `undefinedStyle`, `listInfo`).

Each resulting file must be under ~800 lines. Update `CLAUDE.md`'s architecture lines for the new modules.

- [ ] **Step 1:** Run the full `pytest` and `npm test` and record their counts (baseline).
- [ ] **Step 2:** Move the code. Imports are updated; no logic changes.
- [ ] **Step 3:** Run both suites again. Expected: the same counts, all passing, and `git diff --stat` shows only moves and imports. Also run `ruff` and `tsc`.
- [ ] **Step 4: Commit** `Split the document editor's largest modules`.

---

### Task 5: Markdown, TXT, HTML, ODT/RTF opening and saving

**Files:**
- Create:
  - `web/src/doceditor/markdown.ts`
  - `web/src/doceditor/open.ts`
  - `web/src/doceditor/save.ts`
  - `web/tests/doceditor-open-save.test.ts` (jsdom)
  - `web/tests/doceditor-vectors.test.ts` (jsdom)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `type Convert = (bytes: Uint8Array, fromExt: string, toExt: string, name: string) => Promise<Uint8Array>`. The page implements it with the conversion worker; tests pass a fake.
  - `splitFrontMatter(text: string): [frontMatter: string, body: string]`, with the desktop's rule: line 1 is `---`, line 2 is non-blank `^[A-Za-z0-9_-]+\s*:`, and a closing `---`/`...` exists.
  - `mdToHtml(md: string): string`: `new MarkdownIt("commonmark", { html: true }).enable(["table", "strikethrough"]).render(md)`. This is the same parser and configuration the desktop uses since Task 4b, so its output equals the desktop's for every vector after `normaliseHtml`.
  - `toMarkdown(html: string): string`: a port of `md_writer.py`, byte-identical on the vectors.
  - `openDocument(file: File, convert: Convert): Promise<OpenedDocument>`:
    - size check BEFORE reading the bytes;
    - txt (UTF-8, falling back to windows-1252; NBSP rule), md (front matter + `W_MDEXTRA` / `W_MDFORMAT`), html (`htmlTitle` + report warnings), docx (`readDocx`), odt/rtf (`convert(…, "docx")` then `readDocx`, `sourceFormat` kept, `template = null`);
    - the `MAX_CHARS` check comes after.
  - `SAVE_FORMATS: [ext, label][]` = docx "Word (DOCX)", odt "OpenDocument (ODT)", rtf "Rich Text (RTF)", pdf "PDF", md "Markdown", txt "Plain text (TXT)", html "Web page (HTML)".
  - `saveDocument(content: SaveContent, fmt: string, stem: string, convert: Convert): Promise<{ blob: Blob; name: string }>` (`name` is the download name, e.g. `stem.docx`, or `stem.zip` for Markdown with pictures):
    - docx → `writeDocx(…, content.template)`;
    - html → the standalone page with `data:` pictures and `content.title || stem`;
    - md → front matter + `toMarkdown`. Pictures: the browser can't write a sibling folder, so **md with pictures is saved as a ZIP** (`stem.md` + `stem_files/N.ext`). The download is `stem.zip`, and the page says so;
    - txt → `content.plainText`;
    - pdf → `convert(htmlPageBytes, "html", "pdf")` (the existing MuPDF route, so nothing to download beyond MuPDF);
    - odt/rtf → `convert(docx, "docx", fmt)`.
- [ ] **Step 1: Tests.**
  - `test.each(vectors.markdown)`: `normaliseHtml(mdToHtml(body)).html === v.html` and `toMarkdown(v.html) === v.md_out`.
  - `test.each(vectors.txt)`.
  - `refuses large files before reading`: a File whose `size` exceeds the limit makes `arrayBuffer` throw if called.
  - The ODT path calls `convert` with ("odt", "docx").
  - md with a picture → ZIP holding `a.md` + `a_files/1.png`, whose link is `a_files/1.png`.
  - pdf calls `convert` with "html" → "pdf".
  - A 3-cycle round trip for txt / md / docx / html through `openDocument` → `saveDocument` → `openDocument` gives identical html.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** If markdown-it differs from Python-Markdown on a vector, fix it with markdown-it options or a small post-pass in `mdToHtml`, never by changing the vector. If a difference can't be resolved, report the exact vector, its html and the difference in the task report.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Open and save every editable document format on the website`.

---

### Task 6: ProseMirror schema and subset conversion

**Files:**
- Create: `web/src/ui/doceditor/schema.ts`, `web/src/ui/doceditor/convert.ts`, `web/tests/doceditor-schema.test.ts` (jsdom)

**Interfaces:**
- Consumes: `normaliseHtml`.
- Produces:
  - `docSchema: Schema`. Nodes:
    - `doc`, `paragraph` {align, indent, pageBreak}, `heading` {level 1–3, align, indent, pageBreak}, `blockquote` (paragraph+), `code_block`;
    - `bullet_list` / `ordered_list` / `list_item` (list_item content `paragraph block*`, so an empty `li` is one empty paragraph);
    - prosemirror-tables nodes with a `borderless` attr on `table`;
    - `image` {src, width, height}, `horizontal_rule`, `hard_break`, `text`.
    - Marks: `bold`, `italic`, `underline`, `strike`, `link` {href}, `font` {family}, `size` {pt}, `color` {rgb}, `highlight` {rgb}.
  - `htmlToDoc(html: string): Node`. DOMParser + `DOMParser.fromSchema(docSchema).parse(body, { preserveWhitespace: "full" })`.
  - `docToHtml(doc: Node): string`. Custom serialisation in subset order, then the typed-space rule (a leading space or a space after a space → U+00A0, the same rule as the desktop `html_export`), then `normaliseHtml`.
- [ ] **Step 1: Tests.**
  - `test.each(vectors.cases)`: `docToHtml(htmlToDoc(c)) === normaliseHtml(c).html`.
  - `typed spaces`: inserting `"a    b"` through a transaction exports `a    b`.
  - `empty cell stays one paragraph over 3 cycles`; `empty li kept`; `border:none kept`; `tabs kept`; `page break attr round-trips`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `parseDOM` / `toDOM` for every node and mark. Map `page-break-before:always` to the `pageBreak` attr and back. Indent is `margin-left: 36·n pt`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Add the website document editor's schema`.

---

### Task 7: Editor view and formatting commands

**Files:**
- Create: `web/src/ui/doceditor/commands.ts`, `web/src/ui/doceditor/view.ts`, `web/tests/doceditor-commands.test.ts` (jsdom; build `EditorState`s directly, no `EditorView` needed for most commands)

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - `commands.ts`: ProseMirror `Command`s mirroring desktop `formatting.py`:
    - `setBlockStyle(style: "normal"|"h1"|"h2"|"h3"|"quote"|"code")`. Back to normal removes size and bold inside, as on the desktop.
    - `toggleMark(name)`, `setFont(family)`, `setSize(pt)`, `setColor(rgb)`, `setHighlight(rgb|null)`, `setAlign(a)`.
    - `toggleList("bullet"|"number")`, `changeIndent(delta)`. Nesting is capped one level below the item above, as on the desktop.
    - `insertLink(href, text?)`, `insertImage(name, w?, h?)`, `insertTable(rows, cols)`, `tableOp(op)` with the desktop op names `row_above, row_below, col_left, col_right, del_row, del_col, del_table, merge, split`.
    - `insertPageBreak`, `insertRule`, `clearFormatting`.
    - `activeState(state): {style, marks: Set<string>, font, size, align, inTable}`.
  - `view.ts`: `class DocView`:
    - `constructor(host: HTMLElement)`, `load(opened: OpenedDocument)` (resets history and the dirty flag), `content(): SaveContent` (html via `docToHtml`, only the pictures still referenced, plainText, frontMatter, title, template), `images: DocImages`, `dirty: boolean`, `onChange: () => void`, `focus()`;
    - plugins: history, keymap (the spec's shortcuts — see Task 10), tables (`columnResizing` off), search;
    - pictures shown from object URLs of `images`;
    - paste: `transformPastedHTML` through `normaliseHtml` (`data:` pictures added under fresh `nextImageName` names; remote dropped), and pasted image files added like `insertImage`.
- [ ] **Step 1: Tests**, mirroring the desktop's `test_gui_doceditor.py` command tests:
  - bold then h1 → `<h1>hello world</h1>`, then normal → `<p>hello world</p>`;
  - one undo step per command;
  - lists and indent nest validly;
  - `tableOp` row_below adds a row;
  - `clearFormatting` keeps the list;
  - `activeState` follows the cursor;
  - paste of HTML with a data: picture into a doc that already has img/1.png → two distinct pictures.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Add the website document editor's view and commands`.

---

### Task 8: Formatting bar, table menu, find & replace

**Files:**
- Create: `web/src/ui/doceditor/formatbar.ts`, `web/src/ui/doceditor/findbar.ts`, `web/tests/doceditor-bars.test.ts` (jsdom)
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Task 7 (`DocView`, commands), `ui/icons.ts` (Tabler icons).
- Produces:
  - `class FormatBar { el: HTMLElement; constructor(view: DocView, ask: Asker); refresh(): void; buttons: Record<string, HTMLButtonElement> }`.
    - Its contents match the desktop: style select (Normal, Heading 1, Heading 2, Heading 3, Quote, Code), a font select (a fixed list of common fonts + those used in the document), a size select 8–72, B/I/U/S, colour, highlight, 4 alignments, bullet and numbered lists, indent −/+, link, picture, table, page break, rule, clear formatting.
    - Table menu (`<details>` dropdown) enabled only in a table.
    - `buttons` keys are the desktop names.
    - `Asker` = `{ text(label, value?): Promise<string|null>; color(): Promise<string|null>; file(accept): Promise<File|null>; tableSize(): Promise<[number,number]|null> }`, injectable for tests.
    - The size select applies only on change (the desktop's focus-out lesson).
  - `class FindBar { el; open(replace: boolean); close(); find(text, {backward, matchCase, wholeWord}): boolean; replace(...): boolean; replaceAll(...): number; status: HTMLElement }`.
    - Built on `prosemirror-search` (`SearchQuery`, `findNext`, `replaceNext`, `replaceAll`).
    - Statuses are "No matches." and "Replaced N.".
    - Esc closes, Enter = next, Shift+Enter = previous.
- [ ] **Step 1: Tests.**
  - The bar's bold button reflects the cursor.
  - The size select applies once.
  - The table menu is disabled outside tables.
  - find wraps around.
  - `replaceAll("a","the",{matchCase,wholeWord})` gives 1 and is one undo step.
  - `"cat"→"cats"` terminates.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Styles use the existing tokens in `styles.css`, so dark and light both work. The page column itself stays white with black text, like the desktop.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `Add the website document editor's formatting and find bars`.

---

### Task 9: Document editor page

**Files:**
- Create: `web/src/ui/doceditor/page.ts`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Tasks 5, 7, 8; `ConversionClient` (`client.run(target, files, options, onProgress)`); `converterById` / `targetsFor` from `core/registry`.
- Produces:
  - `mountDocEditor(host: HTMLElement, opts: { client: ConversionClient; onBack: () => void }): DocEditorPage`.
  - `DocEditorPage` has:
    - `open(file: InputFile): Promise<boolean>`, `lastError(): string`, `show()`, `hide()`, `maybeLeave(): boolean`, `dropFiles(files: InputFile[]): Promise<void>`, `save()`, `saveAs()`;
    - `el` with test hooks: `.doc-page` (the editable page), `.doc-banner`, `.doc-status`, `.doc-message`, buttons named "Back to Convert", "Undo", "Redo", "Save", "Save as", "Save to a file…".

- [ ] **Step 1: Implement.**
  - **Layout:** a top bar (as the image editor's), then FormatBar, FindBar, the banner (`warningText`, closable), a centre white page column (794 px, scrolls), and a bottom status: `"{words} words · {chars} characters"`, plus `" · Unsaved changes"` while dirty, with the desktop's counting rule.
  - **`Convert`:** wraps `client.run`. It finds the target `doc:{to}` for an InputFile made from the bytes and name, and runs it with default options. Pandoc's download progress goes to `.doc-message` ("Downloading the document converter… N %").
  - **Open:** `openDocument` with that convert. Errors appear in `.doc-message`; the banner shows when there are warnings.
  - **Save:**
    - Save downloads the original format with the original name.
    - The first save over a source that had warnings asks `confirm("Parts of this document will be lost (…). Save anyway?")`.
    - Save as: a small dialog (reuse the image editor's `saveDialog` from `ui/editor/dialogs.ts` if its shape fits, otherwise a similar one) with a name, the format from `SAVE_FORMATS` and "Save to a file…".
    - Ctrl+S repeats the last Save as.
    - Edits made while saving stay unsaved: compare a doc version counter.
  - **Leave:** `maybeLeave` → `confirm("Discard unsaved changes to this document?")` when dirty. `beforeunload` is set while dirty.
  - **Shortcuts:** the spec's list without zoom (Ctrl+B/I/U, Ctrl+Shift+X, Ctrl+Alt+1/2/3/0, Ctrl+L/E/R/J, Ctrl+Shift+L, Ctrl+K, Ctrl+Enter, Ctrl+Space, Ctrl+Z/Y/Shift+Z, Ctrl+F/H, Ctrl+S/Shift+S).
  - **`dropFiles`:** one editable document → `maybeLeave`, then open it; anything else → `.doc-message` "Only one document can be edited at a time."
- [ ] **Step 2:** `npx tsc --noEmit`. Expected: no errors. (The behaviour is covered by Task 11's e2e.)
- [ ] **Step 3: Commit** `Add the website document editor page`.

---

### Task 10: Convert | Edit for documents on the website

**Files:**
- Modify: `web/src/ui/app.ts` (the Convert | Edit section, ~l.171-230; the drop handler ~l.117)

**Interfaces:**
- Consumes: `mountDocEditor` (Task 9), `WEB_EDITABLE_DOCUMENT_EXTS`.
- Produces: `editKind(): "image" | "document" | null` in app.ts. `canEdit()` = `editKind() !== null`. The tooltip texts are in Global Constraints.

- [ ] **Step 1: Implement.**
  - `enterEdit` branches on the kind. The document page is created lazily with `await import("./doceditor/page")`.
  - Leaving goes through the page that is showing (`maybeLeave`).
  - Drops while the doc editor is showing go to `docEditor.dropFiles`.
  - The image flow is unchanged.
- [ ] **Step 2: Build check:** `npm run build`. Compare the main entry chunk size with `main`'s (`git stash` is forbidden — build `main` in a temp worktree under the scratchpad, or record its size before you start). Expected: within +2 KB, with the ProseMirror code in its own chunk.
- [ ] **Step 3: Commit** `Offer Edit for a single text document on the website`.

---

### Task 11: End-to-end tests, cross-app check, docs

**Files:**
- Create: `web/e2e/doceditor.spec.ts`
- Modify:
  - `web/e2e/global-setup.ts` (DOCX/MD/TXT/HTML fixtures; a desktop-written DOCX via python3)
  - `.github/workflows/web.yml` (`pip install python-docx lxml Pillow` before the e2e step)
  - `README.md` (the document editor row's website column → ✅, with "DOC needs the desktop app")
  - `CLAUDE.md` (web architecture line for `doceditor/` + `ui/doceditor/`; roadmap 10.x line "Document editor on the website")
  - `docs/superpowers/specs/2026-11-10-document-editor-design.md` (Phase 2: logic runs on the page, Pandoc/MuPDF through the worker; MD with pictures downloads as ZIP; no DOC)

**Interfaces:** Consumes everything above. The fixtures go into the existing `FIXTURES` folder.

- [ ] **Step 1: global-setup.**
  - Write `editable.docx` with **pandoc-wasm**, as `report.docx` is made today (heading, bold, list, table). global-setup runs in node without a DOM, so the web writer can't be used there.
  - Write `aligned.txt` with indentation and tabs, and `readme.md` (front matter, fence, quote, nested list, table).
  - If python3 + python-docx are available, write `desktop.docx` with the desktop `docx_writer` (heading, table, tab, "Strong" text). Skip with a warning otherwise, like RAW/PSD.
- [ ] **Step 2: e2e tests** (`?e2e` exposes `window.__doceditor` for state reads, like the image editor):
  - `edit opens a docx`;
  - `bold + add table row + save docx and pdf` (downloads checked: the DOCX re-opens with the bold text and 3 rows; the PDF is non-empty and its text has the heading);
  - `three saves stay identical` (txt, md, docx: open → save → reopen the download, 3 times → identical text and html);
  - `no pandoc download for docx` (no request URL contains `pandoc` during the DOCX round trip);
  - `odt opens via pandoc` (make an ODT with pandoc-wasm in global-setup);
  - `leave asks when dirty` (dialog handler: dismiss keeps the editor);
  - `beforeunload set while dirty`;
  - `images still open the image editor`;
  - `test_cross_app_docx` (skipped without python3 + python-docx). Open `desktop.docx` on the web and compare the visible text, the bold and the table. Then save it from the web, and run `python3 -c` with the desktop `docx_reader.read` on the download. It must show the same html as the web's `__doceditor.html()`.
- [ ] **Step 3: Run the full verification:**
  - in `web/`: `npx tsc --noEmit`, `npm test`, `npm run e2e` (or the Edge config);
  - in the repo: `.venv/Scripts/pytest tests/test_doceditor_vectors.py`, `ruff check . && ruff format --check .`;
  - `node web/scripts/convert-matrix.mjs matrix-out/samples --channel msedge`: no new failures in document targets.
- [ ] **Step 4: Commit** `Add website document editor tests and document the editor`.
