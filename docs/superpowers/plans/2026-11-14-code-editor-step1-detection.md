# Code editor — step 1: accept code files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both apps accept code / text-config files (new family `code`), show them in the viewer
as text, can pack them into archives, read and write them byte-exactly (`textfile`), and the
drop-zone animation gains CSS and JS badges. No editor yet (steps 2–4 have their own plans).

**Architecture:** One Qt-free language table (`umc/codeedit/languages.py`) is the source of
truth, exported to `web/src/codeedit/languages.json` like `rail.json` / `orbit.json`. Detection in
both apps builds `CODE_EXTS` / known file names from it. `textfile` (decode / encode / looks-like-
text) is a line-by-line port in both apps, pinned by one shared fixture file.

**Tech Stack:** Python 3.11 (desktop, pytest), TypeScript + Vite (web, vitest, Playwright).

**Spec:** `docs/superpowers/specs/2026-11-14-code-editor-design.md`

**Execution order:** 1 → 4 → 2 → 3 → 5 → 6 → 7 (detection in Tasks 2 / 3 uses Task 4's
`looks_like_text` / `looksLikeText`; Task 6 is independent apart from the icon added in Task 2).

## Global Constraints

- Work only in the `code-editor` worktree with its own `.venv` (run
  `scripts/setup_worktree.ps1 -Worktree <this worktree>` first if `.venv` / `web/node_modules`
  are missing).
- Commits authored as `darkbepo`, imperative subject, **no co-author or session trailers**.
- Opening and saving untouched must give identical bytes (spec anti-goal).
- Files already in another family keep it (HTML, MD, TXT, JSON, YAML, XML, CSV, TSV, SVG,
  subtitles, TEX, RST…). Only `.ts` is decided by its content.
- Binary = a NUL byte in the first 8 KB (except after a UTF-16 BOM).
- Code extensions go to Explorer "Open with" only, never to the "Convert" right-click entry.
- No new dependencies in this step.
- README "What it can do" tables + CLAUDE.md roadmap / architecture updated in the same commit
  as the feature they describe (Task 7).

## Review Focus

1. **A `.ts` video recording** (MPEG-TS, 188- or 192-byte packets) must still be a video, and a
   TypeScript file must be code — in both apps (Task 2 / Task 3 tests).
2. **A binary file with an unknown extension** (e.g. `.dat` with random bytes, `.bin` with NULs)
   must still be refused, not shown as garbage "Plain text" (Task 4 `looks_like_text` cases).
3. **A UTF-8 file cut at the 8 KB sniff boundary in the middle of a multi-byte character** must
   still count as text (Task 4 case `utf8_split_at_8k`).
4. **Mixed line endings** (`\r\n` and `\n` in one file) saved untouched must come back byte-identical
   (Task 4 case `mixed_newlines`).
5. **Windows-1252 bytes that cp1252 leaves undefined** (0x81, 0x8D, 0x8F, 0x90, 0x9D) must decode
   and re-encode the same in both apps (Python's `cp1252` codec raises on them; the web's
   `TextDecoder` maps them to U+0081…) (Task 4 case `cp1252_undefined_bytes`).

---

### Task 1: Language table (desktop source, web copy)

**Files:**
- Create: `src/umc/codeedit/__init__.py` (docstring only), `src/umc/codeedit/languages.py`
- Create: `web/src/codeedit/languages.json` (generated)
- Test: `tests/test_codeedit_languages.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Language:
      id: str                      # "css", "javascript", "python", "text", …
      name: str                    # shown in the status bar: "CSS", "JavaScript", "Plain text"
      exts: tuple[str, ...]        # canonical, lowercase, no dot
      filenames: tuple[str, ...]   # whole lowercase names: "dockerfile", ".gitignore"
      line_comment: str | None     # "//", "#", "--", "REM", None
      block_comment: tuple[str, str] | None   # ("/*", "*/"), ("<!--", "-->"), None
      pygments: str                # Pygments lexer alias ("css", "js", "text", …)
      codemirror: str              # web language key used in step 3 ("css", "javascript", "python", "plain", …)

  LANGUAGES: tuple[Language, ...]
  PLAIN: Language                  # id "text", name "Plain text"
  def language_for(name: str) -> Language   # by whole name first, then extension; else PLAIN
  def export() -> dict             # {"languages": [asdict(l) …]} (tuples → lists)
  ```
  `python -m umc.codeedit.languages > web/src/codeedit/languages.json` (indent=1, trailing
  newline — same as `umc.core.orbit`).
- Table contents: the spec's first list (css, scss, less, js/mjs/cjs/jsx, ts/tsx, py, java,
  c/h, cpp/hpp/cc/cxx, cs, go, rs, php, rb, sh/bash, ps1, bat/cmd, sql, ini/cfg/conf,
  toml, env, properties, log, vue, svelte, lua, kt, swift, r, pl) **plus** the editable files that
  belong to other families (html, xml, svg, json, yaml, csv, tsv, md, txt, srt, vtt, ass, ssa,
  tex, rst) so later steps can colour them. Filenames: `dockerfile`, `makefile`, `.gitignore`,
  `.gitattributes`, `.editorconfig`, `.env`, `.npmrc`.

- [ ] **Step 1: Write the failing tests** in `tests/test_codeedit_languages.py`:
  - `test_web_copy_matches`: `json.loads(web/src/codeedit/languages.json) == json.loads(json.dumps(languages.export()))`, message "run python -m umc.codeedit.languages".
  - `test_lookup`: `language_for("style.CSS").id == "css"`, `language_for("app.tsx").id == "typescript"`,
    `language_for("Dockerfile").id == "dockerfile"`, `language_for(".gitignore").id == "gitignore"`,
    `language_for("notes.xyz") is PLAIN`, `language_for("run.bat").line_comment == "REM"`.
  - `test_no_extension_twice`: every ext and every filename appears in exactly one language.
  - `test_lexers_exist`: skip unless `pygments` importable; every `pygments` alias resolves
    with `pygments.lexers.get_lexer_by_name`.
- [ ] **Step 2: Run** `pytest tests/test_codeedit_languages.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `languages.py`**, then generate the JSON with the command above.
- [ ] **Step 4: Run** `pytest tests/test_codeedit_languages.py -v` → PASS (lexer test may skip).
- [ ] **Step 5: Commit** `Add the code editor's language table`.

### Task 2: Desktop detection — family `code`

**Files:**
- Modify: `src/umc/core/detect.py` (new `MediaFamily.CODE`, `CODE_EXTS`, `CODE_FILENAMES`,
  `.ts` sniff, plain-text fallback), `src/umc/gui/main_window.py:63` (noun
  `("code file", "code files")`), `src/umc/gui/icons.py:26` (`MediaFamily.CODE: "code"`),
  `src/umc/properties/read.py:32` (`MediaFamily.CODE: "code file"`)
- Modify: `src/umc/gui/icon_data.py`, `web/src/ui/icon_data.ts` — add Tabler 3.24.0 `code`
  (same SVG path data in both)
- Test: `tests/test_detect_code.py`

**Interfaces:**
- Consumes: Task 1 `LANGUAGES`; Task 4 `looks_like_text(head: bytes) -> bool`.
- Produces:
  - `MediaFamily.CODE = "code"`
  - `CODE_EXTS: set[str]` = all `Language.exts` minus every other family's set, plus `"ts"`
    handled below. `family_of_ext("css") == CODE`; `family_of_ext("html") == DOCUMENT` (unchanged).
  - `CODE_FILENAMES: set[str]` = all `Language.filenames`.
  - `detect(path)` rules, in this order:
    1. lowercase whole name in `CODE_FILENAMES` → `code`, `ext = name.lower().lstrip(".")`
       (`"dockerfile"`, `"gitignore"`).
    2. ext `"ts"` → `_is_mpeg_ts(head)` ? video : code.
       `_is_mpeg_ts`: `head[0] == head[188] == head[376] == 0x47`, or (M2TS)
       `head[4] == head[196] == head[388] == 0x47`; needs ≥ 392 bytes read.
    3. existing extension lookup.
    4. existing `_sniff`.
    5. new: first 8192 bytes → `looks_like_text` → `code` with `ext = canonical_ext(suffix)`
       (may be `""`).
    6. else `None`.
  - `DetectedFile.stem` keeps working when `ext == ""` (returns `path.name` for dotfiles like
    `.gitignore`; `Path.stem` already does).

- [ ] **Step 1: Write the failing tests** in `tests/test_detect_code.py` (write files into
  `tmp_path`):
  - `test_css_is_code`: `detect(a.css).family == MediaFamily.CODE` and `.ext == "css"`.
  - `test_other_families_unchanged`: `.html`→DOCUMENT, `.json`→DOCUMENT, `.svg`→VECTOR,
    `.srt`→SUBTITLE, `.txt`→DOCUMENT.
  - `test_named_files`: `Dockerfile` → CODE ext `"dockerfile"`; `.gitignore` → CODE ext
    `"gitignore"`, `stem == ".gitignore"`.
  - `test_ts_typescript`: `a.ts` containing `"const x: number = 1;\n"` → CODE.
  - `test_ts_video_188` / `test_ts_video_192`: 3 packets of `b"\x47" + 187 zero bytes`
    (and the 192-byte M2TS layout with 4 header bytes) → VIDEO, ext `"ts"`.
  - `test_unknown_text_file`: `notes.nfo` with ASCII text → CODE ext `"nfo"`; a file named
    `README` (no suffix) with text → CODE ext `""`.
  - `test_unknown_binary_refused`: `blob.dat` with `bytes(range(256)) * 40` → `None`.
  - `test_pack_targets_only`: `targets_for([detect(a.css)])` ids are exactly the `pack:*` ones
    (`{"pack:zip", "pack:7z", "pack:tar.gz"}`).
  - `test_every_family_has_icon_and_noun`: every `MediaFamily` is in `gui.icons`' family map and
    `main_window`'s noun map (import the dicts) — catches the next new family too.
- [ ] **Step 2: Run** `pytest tests/test_detect_code.py -v` → FAIL.
- [ ] **Step 3: Implement** the detect rules, maps and icon.
- [ ] **Step 4: Run** `pytest tests/test_detect_code.py tests/test_core.py tests/test_orbit.py -v` → PASS
  (orbit's "every badge has an icon" still passes; `code` icon exists in both sets).
- [ ] **Step 5: Run the whole desktop suite** `pytest -q` → no new failures (any existing test
  that expected an unknown text file to be refused must be updated on purpose — list it in the
  commit message).
- [ ] **Step 6: Commit** `Accept code files on the desktop`.

### Task 3: Web detection — family `code`

**Files:**
- Modify: `web/src/core/types.ts:5` (`Family` += `"code"`), `web/src/core/detect.ts`,
  `web/src/ui/app.ts:20` (NOUN `code: ["code file", "code files"]`), `web/src/ui/icons.ts:24,32`
  (`code: "code"`), `web/src/properties/read.ts:12` (`code: "code file"`)
- Create: `tests/fixtures/detect_code_cases.json` — shared cases: `{name, bytes_b64, family, ext}`
  covering every desktop test in Task 2 (css, html, json, svg, srt, txt, Dockerfile, .gitignore,
  ts code, ts 188, ts 192, nfo, README, blob.dat → family `null`).
- Test: `web/tests/detect-code.test.ts`, `tests/test_detect_code.py::test_shared_cases`,
  `web/e2e/code-files.spec.ts`

**Interfaces:**
- Consumes: `languages.json` (Task 1), `looksLikeText(head: Uint8Array): boolean` (Task 4).
- Produces: same rules as Task 2 in `detect(file: File)`; plus a pure, testable
  `detectBytes(name: string, head: Uint8Array): { family: Family; ext: string } | null` that
  `detect` calls (head = first 8192 bytes); `CODE_EXTS`, `CODE_FILENAMES` exported.
  Note `canonicalExt(".gitignore")` today returns `"gitignore"` and `file.name.includes(".")` is
  true — the filename rule must run first so both apps agree.

- [ ] **Step 1: Write the fixture** and the failing tests: vitest loops the cases through
  `detectBytes` and expects `{family, ext}` (or `null`); pytest `test_shared_cases` writes each
  case to `tmp_path / name` and checks `detect()` the same way.
- [ ] **Step 2: Run** `npx vitest run tests/detect-code.test.ts` (in `web/`) → FAIL;
  `pytest tests/test_detect_code.py::test_shared_cases -v` → PASS (desktop is done).
- [ ] **Step 3: Implement** `detectBytes`, the maps and icon wiring.
- [ ] **Step 4: Write the e2e test** `web/e2e/code-files.spec.ts`: `setInputFiles` with a
  `style.css` buffer → `.queue li` shows it, `.pill[data-target="pack:zip"]` is visible after
  `pick()`'s strip step, "Edit" (exact) is disabled; converting to `pack:zip` gives a result in
  `.results li`.
- [ ] **Step 5: Run** `npx tsc --noEmit`, `npm test`, `npx playwright test e2e/code-files.spec.ts`
  (use `-c playwright.edge.local.config.ts` if Playwright's Chromium is missing) → PASS.
- [ ] **Step 6: Commit** `Accept code files on the website`.

### Task 4: `textfile` — byte-exact read / write (both apps)

**Files:**
- Create: `src/umc/codeedit/textfile.py`, `web/src/codeedit/textfile.ts`
- Create: `tests/fixtures/codeedit_cases.json`
- Test: `tests/test_codeedit_textfile.py`, `web/tests/codeedit-textfile.test.ts`

**Interfaces:**
- Produces (Python; the TS port uses the same names in camelCase and `Uint8Array`):
  ```python
  Encoding = Literal["utf-8", "utf-16-le", "utf-16-be", "windows-1252"]
  Newline = Literal["\r\n", "\n", "\r"]

  @dataclass(frozen=True)
  class TextFile:
      text: str            # line endings normalised to "\n"
      encoding: Encoding
      bom: bool
      newline: Newline     # the most common one in the file ("\n" for none; ties → "\r\n")
      mixed: bool          # more than one kind present
      raw: bytes           # the bytes as read (for the untouched round trip)

  def looks_like_text(head: bytes) -> bool
  def is_binary(head: bytes) -> bool            # NUL in first 8192 bytes, unless UTF-16 BOM
  def decode(data: bytes) -> TextFile           # raises ValueError("binary") if is_binary
  def encode(f: TextFile, text: str, *, encoding: Encoding | None = None,
             newline: Newline | None = None, bom: bool | None = None) -> bytes
  ```
- Rules:
  - BOM: `EF BB BF` → utf-8 + bom; `FF FE` → utf-16-le; `FE FF` → utf-16-be. Else strict
    UTF-8; else windows-1252 using the WHATWG mapping (bytes 0x81, 0x8D, 0x8F, 0x90, 0x9D ↔
    U+0081, U+008D, U+008F, U+0090, U+009D) — write `_cp1252_decode/_encode` helpers in Python
    (`bytes.decode("cp1252")` raises on those five).
  - `encode`: if `text == f.text` and every keyword is `None` or equal to the file's value →
    return `f.raw` unchanged (keeps mixed endings / odd bytes). Otherwise join lines with the
    chosen newline (`f.newline` default), encode, prepend the BOM if `bom` (default `f.bom`).
    Characters windows-1252 can't hold → `ValueError("encoding")` (the editor will ask to save as
    UTF-8 in step 2).
  - `looks_like_text(head)`: not `is_binary`, and either valid UTF-8 after dropping an incomplete
    trailing sequence (≤ 3 bytes), or ≤ 5 % of bytes are control bytes other than `\t \n \r \f`
    (and ESC 0x1B, for ANSI logs).
- Fixture `tests/fixtures/codeedit_cases.json`: `[{id, bytes_b64, encoding, bom, newline, mixed,
  text, looks_like_text}]` — ids at least: `ascii_lf`, `crlf`, `cr_only`, `mixed_newlines`,
  `utf8_bom`, `utf16le_bom`, `utf16be_bom`, `cp1252_euro` (byte 0x80 → "€"),
  `cp1252_undefined_bytes`, `utf8_split_at_8k` (8191 ASCII bytes + `"é"`, `looks_like_text` true),
  `empty` (`encoding "utf-8"`, `newline "\n"`), `binary_nul` (`looks_like_text` false; no decode
  fields), `random_bytes_no_nul` (bytes 1…255 repeated → false).
  Plus `[{id, from, newline, expect_b64}]` "re-save" cases: `crlf` saved with `"\n"`;
  `mixed_newlines` saved with `"\r\n"`; `ascii_lf` with `bom=True`.

- [ ] **Step 1: Write the fixture and failing tests** in both suites: for every case,
  `decode(bytes)` fields equal the fixture's; `encode(decode(b), decode(b).text) == b`;
  `looks_like_text(b[:8192]) == case.looks_like_text`; the re-save cases give `expect_b64`;
  `encode(decode("abc"), "€x", encoding="windows-1252")` works and `"日本"` raises.
- [ ] **Step 2: Run** `pytest tests/test_codeedit_textfile.py -v` and
  `npx vitest run tests/codeedit-textfile.test.ts` → FAIL.
- [ ] **Step 3: Implement** `textfile.py`, then the line-by-line port `textfile.ts`
  (`TextDecoder("utf-8", {fatal: true})`, `TextDecoder("utf-16le"/"utf-16be")`,
  `TextDecoder("windows-1252")`; encoding UTF-16 / 1252 by hand — `TextEncoder` is UTF-8 only).
- [ ] **Step 4: Run** both test files → PASS.
- [ ] **Step 5: Commit** `Read and write text files byte for byte`.

### Task 5: Viewer and Explorer integration (desktop)

**Files:**
- Modify: `src/umc/viewer/files.py:48-63` (`family == MediaFamily.CODE` → `TEXT`),
  `src/umc/tools/shell_integration.py:105-117`
- Modify: the viewer's text view loader (find it in `src/umc/gui/viewer/views.py` — `TextView`)
  so it reads through `codeedit.textfile.decode` (encodings shown right, binary refused with the
  InfoView), if it doesn't already handle BOM / 1252.
- Test: `tests/test_viewer_files.py`, `tests/test_shell_integration.py`

**Interfaces:**
- Consumes: `MediaFamily.CODE`, `CODE_EXTS` (Task 2); `decode` (Task 4).
- Produces in `shell_integration`:
  - `known_extensions() -> list[str]` = today's `convert_extensions()` body (every `*_EXTS`,
    aliases, no dotted ones).
  - `convert_extensions()` = `known_extensions()` minus `CODE_EXTS`.
  - `view_extensions()` = `known_extensions()` filtered by viewer kind (as today).

- [ ] **Step 1: Write failing tests:** `kind_of_path(Path("a.css")) == TEXT`;
  `editor_for` / the viewer's Edit is not offered for `a.css` yet (document editor can't open it);
  `"css" in view_extensions()` and `"css" not in convert_extensions()`; `"bat" not in
  convert_extensions()`; `"png" in convert_extensions()` (unchanged); the fake-registry register
  test writes no `SystemFileAssociations\.css\shell\UniversalEdit.Convert` key but does write
  `.css` under the ProgID's `OpenWithProgids`.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_shell_integration.py tests/test_viewer*.py -v` → PASS.
- [ ] **Step 5: Commit** `Show code files in the viewer and Open with`.

### Task 6: Drop-zone badges CSS and JS (both apps)

**Files:**
- Modify: `src/umc/core/orbit.py:22-53`
- Regenerate: `web/src/ui/orbit.json`, `tests/fixtures/orbit_cases.json`
- Test: existing `tests/test_orbit.py`, `web/tests/orbit.test.ts`

**Interfaces:**
- Consumes: Tabler `code` icon (Task 2).
- Produces: `BADGES` gains `("code", "CSS", "#5c6bc0")` and `("code", "JS", "#e3c341")`
  appended at the end (14 badges); `RINGS` counts become 6 / 5 / 3. Colours are a first pick —
  change them if they read too close to "document" (#4f8ef7) / "archive" (#c9a227) on screen.

- [ ] **Step 1: Change `BADGES` / `RINGS`**, then run `pytest tests/test_orbit.py -v` → the
  web-copy and cases tests FAIL (expected: both files are stale).
- [ ] **Step 2: Regenerate** `python -m umc.core.orbit > web/src/ui/orbit.json`, and rewrite
  `orbit_cases.json` keeping each case's `args` and recomputing `spots` from `orbit.layout(*args)`
  with the same keys the file already stores (a one-off script in the scratchpad; not committed).
- [ ] **Step 3: Run** `pytest tests/test_orbit.py -v` and `npx vitest run tests/orbit.test.ts` → PASS
  (incl. `test_badges_stay_inside_and_clear_of_the_text`).
- [ ] **Step 4: Look at it:** web preview at 1280 px and 375 px wide, desktop `python -m umc` —
  14 badges, no two overlapping on the near side, still calm. Screenshot both for the summary.
- [ ] **Step 5: Commit** `Add CSS and JS badges to the drop-zone animation`.

### Task 7: Docs and ship gate

**Files:**
- Modify: `README.md` ("What it can do": a new "Code and text files" fold-out row — desktop ✅ /
  website ✅: "opened, viewed (desktop), packed into ZIP / 7Z / TAR.GZ; editor coming"),
  `CLAUDE.md` (roadmap 10.17 "Code editor" with step 1 ✅ and steps 2–4 listed; architecture tree
  `codeedit/` + `web/src/codeedit/`; detection note: `.ts` sniffing and the plain-text fallback;
  orbit "12 badges" → "14").

- [ ] **Step 1: Update the docs.**
- [ ] **Step 2: Full gate:** `ruff check . && ruff format --check .`, `pytest`, and in `web/`:
  `npx tsc --noEmit`, `npm test`, `npm run e2e`. All green (or only known flakes, named).
- [ ] **Step 3: Matrix spot check:** `python scripts/convert_matrix.py --out matrix-out` still
  passes (no family regressions).
- [ ] **Step 4: Commit** `Document code file support`, then run the `ship` skill and wait for the
  owner's go-ahead before merging to `main`.
