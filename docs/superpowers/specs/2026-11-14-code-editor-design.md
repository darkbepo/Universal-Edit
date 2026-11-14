# Code editor (both apps) — design

Date: 2026-11-14. Roadmap item 10.17 (new).

## Why

Universal Edit refuses `.css`, `.js`, `.py` and every other code file: they aren't in
`core/detect.py`'s lists and have no magic bytes, so `detect()` returns `None`. `.html` is
accepted but opens in the document editor, which keeps only a small HTML subset — scripts,
styles and classes are dropped, so saving a web page from it breaks the page. JSON / YAML / XML
can be converted and the viewer shows them as plain text, but nothing edits them as code.

The owner wants this fixed for **someone making a quick edit to a file**: open it, change a few
lines, save. Model: Notepad++ — but in the app's calm style, not its crowded toolbar and menus.

## Goals

- Code and text-config files are accepted everywhere a file can be added (drop zone, Open…,
  "Open with", the viewer).
- A **Code editor** tile on the start-screen hub and an Edit target for those files, in both apps.
- Editing that feels like Notepad++ for quick edits:
  - syntax colours for the common languages, line numbers, current-line highlight,
    matching brackets, auto-indent, Tab / Shift+Tab indent, toggle comment, go to line;
  - find & replace (match case, whole word, regular expression), with a match count;
  - one tab per open file (several dropped files → several tabs), dirty dot on the tab;
  - status bar: line:column, language (changeable), encoding, line endings (both changeable,
    applied on save), Tab / spaces;
  - word wrap toggle, zoom (Ctrl + / − / 0);
  - undo / redo, Ctrl+S saves back to the same file (same encoding, BOM and line endings unless
    changed), Save as.
- Two small extras:
  - **Preview** for HTML and Markdown (web: a side pane, live; desktop: Markdown in a side pane,
    HTML opens in the default browser — see Constraints).
  - **Checks for JSON / YAML / XML**: a syntax error is underlined with a plain-language note
    ("Missing comma before line 12"); a **Tidy** button re-indents valid files.
- Same look, shortcuts and behaviour in both apps; shared tables / fixtures checked by both
  test suites, like the other editors.

## Non-goals (first version)

- A project / folder view, multi-file search, terminal, git, build or run buttons.
- Autocomplete, language servers, linting beyond the JSON / YAML / XML syntax check.
- Split view of one file, minimap, macros, plugins, column (box) selection.
- Converting code files to anything (e.g. a highlighted PDF) — a possible later step. Code files
  still get the any-family `pack:*` archive targets that every file already has.

## Anti-goals

- It must not change the file beyond what the user typed: no reformatting on open or save, no
  silent encoding / line-ending / trailing-newline / BOM change. Opening and saving untouched must
  give identical bytes (tested).
- It must not feel like an IDE or bring menus into the app.
- It must not make the desktop download noticeably bigger (no embedded browser engine) or slow
  the website's first page (the editor is loaded on demand).

## Constraints

- Licences AGPL-3.0-compatible. Desktop: **Pygments** (BSD-2) for colouring. Web: **CodeMirror 6**
  (MIT) + its `@codemirror/lang-*` packages (MIT).
- QScintilla (what Notepad++'s engine Scintilla offers to Python) only has PyQt bindings, not
  PySide6 — ruled out.
- The desktop app has no QtWebEngine (100 MB+, not wanted), so it can't show a real HTML page
  inside the window. HTML preview = write the current text to a file in a `umc-codepreview-*` temp
  folder and open it in the default browser. A `<base href>` pointing at the original file's
  folder is injected into that copy so relative CSS / pictures still load; nothing is ever written
  into the user's folder. Markdown preview = `QTextBrowser` with the markdown-it HTML already used
  by `tools/htmlpdf.py`.
- Desktop editor is an **add-on** ("code", `umc.gui.codeeditor`, Pygments in its `site/`), like
  the other editors (roadmap 10.12); the Qt-free model `umc.codeedit` stays in the app.
- Never block the UI thread: opening / saving big files and colouring big files run off the GUI
  thread (desktop) or stay incremental (CodeMirror is already).
- Size: opening a 5 MB file takes under ~1 s and typing doesn't stutter. Above a limit
  (`MAX_HIGHLIGHT_BYTES`, ~2 MB desktop) colouring is switched off with a status-bar note; above
  `MAX_OPEN_BYTES` (50 MB desktop / 20 MB web) the file is refused with a plain message.
- Binary files must not be opened as text: a NUL byte in the first 8 KB → "This looks like a
  binary file" message.

## Approaches considered

- **A (chosen by the owner): a native editor in each app, made to match.** Desktop
  `QPlainTextEdit` + a line-number gutter + Pygments colouring; web CodeMirror 6. Light, fast,
  licence-clean. Cost: two implementations kept in step — the same pattern as every other editor
  here (shared JSON tables + mirrored tests).
- **B: CodeMirror in both,** the desktop showing it through QtWebEngine. One editor, but 100 MB+
  more download for one feature. Rejected.
- **C: Monaco (VS Code's editor) on the web.** Several MB and an IDE feel; more than quick edits
  need. Rejected.

## What gets built

### Detection (both apps)

- New family **`code`** (`MediaFamily.CODE` / web family `"code"`) with `CODE_EXTS`, listed once in
  `umc/codeedit/languages.py` (ext → language id, display name, comment tokens, Pygments lexer
  name, CodeMirror language id) and exported to `web/src/codeedit/languages.json`
  (`python -m umc.codeedit.languages`; a test checks they match, as with `rail.json`).
- First list: css, scss, less, js, mjs, cjs, jsx, ts, tsx, py, java, c, h, cpp, hpp, cs, go, rs,
  php, rb, sh, bash, ps1, bat, cmd, sql, ini, cfg, conf, toml, env, properties, log, gitignore /
  dotfiles with no extension that are known names (`.gitignore`, `.editorconfig`, `Dockerfile`,
  `Makefile`), vue, svelte, lua, kt, swift, r, pl.
- Files already in another family keep it (HTML, MD, TXT, JSON, YAML, XML, CSV, SVG, SRT…): they
  still convert as today. The code editor is offered for them through Edit (below).
- Unknown extension + no magic bytes: if the first 8 KB decode as text with no NUL, detect it as
  `code` with language "Plain text" instead of refusing it. (Builder: check this doesn't swallow
  formats some sniffer should have caught — run it after `_sniff`.)

### Which editor Edit opens

- `code` family files → code editor.
- HTML, JSON, YAML, XML, CSV / TSV, SVG (as text), subtitles (SRT/VTT/ASS…), TEX, RST → code
  editor.
- TXT and Markdown → stay with the document editor (writing, not code), and the Edit button gets a
  small drop-down / second choice "Edit as code" (owner decided, 2026-11-14).
- HTML keeps "Edit as document" as the second choice (the document editor's behaviour unchanged).
- Several files: Edit opens the code editor when **every** file is code-editable, one tab each
  (today only one document can be edited at a time; code allows many).
- The viewer's text view gets an Edit button to the code editor (`viewer/files.editor_for`).

### Model (Qt-free desktop `umc/codeedit/`, web `web/src/codeedit/`, line-by-line ports)

- `languages` — the table above; `language_for(name)`.
- `textfile` — read bytes → `TextFile(text, encoding, bom, newline, final_newline)`: BOM
  detection (UTF-8 / UTF-16 LE/BE), strict UTF-8, else Windows-1252 (same fallback the TXT → PDF
  path uses), newline = the most common of `\r\n` / `\n` / `\r` (mixed is kept as is when
  unchanged — store the original text with its line endings and only normalise if the user picks
  one). `encode(TextFile)` → bytes; round-trip identity tested with a shared fixture set
  (`tests/fixtures/codeedit_cases.json`).
- `check` — JSON / YAML / XML syntax check → list of `(line, column, message)` in plain language
  (desktop: `json`, PyYAML, lxml; web: native `JSON.parse` position, `yaml`, `fast-xml-parser`'s
  validator — both already dependencies). Messages needn't be identical across apps; line numbers
  must be (fixture cases).
- `tidy` — JSON (2-space indent, key order kept), XML (lxml pretty print / web own), YAML not
  tidied in v1.
- `comments` — toggle line comment / block comment from the language table.

### Desktop view `gui/codeeditor/` (add-on "code")

- `CodeEditorPage`: top bar like the other editors ("← Back to Convert", file tabs, Save, Find,
  Preview / Check / Tidy where they apply, "?" shortcuts window driven by one shortcut table).
- `code_view.py`: `QPlainTextEdit` subclass with a line-number gutter, current-line and bracket
  highlight, auto-indent, comment toggle, go to line (Ctrl+G).
- `highlight.py`: Pygments-backed colouring; colours from `gui/theme.py` tokens (light / dark).
  How to make it incremental is the builder's call (e.g. `QSyntaxHighlighter` with Pygments run
  over the changed region, or lexing in a worker thread and applying formats to visible blocks);
  it must meet the size goal above.
- `find_bar.py` like the document editor's, plus regex / whole word / count, replace all.
- Saving on a QThread through `codeedit.textfile.encode`; never overwrite silently a file that
  changed on disk since it was opened (ask: reload / overwrite).
- Self-test step "code editor" (opens, colours and saves a small CSS file; checks Pygments
  imports from the add-on).

### Web view `web/src/ui/codeeditor/`

- CodeMirror 6 page loaded on demand from `ui/app.ts`, same top bar / tabs / status bar; language
  packages imported dynamically per language so CSS doesn't load Python's. Theme from the same
  CSS tokens. `window.__codeeditor` with `?e2e`.
- Save = download, plus "Save to a file…" via `showSaveFilePicker` (Chrome / Edge) and Ctrl+S
  overwriting it — same as the image editor.
- HTML preview = sandboxed `<iframe srcdoc>` (no `allow-same-origin`), refreshed after typing
  pauses. Markdown preview with markdown-it (already a dependency).
- Phones (≤ 560 px): "← Back", secondary buttons in `.top-tools`, preview replaces the code
  instead of a side pane. Check at 375 px.

### Drop-zone animation (owner's request, 2026-11-14)

- Two new badges in `core/orbit.BADGES`: `("code", "CSS", …)` and `("code", "JS", …)`, each with
  its own colour that differs from the 12 already there (e.g. a CSS blue-violet and a JS yellow,
  checked against "table" / "archive"). That makes 14 badges, so the ring counts go from 5 / 4 / 3
  to 6 / 5 / 3 (outer rings first, so the inner ring near the text stays uncrowded). Regenerate
  `web/src/ui/orbit.json` (`python -m umc.core.orbit`) and, on purpose,
  `tests/fixtures/orbit_cases.json`. Check that it still looks calm (no overlaps at the narrowest
  drop zone, 375 px on the web). Done in step 1.

### Hub, add-ons, installer, docs

- `core/editors.py` / `web/src/core/editors.ts`: new `EditorInfo("code", "Code editor",
  "Quick edits to code and text files", <Tabler "code" icon>, True)`; empty editor = "Untitled"
  plain text, language changeable, first Save asks for a name.
- `core/addons.ADDONS["code"]`, `scripts/build_addons.py`, installer `[Components]` entry,
  Settings "Editors" list, CI installs it before `--self-test`.
- Explorer "Open with" (`tools/shell_integration.py`) registers the new extensions.
- README "What it can do" tables, CLAUDE.md roadmap 10.17 + architecture tree, same commit as the
  feature (per the owner's rules).

## Suggested order (one task each, ship gate each)

1. Detection + language table + `textfile` round-trip (both apps), so code files are at least
   accepted, viewable and packable; the two new drop-zone badges.
2. Desktop editor (single file → tabs), add-on, self-test.
3. Web editor.
4. Extras: preview, check, tidy (both apps).

## Calls made while writing this up (owner can overrule)

- **`.ts` is both MPEG transport stream (video) and TypeScript.** `detect()` sniffs it: an MPEG-TS
  sync byte 0x47 at offsets 0, 188, 376 (or 4, 196, 388 for 192-byte M2TS packets) → video,
  otherwise → code (TypeScript). Same rule in `web/src/core/detect.ts`, tested in both. Any other
  extension clash found while building gets the same treatment (sniff, never silently move).
- **The editor takes more files itself**, like Notepad++: an Open… button, files dropped onto the
  editor, and a "+" tab for a new Untitled file. They go through the same detect + size + binary
  checks as the converter page.
- **Explorer:** code extensions are registered for "Open with" only, not for the "Convert"
  right-click entry (there's nothing to convert a `.py` / `.bat` to, and `.bat` / `.cmd` run on
  double-click — the app never becomes their default). `shell_integration` gets its own shorter
  list for the Convert menu instead of every `*_EXTS` set. Files with no extension (`Dockerfile`)
  can't be registered; they work by drop / Open… only.

## Left open

- **Builder:** exact incremental-colouring technique on desktop; the Edit drop-down's look (keep it
  minimal: a small chevron on the Edit segment); colour palette mapping of Pygments / CodeMirror
  token types to the theme tokens (both apps should look alike).
