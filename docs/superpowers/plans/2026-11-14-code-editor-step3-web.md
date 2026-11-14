# Code editor — step 3: the website editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop code editor's twin on the website: CodeMirror 6, same look, tabs, find &
replace, status bar, byte-exact saving (download, or "Save to a file…" + Ctrl+S on Chrome / Edge),
reached from the start-screen tile, Edit (with the ▾ second choice) — loaded only on demand.

**Architecture:** `web/src/ui/codeeditor/` is the page (one `EditorView`, one `EditorState` per
tab, swapped with `setState`), `web/src/codeedit/` gets the Qt-free pieces the desktop has:
`search.ts` (port of `search.py`, shared fixture), `cmlanguages.ts` (the language table's
`codemirror` key → a lazily imported CodeMirror language, comment marks from the table),
`textfile.ts` (step 1). Colours come from the `--code-*` CSS variables (step 2, Task 3).

**Tech Stack:** TypeScript + Vite, CodeMirror 6 (`@codemirror/view`, `state`, `commands`,
`language`, `search` (only `highlightSelectionMatches` / search state helpers if useful),
`autocomplete` not used, `lang-*` packages, `legacy-modes` for the rest) — all MIT. vitest,
Playwright.

**Spec:** `docs/superpowers/specs/2026-11-14-code-editor-design.md`; desktop twin:
`docs/superpowers/plans/2026-11-14-code-editor-step2-desktop.md`.

## Global Constraints

- Work in the `code-editor` worktree; commits as `darkbepo`, no trailers.
- The page bundle stays small: everything CodeMirror is imported only from `ui/codeeditor/`
  (dynamic `import()` from `ui/app.ts`), each language package only when a tab needs it. Check
  `npm run build` output: the entry chunk must not grow by more than ~2 KB.
- Byte-exact: saving untouched gives the bytes that were opened (`textfile.encode`); CodeMirror
  must be given `EditorState.lineSeparator`-free text with `"\n"` (textfile already normalises)
  and the page must read text with `state.doc.toString()`.
- Limits as the desktop: refuse > `MAX_OPEN_BYTES_WEB = 20_000_000` and binary (`decode` throws
  "binary"); no colours above `MAX_HIGHLIGHT_BYTES = 2_000_000` or for lines over 5000 characters
  (CodeMirror's own `highlight` works per viewport, but the language parser still parses the
  whole document — skip the language for big files).
- Same shortcuts as desktop `gui/codeeditor/shortcuts.py`, except where the browser keeps a key
  (Ctrl+N, Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab belong to the browser: New = Alt+N, close tab =
  Alt+W, next / previous tab = Alt+PageDown / Alt+PageUp; document them in the "?" window).
- Calm style: same top bar / status bar classes as the document editor page; phones (≤ 560 px):
  "← Back", secondary buttons in `.top-tools`, tabs scroll sideways. Check at 375 px.
- Test hooks: `.code-editor` page, `.code-tabs [role=tab]`, `.code-status`, buttons "Save",
  "Save as…", "Open…", "New", "Find"; `window.__codeeditor` with `?e2e` (`text()`, `setText()`,
  `tabs()`).
- README row (website ✅) and CLAUDE.md roadmap 10.17 step 3 in the same commit as the feature.

## Review Focus

1. **Saving untouched** a CRLF + BOM file, a Windows-1252 file and a file with a no-break space
   must download identical bytes (e2e, Task 5).
2. **A 5 MB log / a 1.5 MB one-line minified file** must open without freezing the tab (e2e
   timing, Task 5).
3. **Leaving with unsaved changes in a hidden tab** must ask (`maybeLeave`, Task 4 vitest-free e2e).
4. **Ctrl+S after "Save to a file…"** writes to the picked file, not a download (e2e with a faked
   `showSaveFilePicker`, Task 5).
5. **The phone layout** (375 px) has no horizontal page scroll and all buttons reachable (e2e,
   Task 5).

---

### Task 1: Dependencies and language loading (`codeedit/cmlanguages.ts`)

**Files:** `web/package.json` (+ lock), Create `web/src/codeedit/cmlanguages.ts`, Test
`web/tests/codeedit-languages.test.ts`

**Interfaces:**
- `loadLanguage(languageId: string): Promise<Extension>` — `[]` for plain text; the language's
  `LanguageSupport` (lang-* packages: css, sass (scss + less via sass indented=false), javascript
  (jsx / typescript / tsx options), python, java, cpp, rust, go, php, sql, json, xml (+ svg),
  yaml, markdown, html (+ vue, svelte)) or `StreamLanguage.define(mode)` from `legacy-modes`
  (shell, powershell, lua, ruby, toml, properties (ini, dotenv), perl, r, swift, kotlin + csharp
  via clike, dockerfile, stex). Plus `EditorState.languageData.of(() => [{ commentTokens }])`
  from the table's `line_comment` / `block_comment` so Ctrl+/ uses the same marks as desktop.
- `LANGUAGE_IDS: string[]` from `languages.json`.

- [ ] Step 1: failing test — every `languages.json` id: `await loadLanguage(id)` resolves without
  throwing; plain text → `[]`-equivalent (no language); `"css"` result contains a `LanguageSupport`.
- [ ] Step 2: `npm i @codemirror/view @codemirror/state @codemirror/commands @codemirror/language
  @codemirror/lang-css @codemirror/lang-sass @codemirror/lang-javascript @codemirror/lang-python
  @codemirror/lang-java @codemirror/lang-cpp @codemirror/lang-rust @codemirror/lang-go
  @codemirror/lang-php @codemirror/lang-sql @codemirror/lang-json @codemirror/lang-xml
  @codemirror/lang-yaml @codemirror/lang-markdown @codemirror/lang-html @codemirror/legacy-modes
  @lezer/highlight` (all MIT; record versions).
- [ ] Step 3: implement; run `npx vitest run tests/codeedit-languages.test.ts` → PASS; commit
  `Load CodeMirror languages for the website code editor`.

### Task 2: Search port (`codeedit/search.ts`) with a shared fixture

**Files:** Create `web/src/codeedit/search.ts`, `tests/fixtures/codeedit_search_cases.json`;
Modify `tests/test_codeedit_search.py` (also loop the fixture); Test `web/tests/codeedit-search.test.ts`

**Interfaces:** `SearchError`, `findAll(text, query, {caseSensitive, wholeWord, regex})`,
`replaceAll(...) → [string, number]`, `replacementAt(text, query, replacement, start, opts) →
[start, end, newText] | null` — JS `RegExp` with flags `gu` (+`i`); whole word via
`(?<![\p{L}\p{N}_])(?:…)(?![\p{L}\p{N}_])`; replacement with regex: Python `\1` / `\g<name>`
syntax translated to JS `$1` / `$<name>` (a literal `$` escaped as `$$`); positions are JS string
(UTF-16) indices — the page uses them directly (CodeMirror positions are UTF-16 too). Python
indices differ for characters beyond U+FFFF, so the fixture avoids those.

- [ ] Step 1: write the fixture: every case from `test_codeedit_search.py` as
  `{text, query, options, find?, replace?: {with, result, count}, at?: {start, result}, error?}`;
  Python test loops it too (must stay green); vitest loops it → FAIL.
- [ ] Step 2: implement; both suites PASS; commit `Port the code editor's search to the website`.

### Task 3: Colours and the editor setup (`ui/codeeditor/setup.ts`)

**Files:** Create `web/src/ui/codeeditor/setup.ts`; Test `web/tests/codeeditor-setup.test.ts`

**Interfaces:**
- `codeHighlight: HighlightStyle` mapping lezer tags to `var(--code-<kind>)`: keyword (+
  controlKeyword, modifier, operatorKeyword) → keyword; string, regexp, special(string) → string;
  comment (all) → comment (italic); number, bool, null, atom → number; function(variableName),
  function(propertyName), className, definition(…) → function; typeName, namespace → type;
  tagName → tag; attributeName, propertyName → attribute; operator → operator; standard(…),
  self → builtin.
- `baseExtensions(opts: {onChange, onCursor}): Extension[]` — lineNumbers, highlightActiveLine(+
  gutter), bracketMatching, indentOnInput, history, drawSelection, EditorView.theme using
  `--surface`, `--text`, `--code-gutter`, `--code-line`, `--code-match`, `--code-bracket`,
  monospace font stack `"Cascadia Mono", Consolas, "DejaVu Sans Mono", Menlo, monospace`, keymap
  (defaultKeymap, historyKeymap, indentWithTab, toggleComment on Mod-/), a `Compartment` each for
  language, wrap (`EditorView.lineWrapping`), font size (zoom), indent unit, read-only.
- `KIND_TAGS: Record<Kind, Tag[]>` exported for the test.

- [ ] Step 1: failing test — every kind in `KINDS` (copy of `lexing.KINDS`: keyword, string,
  comment, number, function, type, tag, attribute, operator, builtin) has tags and its style uses
  `var(--code-<kind>)`; `styles.css` has every `--code-*` variable (already true).
- [ ] Step 2: implement; PASS; commit `Add the website code editor's colours and setup`.

### Task 4: The page (`ui/codeeditor/page.ts`, `findbar.ts`)

**Files:** Create `web/src/ui/codeeditor/page.ts`, `findbar.ts`, `shortcuts.ts`; Modify
`web/src/styles.css` (`.code-editor …` rules, phone rules)

**Interfaces:**
```ts
export interface CodeEditorOptions { onBack: () => void }
export class CodeEditorPage {
  el: HTMLElement;
  show(): void; hide(): void; lastError(): string;
  open(files: InputFile[]): Promise<boolean>;   // a tab each (already open → shown); false if none opened
  openEmpty(): void;                            // "Untitled"
  maybeLeave(): boolean;                        // confirm() if any tab is dirty; "OK" discards (closes the tabs)
  save(): Promise<boolean>; saveAs(): Promise<boolean>;
}
export function mountCodeEditor(host: HTMLElement, opts: CodeEditorOptions): CodeEditorPage;
```
Tab = `{ name, file: TextFile | null, state: EditorState, languageId, encoding, newline, bom,
indent, handle?: FileHandle, colours: boolean, savedDoc: Text }`; dirty = `!state.doc.eq(savedDoc)`
or encoding / newline / bom changed. Save: `encode(file ?? decode(empty), text, {…})` → if the tab
has a handle, `createWritable` + write; else download `name` (Save as asks the name in a small
dialog with "Save to a file…" where `showSaveFilePicker` exists — same as the document editor).
Encoding error → `confirm("This text has characters … can't store. Save as UTF-8 instead?")`.
Find bar: same fields and labels as desktop ("Match case", "Whole word", "Pattern (.*)", count
"N matches" / "N replaced" / error text), matches decorated with `--code-match`, Replace needs
the selection to be exactly one match, Replace all = one transaction (one undo step).
Status bar `.code-status`: `Ln x, Col y`, language `<select>`, encoding `<select>` (UTF-8, UTF-8
with BOM, UTF-16 LE, UTF-16 BE, Windows-1252), line endings `<select>` (CRLF / LF / CR), indent.
Drop onto the page opens files (detect first; refuse binary). Big files: no language, note
"Colours are off for files this big".

- [ ] Step 1: implement the page against the interface (UI code; covered by Task 5's e2e).
- [ ] Step 2: `npx tsc --noEmit` clean; commit `Add the website code editor page`.

### Task 5: Wiring and end-to-end tests

**Files:** Modify `web/src/ui/app.ts` (`editKind` + `"code"`, `editChoices()`, a `▾` button
`.edit-more` after Edit with a menu "Edit as code" / "Edit as document", `ensureCodeEditor`,
`openEditor("code")`, `showConverter` hides it, `enterProperties` asks it), `web/src/core/editors.ts`
(code `available: true`), `src/umc/core/editors.py` (`WEB_LATER = {"video"}`),
`web/src/ui/hub.ts` nothing; Tests: Create `web/e2e/codeeditor.spec.ts`; Modify
`web/e2e/hub.spec.ts` (code tile opens an Untitled file), `web/e2e/code-files.spec.ts` (Edit now
enabled for CSS), `tests/test_editor_hub.py` (parity list).

Rules for `editChoices()` (same as desktop `MainWindow.edit_choices`): image / model / audio
rules first; one txt / md → `["document", "code"]`; one html → `["code", "document"]`; one
other `WEB_EDITABLE_DOCUMENT_EXTS` → `["document"]`; every file family `code`, or family
document / vector / subtitle with ext in the table's extensions → `["code"]`.

- [ ] Step 1: write `e2e/codeeditor.spec.ts` (each from the Review Focus, plus):
  - open `style.css` via `setInputFiles` → Edit → `.code-editor` visible, one tab;
  - untouched Save downloads identical bytes for: CRLF+BOM UTF-8, Windows-1252 (`caf\xe9`), a
    no-break space; typing "x" then Save keeps CRLF;
  - two files → two tabs in order; hidden dirty tab → Back asks (dialog handler dismiss → stays,
    accept → converter);
  - Ctrl+F, type "-", count "2 matches"; Replace all "+" → text changed, one Ctrl+Z restores;
  - "Save to a file…" with `showSaveFilePicker` faked in `addInitScript` to record writes →
    Ctrl+S writes there again;
  - a 5 MB log and a 1.5 MB one-line JS: `.code-editor` shows and typing works within 3 s;
  - `notes.txt` → `.edit-more` visible → "Edit as code" opens it here;
  - tile "Code editor" → Untitled tab;
  - 375 × 812: `document.documentElement.scrollWidth <= 375`, Save visible.
  Run `npx playwright test e2e/codeeditor.spec.ts -c playwright.edge.local.config.ts` → FAIL.
- [ ] Step 2: wire `app.ts`, flip `available`, `WEB_LATER`; PASS; `npm test`, `npx tsc --noEmit`,
  pytest `tests/test_editor_hub.py`; `npm run build` and compare the entry chunk size to `main`.
- [ ] Step 3: commit `Open code files in the website code editor`.

### Task 6: Docs and gate

- [ ] README: Code editor row website "✅" (note the browser-kept shortcuts), Start screen row
  ("video editor: Coming soon" only), Code and text files row ("editor coming later" removed).
  CLAUDE.md roadmap 10.17 step 3 ✅ (CodeMirror, lazy languages, `--code-*` HighlightStyle,
  shortcut differences, test hooks); add `.code-editor` hooks to the e2e hooks paragraph.
- [ ] Full gate: ruff, pytest, `npx tsc --noEmit`, `npm test`, full Edge e2e. Final whole-branch
  review, fixes, then ask the owner before merging.
