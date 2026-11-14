// The website code editor's keyboard shortcuts (the desktop's gui/codeeditor/shortcuts.py, except
// keys the browser keeps for itself: Ctrl+N / Ctrl+W / Ctrl+Tab → Alt+N / Alt+W / Alt+PageDown).

export interface Shortcut {
  id: string;
  keys: string[]; // "Mod" = Ctrl (⌘ on a Mac)
  label: string;
  group: string;
  bound?: false; // handled by CodeMirror itself: listed in the "?" window only
}

export const SHORTCUTS: Shortcut[] = [
  { id: "new", keys: ["Alt-n"], label: "New file", group: "Files" },
  { id: "open", keys: ["Mod-o"], label: "Open…", group: "Files" },
  { id: "save", keys: ["Mod-s"], label: "Save", group: "Files" },
  { id: "save_as", keys: ["Mod-Shift-s"], label: "Save as…", group: "Files" },
  { id: "close", keys: ["Alt-w"], label: "Close the tab", group: "Files" },
  { id: "next_tab", keys: ["Alt-PageDown"], label: "Next tab", group: "Files" },
  { id: "prev_tab", keys: ["Alt-PageUp"], label: "Previous tab", group: "Files" },
  { id: "find", keys: ["Mod-f"], label: "Find", group: "Find" },
  { id: "replace", keys: ["Mod-h"], label: "Find and replace", group: "Find" },
  { id: "find_next", keys: ["F3"], label: "Next match", group: "Find" },
  { id: "find_prev", keys: ["Shift-F3"], label: "Previous match", group: "Find" },
  { id: "go_to_line", keys: ["Mod-g"], label: "Go to line…", group: "Find" },
  { id: "undo", keys: ["Mod-z"], label: "Undo", group: "Editing", bound: false },
  { id: "redo", keys: ["Mod-y", "Mod-Shift-z"], label: "Redo", group: "Editing", bound: false },
  { id: "comment", keys: ["Mod-/"], label: "Comment / uncomment the lines", group: "Editing", bound: false },
  { id: "indent", keys: ["Tab"], label: "Indent the selected lines", group: "Editing", bound: false },
  { id: "outdent", keys: ["Shift-Tab"], label: "Outdent the selected lines", group: "Editing", bound: false },
  { id: "wrap", keys: ["Alt-z"], label: "Word wrap on / off", group: "View" },
  { id: "zoom_in", keys: ["Mod-=", "Mod-+"], label: "Bigger text", group: "View" },
  { id: "zoom_out", keys: ["Mod--"], label: "Smaller text", group: "View" },
  { id: "zoom_reset", keys: ["Mod-0"], label: "Normal text size", group: "View" },
  { id: "help", keys: ["F1"], label: "Keyboard shortcuts", group: "View" },
];

/** "Mod-Shift-s" style name of a key press. */
export function keyName(e: KeyboardEvent): string {
  const mods = `${e.ctrlKey || e.metaKey ? "Mod-" : ""}${e.altKey ? "Alt-" : ""}${e.shiftKey ? "Shift-" : ""}`;
  let key = e.key;
  if (key.length === 1) key = key.toLowerCase();
  if (e.shiftKey && key === "+") return `${mods.replace("Shift-", "")}+`;
  return mods + key;
}

/** How a key is shown in the "?" window. */
export const showKey = (k: string) => k.replace("Mod-", "Ctrl+").replace(/-(?=.)/g, "+");
