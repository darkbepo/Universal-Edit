# Code editor — step 2: the desktop editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Notepad++-style code editor in the desktop app — coloured code, line numbers, find &
replace, a tab per file, encoding / line endings in the status bar, byte-exact saving — reached
from the start-screen tile, the Edit switch and the viewer, shipped as add-on "code".

**Architecture:** The Qt-free model grows in `src/umc/codeedit/` (line-by-line lexing on top of
Pygments, text editing helpers, search); the website ports those in step 3, so they take plain
strings and return plain data. The Qt page lives in `src/umc/gui/codeeditor/` (the add-on):
one `QPlainTextEdit` view that swaps one `QTextDocument` per tab, a `QSyntaxHighlighter` that
colours in chunks from a timer, and the same top bar / status bar / worker-thread pattern as
`gui/doceditor/doc_editor_page.py`.

**Tech Stack:** Python 3.11, PySide6, Pygments (BSD-2, new dependency), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-11-14-code-editor-design.md` (step 1 plan, done:
`docs/superpowers/plans/2026-11-14-code-editor-step1-detection.md`).

## Global Constraints

- Work in the `code-editor` worktree with its `.venv`; commits as `darkbepo`, no trailers.
- Anti-goal: opening and saving untouched gives identical bytes; no reformatting, no silent
  encoding / line-ending / BOM / trailing-newline change. All saving goes through
  `codeedit.textfile.encode`.
- No menus bar, calm style: top bar like the other editors ("← Back to Convert", buttons), "?"
  shortcuts window, status bar at the bottom.
- Never block the GUI thread: open and save run on a QThread (`_Worker` pattern of
  `doc_editor_page.py`, results through bound-method slots); colouring is chunked.
- Limits: `MAX_OPEN_BYTES = 50_000_000` (refused with a plain message), `MAX_HIGHLIGHT_BYTES =
  2_000_000` (opened without colours, status-bar note "Colours are off for files this big").
- Binary files refused: "“x” looks like a binary file, not text." (`textfile.decode` raises).
- Never overwrite silently: Save writes back to the file that was opened (that's what Save
  means), but if it changed on disk since opening / last save (mtime_ns or size differ) ask
  "Overwrite / Cancel". Save as goes through the file dialog's own overwrite question.
- The editor is an add-on: only `umc.gui.codeeditor` and the `pygments` package live in it; the
  Qt-free `umc.codeedit` stays in the app (the viewer and detection use it).
- New dependency `pygments>=2.17` (BSD-2) in `pyproject.toml`.
- README "What it can do" row + CLAUDE.md roadmap 10.17 step 2 in the same commit as the
  feature they describe (Task 8).

## Review Focus

1. **A multi-line comment or string** (`/* … */` over several lines in CSS, `"""…"""` in
   Python) must stay coloured as a comment / string on every line, and typing `*/` must
   re-colour the lines below it (Task 1 lexer tests, Task 4 highlighter test).
2. **A big file** (a 1.5 MB minified JS on one line, a 40 000-line log) must open without
   freezing the window: the page is shown and accepts typing before colouring finishes (Task 4
   timing test).
3. **Closing the app / Back with unsaved changes in a tab that isn't the visible one** must still
   ask (Task 5 `maybe_leave` test).
4. **A file that changed on disk after it was opened** (another program saved it) must not be
   overwritten without asking (Task 5 test).
5. **Saving text with a character the file's encoding can't hold** (typing "日本" into a
   Windows-1252 file) must offer "Save as UTF-8" instead of failing or writing "?" (Task 5 test).

---

### Task 1: Line-by-line lexing (`codeedit/lexing.py`)

**Files:**
- Create: `src/umc/codeedit/lexing.py`
- Modify: `pyproject.toml` (add `"pygments>=2.17",  # code editor colours (BSD-2)`)
- Test: `tests/test_codeedit_lexing.py`

**Interfaces:**
- Consumes: `languages.Language.pygments` (lexer alias), `languages.language_for`.
- Produces:
  ```python
  KINDS: tuple[str, ...] = ("keyword", "string", "comment", "number", "function", "type",
                            "tag", "attribute", "operator", "builtin")
  @dataclass(frozen=True)
  class Span:
      start: int; length: int; kind: str      # kind ∈ KINDS
  def kind_of(token_type) -> str | None      # Pygments token type → kind (None = plain text)
  class LineLexer:
      def __init__(self, language_id: str) -> None
      def lex(self, line: str, state: int) -> tuple[list[Span], int]
      # state 0 = start of file; the returned int is the state after this line, the same int
      # for the same lexer stack (interned per LineLexer), so Qt's block state can hold it.
  ```
- Approach (an algorithm the signature doesn't determine): for a Pygments `RegexLexer` that is
  not an `ExtendedRegexLexer`, run its token loop yourself so the state stack survives between
  lines — a copy of `pygments.lexer.RegexLexer.get_tokens_unprocessed` (BSD-2; say so in the
  docstring) that takes the starting stack and returns the final one; lex `line + "\n"` so
  end-of-line rules fire, and drop spans past `len(line)`. Callbacks (`bygroups`, `using`) yield
  tokens as usual. Anything else (other lexer classes, or any exception) → lex the line alone
  from the root state and return state 0. `kind_of` walks the token's parents: `Comment` →
  comment, `String` → string, `Number` → number, `Keyword` → keyword (`Keyword.Type` → type),
  `Name.Function` / `Name.Class` → function, `Name.Builtin` → builtin, `Name.Tag` → tag,
  `Name.Attribute` → attribute, `Name.Decorator` → function, `Operator` → operator, else None.

- [ ] **Step 1: Write the failing tests** (`tests/test_codeedit_lexing.py`):
  - `test_css_block_comment_spans_lines`: lines `["a { color: red; } /* one", "two", "three */ b {}"]`
    lexed in order (state threaded through): line 2 is one span `(0, 3, "comment")`; line 3
    starts with a comment span covering `"three */"`; line 3's `b` is not comment.
  - `test_python_triple_quoted_string`: `['x = """a', 'b', '"""', 'y = 1']` → line 2 all
    `string`; line 4 has a `number` span at `"1"`.
  - `test_same_stack_same_state`: two different comment lines in the middle of a CSS comment
    return the same state int; a line after `*/` returns the state of line 1 before the comment
    (0).
  - `test_kinds`: `kind_of(Token.Keyword.Type) == "type"`, `kind_of(Token.Name.Function) ==
    "function"`, `kind_of(Token.Text) is None`.
  - `test_plain_text_has_no_spans`: `LineLexer("text").lex("anything at all", 0) == ([], 0)`.
  - `test_lexer_trouble_falls_back`: monkeypatch the stack-keeping loop to raise; `LineLexer("css").lex("a { color: red }", 0)`
    still returns spans (the line lexed alone) and state 0.
  - `test_every_language_lexes`: for every `Language` in `LANGUAGES`, `LineLexer(l.id).lex("a = 1 // x", 0)`
    returns without error.
- [ ] **Step 2: Run** `pytest tests/test_codeedit_lexing.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `lexing.py`; add the pyproject line.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Colour code line by line with Pygments`.

### Task 2: Editing helpers and search (`codeedit/editing.py`, `codeedit/search.py`)

**Files:**
- Create: `src/umc/codeedit/editing.py`, `src/umc/codeedit/search.py`
- Test: `tests/test_codeedit_editing.py`, `tests/test_codeedit_search.py`

**Interfaces:**
- Produces (`editing.py`, all pure, on lists of lines):
  ```python
  def detect_indent(text: str) -> str        # "\t", "  " or "    " (most common leading unit; default "    ")
  def indent_lines(lines: list[str], unit: str) -> list[str]          # blank lines stay blank
  def outdent_lines(lines: list[str], unit: str) -> list[str]         # removes one unit or up to len(unit) spaces / one tab
  def toggle_comment(lines: list[str], language_id: str) -> list[str]
  def next_line_indent(line_before_cursor: str, unit: str) -> str     # copy leading ws, + unit after { [ ( : or an opening tag
  def matching_bracket(text: str, pos: int) -> int | None             # pos at/after one of ()[]{}; skips nothing fancy
  ```
  `toggle_comment`: with a `line_comment`, all non-blank lines commented → uncomment (remove the
  marker and one following space), else comment every non-blank line at the smallest indent
  (`marker + " "`). Without one (CSS, HTML), wrap the whole range in `block_comment` / unwrap if
  the first non-blank line starts with the opener and the last ends with the closer. Languages
  with neither → lines unchanged.
- Produces (`search.py`):
  ```python
  class SearchError(ValueError): ...           # plain message, e.g. "That pattern isn't valid: missing )"
  def find_all(text: str, query: str, *, case: bool, whole_word: bool, regex: bool) -> list[tuple[int, int]]
  def replace_all(text: str, query: str, replacement: str, *, case: bool, whole_word: bool,
                  regex: bool) -> tuple[str, int]
  ```
  Empty query → `[]` / `(text, 0)`. Zero-length regex matches are skipped. With `regex=False`
  the replacement is literal; with `regex=True` `\1` / `\g<name>` work (Python `re`).

- [ ] **Step 1: Write the failing tests** (cases, each an assertion with exact values):
  - `detect_indent("a\n\tb\n\tc")=="\t"`, `detect_indent("a\n  b\n    c\n  d")=="  "`,
    `detect_indent("x")=="    "`.
  - `indent_lines(["a", "", "b"], "  ")==["  a", "", "  b"]`;
    `outdent_lines(["    a", "  b", "\tc", "d"], "    ")==["a", "b", "c", "d"]`.
  - `toggle_comment(["x = 1", "  y = 2"], "python")==["# x = 1", "#   y = 2"]` (marker at the
    smallest indent of the non-blank lines, here 0);
    `toggle_comment(["  a", "    b"], "javascript")==["  // a", "  //   b"]`; applying either
    twice gives the input back; `toggle_comment(["a {}", "b {}"], "css")==["/* a {}", "b {} */"]`
    and back; `toggle_comment(["x"], "text")==["x"]`.
  - `next_line_indent("    if x:", "    ")=="        "`, `next_line_indent("  a {", "  ")=="    "`,
    `next_line_indent("\tfoo()", "\t")=="\t"`.
  - `matching_bracket("f(a[1])", 1)==6`, `matching_bracket("f(a[1])", 6)==1`,
    `matching_bracket("f(a", 1) is None`.
  - `find_all("Cat cat cat", "cat", case=False, whole_word=False, regex=False)==[(0,3),(4,7),(8,11)]`;
    `case=True` → `[(4,7),(8,11)]`; `find_all("cat catalog", "cat", case=True, whole_word=True,
    regex=False)==[(0,3)]`; `find_all("a1 b22", r"\d+", …regex=True)==[(1,2),(4,6)]`;
    `find_all("a(b", "(", …regex=False)==[(1,2)]`; bad regex `"("` with `regex=True` raises
    `SearchError`; `find_all("abc", "x*", …regex=True)==[]`.
  - `replace_all("a-b-c", "-", "+", …)==("a+b+c", 2)`; `replace_all("x1 y2", r"(\w)(\d)", r"\2\1",
    regex=True, …)==("1x 2y", 2)`; with `regex=False` the replacement `r"\1"` is inserted as-is.
- [ ] **Step 2: Run** both test files → FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Add the code editor's editing and search helpers`.

### Task 3: Code colours in the theme

**Files:**
- Modify: `src/umc/gui/theme.py` (LIGHT / DARK dicts), `web/src/styles.css` (`:root` and the
  dark blocks — same names with `--code-*`, so step 3 can use them)
- Test: `tests/test_theme.py` (new test)

**Interfaces:**
- Produces palette keys `code_keyword`, `code_string`, `code_comment`, `code_number`,
  `code_function`, `code_type`, `code_tag`, `code_attribute`, `code_operator`, `code_builtin`,
  `code_gutter` (line-number text), `code_line` (current-line background), `code_match`
  (find-match background), `code_bracket` (matching-bracket background).
  Light: keyword `#8839ef`, string `#2f8f2f`, comment `#8c8fa1`, number `#d4570b`, function
  `#1e66f5`, type `#b7791f`, tag `#d20f39`, attribute `#b7791f`, operator `#5c6070`, builtin
  `#0e8a8a`, gutter `#9aa0ad`, line `#f4f5fa`, match `#fff2a8`, bracket `#dfe1fb`.
  Dark: keyword `#c792ea`, string `#a6d189`, comment `#7f8796`, number `#f5a97f`, function
  `#8aadf4`, type `#eed49f`, tag `#ed8796`, attribute `#eed49f`, operator `#a5adcb`, builtin
  `#8bd5ca`, gutter `#5b6070`, line `#1f2128`, match `#5a4b16`, bracket `#2c2e52`.
  (Starting values; adjust by eye in Task 4 if one is hard to read, and keep both apps equal.)

- [ ] **Step 1: Write the failing test** `test_code_colours_in_both_apps`: every
  `code_*` key exists in LIGHT and DARK, and `web/src/styles.css` contains
  `--code-<name>: <light value>` and `--code-<name>: <dark value>` for each (read the file as
  text).
- [ ] **Step 2: Run** → FAIL. **Step 3: Add the values.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Add code colours to both themes`.

### Task 4: The code view and highlighter (`gui/codeeditor/code_view.py`, `highlight.py`)

**Files:**
- Create: `src/umc/gui/codeeditor/__init__.py`, `code_view.py`, `highlight.py`
- Test: `tests/test_code_view.py`

**Interfaces:**
- Consumes: `LineLexer`, `Span`, `KINDS` (Task 1); `editing.*` (Task 2); theme `code_*` (Task 3).
- Produces:
  ```python
  class ChunkedHighlighter(QSyntaxHighlighter):
      CHUNK_LINES = 1500        # lines coloured per timer tick (0 ms single-shot QTimer)
      def __init__(self, document: QTextDocument, language_id: str) -> None
      def set_language(self, language_id: str) -> None     # re-colours from the top
      def set_enabled(self, on: bool) -> None              # off = no colours (big files)
      def done(self) -> bool                               # every block coloured
  class CodeView(QPlainTextEdit):
      cursor_moved = Signal(int, int)                      # line, column (1-based)
      def __init__(self, parent=None) -> None
      def set_indent_unit(self, unit: str) -> None
      def set_language(self, language_id: str) -> None     # for comments / auto-indent
      def toggle_comment(self) -> None                     # Ctrl+/
      def go_to_line(self, line: int) -> None              # Ctrl+G (dialog lives in the page)
      def zoom(self, steps: int) -> None                   # 0 = reset; Ctrl+= / Ctrl+- / Ctrl+0
      def set_wrap(self, on: bool) -> None                 # Alt+Z
  ```
- Highlighter approach: `highlightBlock` colours a block only once the chunk timer has reached
  it (`self._ready_until` block number); later blocks get `setCurrentBlockState(-1)` and no
  formats. The timer advances `_ready_until` by `CHUNK_LINES` and calls `rehighlightBlock` for
  those blocks, so the window stays responsive. Edits: Qt re-highlights the changed block and,
  while its end state changes, the following ones — that is what makes `*/` re-colour the rest.
  Block state = the `LineLexer` state int (`previousBlockState()`, `-1` → 0). Formats come from
  `theme.palette()["code_<kind>"]`; comments italic; re-built on the theme's change signal.
- CodeView: line-number gutter (a child widget painted from `firstVisibleBlock()`, width from
  the digit count, colour `code_gutter`), current-line background (`code_line`) and matching
  bracket (`code_bracket`) via `setExtraSelections`, monospace font (`QFontDatabase.systemFont(
  FixedFont)`, 10 pt), Tab / Shift+Tab indent / outdent the selected lines (one undo step:
  `beginEditBlock`), Enter inserts `next_line_indent`, tab stop = 4 spaces' width, no rich text.

- [ ] **Step 1: Write the failing tests** (`tests/test_code_view.py`, pytest-qt):
  - `test_highlighter_colours_a_multiline_comment`: CSS text with a 3-line `/* */`, wait until
    `done()`; the middle block's formats (`block.layout().formats()`) are all the comment colour.
  - `test_closing_a_comment_recolours_below`: CSS `"/* a\nb {}\nc {}"`, wait `done()`; insert
    `" */"` at the end of line 1; line 2's `b` is no longer comment-coloured.
  - `test_big_file_stays_responsive`: 40 000 lines of CSS; after `setPlainText` +
    `ChunkedHighlighter(...)` the call returns in < 1.0 s and `done()` is False; `qtbot.waitUntil(h.done, timeout=30000)`.
  - `test_tab_indents_the_selection_in_one_undo_step`: select lines 1–2 of `"a\nb\nc"`, press
    Tab → `"    a\n    b\nc"`; `undo()` once → original.
  - `test_enter_keeps_indent`: text `"  if x:"`, cursor at end, unit `"  "`, press Enter →
    second line `"    "`.
  - `test_toggle_comment_python`: two lines → commented → toggled back.
  - `test_gutter_widens_with_more_digits`: 9 lines vs 10 000 lines → gutter width grows.
- [ ] **Step 2: Run** `QT_QPA_PLATFORM=offscreen pytest tests/test_code_view.py -v` → FAIL.
- [ ] **Step 3: Implement** `highlight.py`, then `code_view.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Add the code view with line numbers and chunked colouring`.

### Task 5: The page (`gui/codeeditor/code_editor_page.py`, `find_bar.py`, `shortcuts.py`)

**Files:**
- Create: `src/umc/gui/codeeditor/code_editor_page.py`, `find_bar.py`, `shortcuts.py`,
  `tab.py` (one open file)
- Test: `tests/test_code_editor_page.py`

**Interfaces:**
- Consumes: `textfile.decode/encode` (step 1), `languages.language_for/LANGUAGES`, `search.*`
  (Task 2), `CodeView`, `ChunkedHighlighter` (Task 4).
- Produces:
  ```python
  @dataclass
  class CodeTab:                       # tab.py
      path: Path | None                # None = Untitled
      file: TextFile | None            # None for Untitled (first save: UTF-8, no BOM, "\n")
      document: QTextDocument          # QPlainTextDocumentLayout set
      highlighter: ChunkedHighlighter
      language_id: str
      encoding: Encoding; newline: Newline; bom: bool    # what Save writes (status-bar menus change them)
      indent: str
      disk_stamp: tuple[int, int] | None                 # (mtime_ns, size) at open / last save
      @property def dirty(self) -> bool                  # document.isModified() or encoding/newline/bom changed
      @property def title(self) -> str                   # file name or "Untitled"
  class CodeEditorPage(QWidget):
      back_requested = Signal()
      def open_paths(self, paths: list[Path]) -> bool    # one tab each; an already-open file is focused; False if none opened (message shown)
      def open_empty(self) -> bool                       # a new "Untitled" tab
      def save(self) -> bool; def save_as(self) -> bool  # current tab
      def close_tab(self, index: int) -> bool            # asks Save / Don't save / Cancel when dirty
      def maybe_leave(self) -> bool                      # every dirty tab: one question "Save all / Discard / Cancel"
      def drop_paths(self, paths: list[Path]) -> None    # = open_paths
      busy: bool                                         # an open or save thread is running
      tabs: QTabBar; view: CodeView; find_bar: FindBar; message: QLabel
  ```
- Behaviour to pin:
  - Top bar: "← Back to Convert" | "Open…" "New" | (stretch) | "Find" "?" | "Save as…" "Save".
    Under it the tab bar (closable, movable, dirty tabs show "● name").
  - Status bar (right side): `Ln 12, Col 4` · language (button → menu of `LANGUAGES` names) ·
    encoding (button → UTF-8 / UTF-8 with BOM / UTF-16 LE / UTF-16 BE / Windows-1252) · line
    endings (button → Windows (CRLF) / Unix (LF) / Old Mac (CR)) · `Spaces: 4` / `Tab`. Changing
    encoding / line endings marks the tab dirty and is applied on Save.
  - Opening reads the file on a QThread: size check, `decode`, `detect_indent`; > 2 MB →
    highlighter disabled + note.
  - Save: `encode(tab.file, text, encoding=…, newline=…, bom=…)`; `ValueError("encoding")` →
    ask "This text has characters Windows-1252 can't store. Save as UTF-8 instead?" (Yes → switch
    the tab to UTF-8 and save; No → nothing written). Disk stamp differs → "“x” was changed by
    another program since you opened it. Overwrite it?" Write = temp file in the same folder +
    `os.replace`. Untitled → Save as (default name `Untitled.txt` in Documents).
  - Find bar (Ctrl+F; Ctrl+H shows the replace row): query, match case, whole word, regex,
    "3 of 12" count, Enter / F3 next, Shift+F3 previous, Replace, Replace all (one undo step,
    via `search.replace_all` on the whole text then `QTextCursor` select-all + insertText inside
    one edit block), Esc closes. Matches painted with `code_match` extra selections. A
    `SearchError` shows its message in the bar.
  - Shortcuts (`shortcuts.py` one table → bindings and the "?" window, like the image editor):
    Ctrl+N new, Ctrl+O open, Ctrl+S, Ctrl+Shift+S, Ctrl+W close tab, Ctrl+Tab / Ctrl+Shift+Tab
    next / previous tab, Ctrl+F, Ctrl+H, F3, Shift+F3, Ctrl+G go to line, Ctrl+/ comment, Tab /
    Shift+Tab, Alt+Z wrap, Ctrl+= / Ctrl+- / Ctrl+0 zoom, Ctrl+Z / Ctrl+Y undo / redo, F1 "?".
    The "?" window is this package's own small QDialog (the image editor's lives in another
    add-on).

- [ ] **Step 1: Write the failing tests** (`tests/test_code_editor_page.py`; files in
  `tmp_path`; wait for `not page.busy` with `qtbot.waitUntil`):
  - `test_open_edit_save_keeps_bytes`: open a CRLF + BOM UTF-8 CSS, save untouched → bytes
    identical; type a character, save → still CRLF + BOM, the new character present.
  - `test_tabs_and_duplicates`: open `a.css`, `b.js`, then `a.css` again → 2 tabs, current = a.
  - `test_binary_and_huge_are_refused`: a NUL-file → message contains "binary"; a file of
    `MAX_OPEN_BYTES + 1` bytes (sparse write) → refused, no tab.
  - `test_big_file_has_no_colours`: 2.1 MB text → highlighter disabled, status note shown.
  - `test_encoding_menu_marks_dirty_and_saves_converted`: UTF-8 file → choose Windows-1252 →
    dirty; save → bytes are cp1252.
  - `test_unencodable_offers_utf8` (monkeypatch `QMessageBox.question` → Yes): 1252 file +
    "日本" → saved as UTF-8; with No → file unchanged.
  - `test_changed_on_disk_asks` (question → No): open, rewrite the file externally (bump mtime),
    edit, save → file keeps the external content.
  - `test_maybe_leave_asks_for_hidden_dirty_tab` (question → Cancel): dirty tab 1, tab 2 shown →
    `maybe_leave()` is False; with "Discard" → True.
  - `test_untitled_saves_through_save_as` (monkeypatch `QFileDialog.getSaveFileName`): new tab,
    type, save → file written UTF-8 "\n".
  - `test_find_and_replace_all_one_undo`: "a-b-c", replace "-" → "+" all → "a+b+c", count label
    "2 replaced"; one `undo()` → "a-b-c"; bad regex shows a message, nothing changes.
  - `test_every_shortcut_has_an_action_and_no_clash`: like `tests/test_editor_shortcuts*`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `tab.py`, `find_bar.py`, `shortcuts.py`, then the page.
- [ ] **Step 4: Run** `pytest tests/test_code_editor_page.py tests/test_code_view.py -v` → PASS.
- [ ] **Step 5: Commit** `Add the desktop code editor page`.

### Task 6: Reaching it — tile, Edit switch, viewer

**Files:**
- Modify: `src/umc/core/editors.py` (code tile `available=True`; `WEB_LATER = {"video", "code"}`),
  `src/umc/codeedit/languages.py` (`EDITABLE_EXTS: frozenset[str]` = every table extension
  + `""`; `DOCUMENT_FIRST = frozenset({"txt", "md"})`), `src/umc/gui/main_window.py`
  (`edit_choices`, `edit_kind`, Edit ▾ button, `_page("code")`, `enter_edit`, `open_editor`,
  tooltip), `src/umc/viewer/files.py` + `src/umc/gui/viewer/viewer_window.py` (Edit opens code
  files in the code editor)
- Test: `tests/test_editor_hub.py` (update: code enabled), `tests/test_code_edit_switch.py`,
  `tests/test_viewer_files.py`

**Interfaces:**
- Produces in `main_window.py`:
  ```python
  def edit_choices(self) -> list[str]   # editors that can open the files, default first
  def edit_kind(self) -> str | None     # edit_choices()[0] if any
  def enter_edit(self, kind: str | None = None) -> None   # kind from the ▾ menu
  ```
  Rules (after the existing image / model / audio / video rules, which win):
  - one file, ext in `DOCUMENT_FIRST` (txt, md) → `["document", "code"]`;
  - one file `html` → `["code", "document"]`;
  - one other `EDITABLE_DOCUMENT_EXTS` file (docx, odt, rtf, doc) → `["document"]`;
  - every file `family == CODE` or `ext in EDITABLE_EXTS` → `["code"]`;
  - else `[]`.
  `self.edit_more` = a small `QToolButton` ("▾", tooltip "Other editors") right after the Edit
  segment, visible only when `len(edit_choices()) > 1`; its menu: "Edit as code" / "Edit as
  document" → `enter_edit("code" | "document")`.
- Viewer: `viewer.files.editor_id_for(kind: str, path: Path) -> str | None` — TEXT kind and the
  file's extension not in `EDITABLE_DOCUMENT_EXTS` but in `EDITABLE_EXTS` (or a `CODE_FILENAMES`
  name) → `"code"`; otherwise today's `editor_for(kind).id` rule. `can_edit` uses it.

- [ ] **Step 1: Write the failing tests:**
  - hub: `enabled == ["image", "model", "video", "audio", "document", "code"]`; the old
    `code` "Coming soon" lines removed; `open_editor("code")` opens an Untitled tab.
  - `test_code_edit_switch.py`: one `.css` → `edit_kind()=="code"`, Edit enabled, ▾ hidden,
    `enter_edit()` shows the code page with one tab; one `.txt` → `["document", "code"]`, ▾ shown,
    `enter_edit("code")` opens it in the code editor; one `.html` → `["code", "document"]`;
    `.css` + `.js` → code with two tabs; `.css` + `.png` → `[]`; one `.docx` → `["document"]`.
  - viewer: `vf.can_edit(vf.TEXT, Path("a.css"))` is now True and `editor_id_for(vf.TEXT,
    Path("a.css")) == "code"`; `editor_id_for(vf.TEXT, Path("a.txt")) == "document"`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** those files + `tests/test_gui_viewer.py` → PASS.
- [ ] **Step 5: Commit** `Open code files in the code editor from the tile, Edit and the viewer`.

### Task 7: Add-on, packaging, self-test

**Files:**
- Modify: `src/umc/core/addons.py` (`"code": Addon("code", "Code editor", modules=("umc.gui.codeeditor",), packages=("pygments",), entry="umc.gui.codeeditor.code_editor_page")`),
  `packaging/installer.iss` ([Components] `code`, [Files] `UniversalEdit-addon-code-*.zip`,
  [Run] condition `… or video or code`), `.github/workflows/build-windows.yml` (the installed
  add-on loop gets `"code"`), `src/umc/selftest.py` (`_code_editor_check`, listed after the
  video editor step as "code editor (colours, edit, save byte-exact)")
- Test: `tests/test_addons.py` (existing per-add-on tests pick it up; add one asserting
  `"pygments"` is in the code add-on's packages and `umc.codeedit` is NOT in any add-on's
  modules), `tests/test_selftest*.py` if one lists steps

**Interfaces:**
- `_code_editor_check(d: Path) -> list[Path]`: imports `umc.gui.codeeditor.code_editor_page`;
  `LineLexer("css")` over a 3-line comment gives comment spans on line 2; writes a CRLF CSS file,
  `decode` → `encode` untouched equals the bytes; returns `[that file]`.
- Note: Pygments isn't used by the app itself, so the spec's add-on exclusion keeps it out of the
  main build; `build_addons.py` copies it into the add-on's `site/` (as for onnxruntime).

- [ ] **Step 1: Write the failing tests** (add-on entry + packages; self-test step name listed).
- [ ] **Step 2: Run** `pytest tests/test_addons.py -v` → FAIL. **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_addons.py -v` and
  `python -m umc --self-test <scratch>/report.txt` from source → PASS / exit 0.
- [ ] **Step 5: Commit** `Ship the code editor as an add-on`.

### Task 8: Docs, look-check and ship gate

**Files:**
- Modify: `README.md` ("The other editors in detail": a **Code editor** row — desktop ✅,
  website "coming later"; the "Code and text files" row loses "a code editor is on its way";
  "Start screen tiles" row: the code editor opens a new Untitled file), `CLAUDE.md` (roadmap
  10.17 step 2 ✅ with the architecture notes: LineLexer stack-preserving loop, chunked
  highlighter, add-on "code"; architecture tree `gui/codeeditor/`; `codeedit/` gains
  lexing / editing / search)

- [ ] **Step 1: Look at it:** run `python -m umc`, open a CSS, a Python file, a 40 000-line log
  and an HTML file, in dark and light; type across a comment end; find / replace; switch tabs;
  change encoding; Save. Screenshot the CSS tab in both themes for the summary. Adjust any
  hard-to-read `code_*` colour (both apps' values together).
- [ ] **Step 2: Update the docs.**
- [ ] **Step 3: Full gate:** `ruff check . && ruff format --check .`, `pytest`, and in `web/`
  `npx tsc --noEmit`, `npm test`, `npx playwright test -c playwright.edge.local.config.ts`
  (the hub tests change: the website keeps "Coming soon" for code via `WEB_LATER`).
- [ ] **Step 4: Commit** `Document the desktop code editor`, run the ship skill, and wait for
  the owner's go-ahead before merging.
