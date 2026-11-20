// The website subtitle editor's keyboard shortcuts (the desktop's gui/subtitleeditor/shortcuts.py,
// same ids; the browser keeps Ctrl+N, so New is Alt+N).

export interface Shortcut {
  id: string;
  keys: string[]; // "Mod" = Ctrl (⌘ on a Mac)
  label: string;
  group: string;
}

export const SHORTCUTS: Shortcut[] = [
  { id: "new", keys: ["Alt-n"], label: "New subtitles", group: "Files" },
  { id: "open", keys: ["Mod-o"], label: "Open…", group: "Files" },
  { id: "save", keys: ["Mod-s"], label: "Save", group: "Files" },
  { id: "save_as", keys: ["Mod-Shift-s"], label: "Save as…", group: "Files" },
  { id: "undo", keys: ["Mod-z"], label: "Undo", group: "Editing" },
  { id: "redo", keys: ["Mod-y", "Mod-Shift-z"], label: "Redo", group: "Editing" },
  { id: "add", keys: ["Mod-Enter"], label: "Add a subtitle after this one", group: "Editing" },
  { id: "delete", keys: ["Alt-Delete"], label: "Delete the chosen subtitles", group: "Editing" },
  { id: "split", keys: ["Alt-s"], label: "Split at the cursor (or in the middle)", group: "Editing" },
  { id: "join", keys: ["Alt-j"], label: "Join the chosen subtitles (or with the next)", group: "Editing" },
  { id: "prev", keys: ["Alt-ArrowUp"], label: "Previous subtitle", group: "Editing" },
  { id: "next", keys: ["Alt-ArrowDown"], label: "Next subtitle", group: "Editing" },
  { id: "next_problem", keys: ["F8"], label: "Next problem", group: "Editing" },
  { id: "find", keys: ["Mod-f"], label: "Find", group: "Find" },
  { id: "replace", keys: ["Mod-h"], label: "Find and replace", group: "Find" },
  { id: "find_next", keys: ["F3"], label: "Next match", group: "Find" },
  { id: "find_prev", keys: ["Shift-F3"], label: "Previous match", group: "Find" },
  { id: "play", keys: ["Mod- "], label: "Play / pause the video", group: "Video" },
  { id: "set_start", keys: ["Mod-["], label: "Start = the video's position", group: "Video" },
  { id: "set_end", keys: ["Mod-]"], label: "End = the video's position", group: "Video" },
  { id: "help", keys: ["F1"], label: "Keyboard shortcuts", group: "View" },
];

/** "Mod-Shift-s" style name of a key press. */
export function keyName(e: KeyboardEvent): string {
  const mods = `${e.ctrlKey || e.metaKey ? "Mod-" : ""}${e.altKey ? "Alt-" : ""}${e.shiftKey ? "Shift-" : ""}`;
  let key = e.key;
  if (e.altKey && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3); // Alt+S on a Mac types "ß"
  if (key.length === 1) key = key.toLowerCase();
  return mods + key;
}

/** How a key is shown in the "?" window. */
export const showKey = (k: string) => k.replace("Mod- ", "Ctrl+Space").replace("Mod-", "Ctrl+").replace("ArrowUp", "↑").replace("ArrowDown", "↓").replace(/-(?=.)/g, "+");
