// The website vector editor's keyboard shortcuts (the desktop's gui/vectoreditor/shortcuts.py,
// same ids; the browser keeps Ctrl+N / Ctrl+E, so New is Alt+N and Export Alt+E). `canvas` ones
// act only while the drawing has the focus (the panel boxes keep their usual keys).
export { keyName, showKey } from "../subtitleeditor/shortcuts";

export interface Shortcut {
  id: string;
  keys: string[]; // "Mod" = Ctrl (⌘ on a Mac)
  label: string;
  group: string;
  canvas?: boolean;
}

export const SHORTCUTS: Shortcut[] = [
  { id: "new", keys: ["Alt-n"], label: "New drawing", group: "Files" },
  { id: "open", keys: ["Mod-o"], label: "Open…", group: "Files" },
  { id: "save", keys: ["Mod-s"], label: "Save", group: "Files" },
  { id: "save_as", keys: ["Mod-Shift-s"], label: "Save as…", group: "Files" },
  { id: "export", keys: ["Alt-e"], label: "Export as PNG, JPG …", group: "Files" },
  { id: "undo", keys: ["Mod-z"], label: "Undo", group: "Editing" },
  { id: "redo", keys: ["Mod-y", "Mod-Shift-z"], label: "Redo", group: "Editing" },
  { id: "copy", keys: ["Mod-c"], label: "Copy", group: "Editing", canvas: true },
  { id: "cut", keys: ["Mod-x"], label: "Cut", group: "Editing", canvas: true },
  { id: "paste", keys: ["Mod-v"], label: "Paste", group: "Editing", canvas: true },
  { id: "duplicate", keys: ["Mod-d"], label: "Duplicate", group: "Editing", canvas: true },
  { id: "delete", keys: ["Delete", "Backspace"], label: "Delete (a point with the Node tool)", group: "Editing", canvas: true },
  { id: "select_all", keys: ["Mod-a"], label: "Select everything", group: "Editing", canvas: true },
  { id: "deselect", keys: ["Escape"], label: "Stop drawing / select nothing", group: "Editing", canvas: true },
  { id: "group", keys: ["Mod-g"], label: "Group", group: "Arrange", canvas: true },
  { id: "ungroup", keys: ["Mod-Shift-g"], label: "Ungroup", group: "Arrange", canvas: true },
  { id: "forward", keys: ["PageUp"], label: "Bring forward", group: "Arrange", canvas: true },
  { id: "backward", keys: ["PageDown"], label: "Send backward", group: "Arrange", canvas: true },
  { id: "front", keys: ["Home"], label: "Bring to front", group: "Arrange", canvas: true },
  { id: "back", keys: ["End"], label: "Send to back", group: "Arrange", canvas: true },
  { id: "to_path", keys: ["Mod-Shift-c"], label: "Turn into a path", group: "Arrange", canvas: true },
  { id: "nudge_left", keys: ["ArrowLeft", "Shift-ArrowLeft"], label: "Move left (Shift: 10)", group: "Arrange", canvas: true },
  { id: "nudge_right", keys: ["ArrowRight", "Shift-ArrowRight"], label: "Move right (Shift: 10)", group: "Arrange", canvas: true },
  { id: "nudge_up", keys: ["ArrowUp", "Shift-ArrowUp"], label: "Move up (Shift: 10)", group: "Arrange", canvas: true },
  { id: "nudge_down", keys: ["ArrowDown", "Shift-ArrowDown"], label: "Move down (Shift: 10)", group: "Arrange", canvas: true },
  { id: "tool_select", keys: ["v"], label: "Select tool", group: "Tools", canvas: true },
  { id: "tool_node", keys: ["a"], label: "Node tool (move a path's points)", group: "Tools", canvas: true },
  { id: "tool_rect", keys: ["r"], label: "Rectangle", group: "Tools", canvas: true },
  { id: "tool_ellipse", keys: ["e"], label: "Ellipse", group: "Tools", canvas: true },
  { id: "tool_line", keys: ["l"], label: "Line", group: "Tools", canvas: true },
  { id: "tool_pen", keys: ["p"], label: "Pen (click = corner, drag = curve, Enter ends)", group: "Tools", canvas: true },
  { id: "tool_text", keys: ["t"], label: "Text", group: "Tools", canvas: true },
  { id: "finish", keys: ["Enter"], label: "Finish the pen path", group: "Tools", canvas: true },
  { id: "fit", keys: ["Mod-0"], label: "Fit the page in the window", group: "View" },
  { id: "actual", keys: ["Alt-0"], label: "Actual size (the browser keeps Ctrl+1)", group: "View" },
  { id: "zoom_in", keys: ["Mod-=", "Mod-+"], label: "Zoom in", group: "View" },
  { id: "zoom_out", keys: ["Mod--"], label: "Zoom out", group: "View" },
  { id: "help", keys: ["F1"], label: "Keyboard shortcuts", group: "Help" },
];
