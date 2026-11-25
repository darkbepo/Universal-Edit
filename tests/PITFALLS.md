# Test pitfalls

Mistakes earlier sessions made while writing tests. Read this before writing or changing
tests (CLAUDE.md points here); add one line when a new kind of mistake costs you time.

- **Hangs = a blocking `exec()` with nobody to click.** pytest-qt closes windows *before*
  fixtures clean up, so an editor left with unsaved edits (a test that failed halfway) asks
  "Save changes?" for ever. `conftest.pytest_runtest_teardown` answers such boxes "Don't save"
  while windows close (`tests/test_teardown_guard.py`) — don't rely on fixture clean-up.
  A menu / dialog a test opens itself must go through a page method the test replaces
  (`exec_menu`, `ask_text`, `confirm`); patching `QMenu.exec` on the class does not reach Qt.
  pytest-timeout (`pyproject.toml`: 300 s, method "thread") stops a stuck test, prints every
  thread's stack (look for your test file's line) and ends the run. Give a genuinely long test
  `@pytest.mark.timeout(seconds)` instead of raising the default; while writing a new GUI
  test, `pytest --timeout 30 …` shows a hang sooner. A venv made before it was added stops with "Missing required
  plugins": run `pip install -e ".[dev]"` again.
- pytest-qt keeps only a **weak** reference to `qtbot.addWidget` windows: hold your own.
- Track settings aren't undo steps: a test that changes them ends with
  `page._tracks_saved_rev = page._tracks_rev` (else the page counts as unsaved).
- `Track` is a dataclass holding numpy arrays: compare tracks with `is`, never `in` / `==`.
- e2e: never assert on timing ("played for ~1 s") — read state (`player.origin`, paused);
  give locators `exact: true` when a label is part of another ("Volume" vs "… volume");
  pick elements by their own class (`canvas.audio-canvas`), not the first tag on the page — a
  later feature can add another canvas or button before it;
  choose formats with `pick()`; never run two Playwright runs at once (they share port 4173).
- Shared-case fixtures (`tests/fixtures/*_cases.json`) are regenerated only on purpose; check
  the old cases' values didn't change when you add new ones.
- A `QTableView` cell editor commits its text *queued* after Enter: `qtbot.waitUntil` the new
  value instead of asserting right after `keyClick` (a direct `model.setData` is immediate).
- e2e under a shell with no `LANG` (some containers): Chromium drops files given to
  `setInputFiles` by a path with non-ASCII letters (test output folders are named after the test,
  e.g. "Find file…"), so the page never sees them. Run with `LANG=C.UTF-8 LC_ALL=C.UTF-8`; CI has it.
