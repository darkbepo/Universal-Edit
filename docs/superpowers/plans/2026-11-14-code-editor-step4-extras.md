# Code editor — step 4: preview, checks and Tidy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The spec's two extras, in both apps: a **Preview** for HTML and Markdown, and **checks
for JSON / YAML / XML** — a syntax error is underlined with a plain-language note — with a
**Tidy** button that re-indents valid JSON and XML. This is the last step of the code editor.

**Architecture:** Two new Qt-free modules with line-by-line ports, as before:
`codeedit/check.py` ↔ `web/src/codeedit/check.ts` (problems with line / column / message) and
`codeedit/tidy.py` ↔ `tidy.ts` (identical output, checked by a shared fixture). The pages add a
debounced check, the underline, a status-bar note, a Tidy button and a Preview button / pane.

**Tech Stack:** Python (`json`, PyYAML, lxml — all already dependencies), markdown-it-py
(already); web: `yaml`, `fast-xml-parser`, `markdown-it` (already), `@codemirror/lint` (MIT, new,
for the underline and the gutter mark).

**Spec:** `docs/superpowers/specs/2026-11-14-code-editor-design.md` ("Two small extras",
"Model … check / tidy", "Web view … HTML preview").

## Global Constraints

- Work in the `code-editor` worktree; commits as `darkbepo`, no trailers.
- Checks and Tidy apply to languages `json` (incl. ipynb), `yaml`, `xml` (incl. `svg`). Other
  languages: no check, no Tidy button.
- Messages are plain language ("Missing comma or closing bracket", "Unclosed tag <div>"),
  never a raw parser dump. **Line numbers must be the same in both apps**; JSON columns too;
  message wording may differ (spec).
- Tidy never changes meaning: JSON 2-space indent, key order kept, non-ASCII kept as-is, a
  trailing newline kept / not added exactly as the original had; XML elements-only content
  indented by 2 spaces, text / mixed content and comments, CDATA, processing instructions and the
  declaration kept as they are. Tidy refuses (plain message) while the check reports a problem.
  One undo step.
- Preview never writes into the user's folder and never runs with the site's rights:
  desktop HTML → a `umc-codepreview-*` temp folder with `<base href="file:///…/original folder/">`
  injected, opened in the default browser; desktop Markdown → a side pane (`QTextBrowser`, the
  markdown-it HTML `tools/htmlpdf.py` already makes); web HTML / Markdown → a side pane with
  `<iframe sandbox="allow-scripts">` (no `allow-same-origin`) fed through `srcdoc`, refreshed 500 ms
  after typing stops. On phones (≤ 560 px) the preview replaces the code (a toggle), no side pane.
- Big files: no automatic check above 2 MB (the Check runs only when Tidy is pressed).
- README rows + CLAUDE.md roadmap 10.17 step 4 ✅ in the same commit as the feature.

## Review Focus

1. **A JSON error at the end of the file** (missing closing `}`) must point at the last line,
   not line 1, in both apps (fixture case).
2. **Tidy on a file with CRLF line endings or a BOM** must keep them when saved (Tidy changes
   text; `textfile.encode` keeps the tab's settings) — test in both apps.
3. **Tidy on XML with text next to tags** (`<p>Hello <b>world</b>!</p>`) must not add spaces
   inside the text (fixture case).
4. **An HTML preview with `<script>`** must not reach the page (web: the iframe has no
   `allow-same-origin`; e2e checks `contentWindow.parent.document` access throws) and relative
   pictures / CSS load on desktop through the injected `<base>`.
5. **Typing fast in a 1.5 MB JSON file** must not run the check on every key (debounced; above
   2 MB no automatic check).

---

### Task 1: Checks (`codeedit/check.py` + `check.ts`, shared fixture)

**Files:** Create `src/umc/codeedit/check.py`, `web/src/codeedit/check.ts`,
`tests/fixtures/codeedit_check_cases.json`; Test `tests/test_codeedit_check.py`,
`web/tests/codeedit-check.test.ts`

**Interfaces:**
```python
@dataclass(frozen=True)
class Problem:
    line: int        # 1-based
    column: int      # 1-based (0 = whole line)
    message: str     # plain language
CHECKED: frozenset[str] = frozenset({"json", "yaml", "xml", "svg"})
def check(language_id: str, text: str) -> list[Problem]   # [] = fine (or not a checked language)
```
TS: `check(languageId, text): Problem[]`, same fields.
- JSON: **an own small scanner in both apps** (values, strings with escapes, numbers, literals,
  commas, colons, brackets) that stops at the first error — so line / column agree exactly; the
  browser's `JSON.parse` messages and positions differ between engines. Messages: "Missing comma
  or closing bracket", "Missing closing quote", "Unexpected text after the end", "A key must be in
  double quotes", "Trailing comma", "Missing value".
- YAML: PyYAML `safe_load` → `problem_mark.line + 1`; web `yaml` `parseDocument` errors →
  `linePos[0].line`. Fixture cases chosen where both libraries agree on the line (tabs used for
  indent, an unclosed quote, a bad indent of a mapping value).
- XML: lxml `XMLSyntaxError.lineno`; web `XMLValidator.validate` → `err.line`. Fixture: unclosed
  tag, mismatched closing tag, `&` unescaped, two root elements.

- [ ] Write the fixture: `[{language, text, line, column?, ok?}]` — at least 6 JSON (incl. Review
  Focus 1 and a valid one), 3 YAML, 4 XML, 1 valid of each.
- [ ] Both test files loop it (line always, column for JSON); run → FAIL.
- [ ] Implement both; run → PASS; commit `Check JSON, YAML and XML in the code editor`.

### Task 2: Tidy (`codeedit/tidy.py` + `tidy.ts`, shared fixture)

**Files:** Create `src/umc/codeedit/tidy.py`, `web/src/codeedit/tidy.ts`,
`tests/fixtures/codeedit_tidy_cases.json`; Test `tests/test_codeedit_tidy.py`,
`web/tests/codeedit-tidy.test.ts`

**Interfaces:** `TIDY: frozenset[str] = frozenset({"json", "xml", "svg"})`;
`tidy(language_id: str, text: str) -> str` raising `TidyError(message)` when the check finds a
problem. JSON = Python `json.dumps(json.loads(text), indent=2, ensure_ascii=False)` rules
reproduced in TS (`JSON.stringify(v, null, 2)` differs for numbers like `1.0` / `1e5` and for
`\u` escapes: keep the **original number and string spellings** — so both apps tidy with the own
scanner from Task 1, re-emitting tokens as they were written, 2 spaces, `", "` → `",\n"`, empty
`{}` / `[]` kept short). XML = an own token pass in both (tags, text, comments, CDATA, PI): an
element whose children are only elements (and whitespace) gets each child on its own line,
indented 2 spaces per level; anything with non-whitespace text stays exactly as written.
Trailing newline: kept if the input ended with one.

- [ ] Fixture `[{language, text, result | error}]`: nested JSON, JSON with `1.0`, `1e5`, `"é"`,
  empty containers; XML with declaration + comment, mixed content (Review Focus 3), self-closing
  tags, attributes with `>` inside quotes, CDATA; one invalid of each → error.
- [ ] Both suites loop it → FAIL; implement both → PASS; commit `Tidy JSON and XML in the code editor`.

### Task 3: Desktop — underline, status note, Tidy, Preview

**Files:** Modify `src/umc/gui/codeeditor/code_editor_page.py`, `code_view.py`; Create
`src/umc/gui/codeeditor/preview.py`; Test `tests/test_code_editor_page.py`

**Interfaces:**
- `CodeView.set_problems(problems: list[Problem])` — wavy underline (`QTextCharFormat.WaveUnderline`,
  colour `danger`) on the problem's line from the column (whole line when 0), merged into the
  existing extra selections.
- Page: a 400 ms single-shot check timer restarted on every change of a checked language tab
  (not above 2 MB); `self.problems: list[Problem]`; status note "Line 12: Missing comma or
  closing bracket" (clicking it = go to that line; tooltip lists all); "Tidy" button in the top
  bar, visible for `TIDY` languages — refuses with the note while there are problems, otherwise
  replaces the text in one edit block (undo once); "Preview" button visible for `html` and
  `markdown`.
- `preview.py`: `html_preview_file(text: str, source: Path | None) -> Path` (temp folder
  `umc-codepreview-*`, `<base href>` injected after `<head>` or at the start, removed by the page
  on close / next preview); `MarkdownPane(QTextBrowser)` with `set_markdown(text)` (markdown-it
  HTML, links open in the default browser); the page shows it in a `QSplitter` next to the view,
  refreshed 500 ms after typing.

- [ ] Tests (each RED first): a JSON tab with `{"a": 1 "b": 2}` → after the timer, `page.problems`
  has line 1 and the note text; fixing it clears; Tidy on valid CRLF JSON → text tidied, one
  `undo()` restores, Save keeps CRLF (Review Focus 2); Tidy while broken → text unchanged, note
  says so; Preview on Markdown shows the pane with an `<h1>` for `# Title`; HTML preview file has
  the `<base>` and lies outside the source folder; a 2.1 MB JSON → no automatic check.
- [ ] Implement; `pytest tests/test_code_editor_page.py tests/test_code_view.py` → PASS; commit
  `Add checks, Tidy and Preview to the desktop code editor`.

### Task 4: Website — lint, Tidy, Preview pane

**Files:** `web/package.json` (+ `@codemirror/lint`), Modify `web/src/ui/codeeditor/page.ts`,
`setup.ts`, `web/src/styles.css`; Test `web/e2e/codeeditor.spec.ts`

- `linter()` from `@codemirror/lint` in a compartment, enabled for `CHECKED` languages under 2 MB,
  `delay: 400`, diagnostics from `check.ts` (`from` = line start + column − 1, whole line when 0,
  severity "error"); `lintGutter()`; the status note shows the first problem (click → go there).
- Tidy button (`TIDY` languages): one transaction replacing the document.
- Preview button (`html`, `markdown`): `.code-preview` pane beside the editor
  (`<iframe sandbox="allow-scripts" title="Preview">`, `srcdoc` = the HTML, or markdown-it output
  wrapped in a small page with the site's fonts); on phones the button toggles between code and
  preview.

- [ ] e2e (RED first): JSON with an error → `.cm-lintRange-error` appears and the note says
  "Line 1"; Tidy → text tidied, Ctrl+Z restores, Save keeps CRLF + BOM bytes for an untouched
  re-save after undo; Preview on `# Title` Markdown → the iframe contains an `h1`; HTML preview
  with `<script>parent.document.title='x'</script>` → the page title is unchanged (Review Focus 4);
  375 px: Preview toggles, no sideways scroll.
- [ ] Implement; `npx tsc --noEmit`, `npm test`, the e2e file → PASS; check the entry chunk didn't
  grow (`npm run build`); commit `Add checks, Tidy and Preview to the website code editor`.

### Task 5: Docs, look-check, gate, review

- [ ] Look at both apps (desktop rendered with real fonts, web at 1280 / 375 px, light and dark).
- [ ] README: Code editor row gains "· HTML / Markdown preview · JSON / YAML / XML checks · Tidy
  for JSON / XML" in both columns; CLAUDE.md roadmap 10.17 step 4 ✅ and "Code editor done".
- [ ] Full gate (ruff, pytest, tsc, npm test, full Edge e2e), a fresh whole-branch review, fixes,
  then ask the owner before merging.
