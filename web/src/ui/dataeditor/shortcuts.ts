// The website data editor's keyboard shortcuts (the desktop's gui/dataeditor/shortcuts.py, same
// ids; the browser keeps Ctrl+N, so New is Alt+N). `table` ones are the grid's own keys: they act
// only while the grid has the focus (the edit box and the find bar keep their usual keys).
export { keyName, showKey } from "../subtitleeditor/shortcuts";

export interface Shortcut {
  id: string;
  keys: string[]; // "Mod" = Ctrl (⌘ on a Mac)
  label: string;
  group: string;
  table?: boolean;
}

export const SHORTCUTS: Shortcut[] = [
  { id: "new", keys: ["Alt-n"], label: "New table", group: "Files" },
  { id: "open", keys: ["Mod-o"], label: "Open…", group: "Files" },
  { id: "save", keys: ["Mod-s"], label: "Save", group: "Files" },
  { id: "save_as", keys: ["Mod-Shift-s"], label: "Save as…", group: "Files" },
  { id: "undo", keys: ["Mod-z"], label: "Undo", group: "Editing" },
  { id: "redo", keys: ["Mod-y", "Mod-Shift-z"], label: "Redo", group: "Editing" },
  { id: "add_row", keys: ["Mod-Enter"], label: "Add a row below", group: "Editing" },
  { id: "add_column", keys: ["Mod-Shift-Enter"], label: "Add a column to the right", group: "Editing" },
  { id: "delete_rows", keys: ["Mod--"], label: "Delete the chosen rows", group: "Editing" },
  { id: "edit_box", keys: ["F4"], label: "Edit the cell in the box above the table", group: "Editing" },
  { id: "edit_cell", keys: ["F2", "Enter"], label: "Edit the cell in place", group: "Editing", table: true },
  { id: "clear", keys: ["Delete"], label: "Empty the chosen cells", group: "Editing", table: true },
  { id: "copy", keys: ["Mod-c"], label: "Copy the chosen cells", group: "Editing", table: true },
  { id: "cut", keys: ["Mod-x"], label: "Cut the chosen cells", group: "Editing", table: true },
  { id: "paste", keys: ["Mod-v"], label: "Paste (rows and columns are added when needed)", group: "Editing", table: true },
  { id: "find", keys: ["Mod-f"], label: "Find", group: "Find" },
  { id: "replace", keys: ["Mod-h"], label: "Find and replace", group: "Find" },
  { id: "find_next", keys: ["F3"], label: "Next match", group: "Find" },
  { id: "find_prev", keys: ["Shift-F3"], label: "Previous match", group: "Find" },
  { id: "help", keys: ["F1"], label: "Keyboard shortcuts", group: "View" },
];
