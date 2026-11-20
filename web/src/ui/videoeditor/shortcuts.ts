// The video editor's keys in one table (port of editor_page._install_shortcuts). DOM-free: the
// page passes a key event's fields to `actionFor`; ⌘ counts as Ctrl on a Mac.

export type ActionId =
  | "play" | "split" | "delete" | "prev-frame" | "next-frame" | "back-second" | "forward-second"
  | "home" | "end" | "undo" | "redo" | "save" | "save-as" | "export" | "zoom-in" | "zoom-out" | "loop";

export const SHORTCUTS: { id: ActionId; keys: string[]; label: string }[] = [
  { id: "play", keys: ["Space"], label: "Play / pause" },
  { id: "split", keys: ["S"], label: "Split at the playhead" },
  { id: "delete", keys: ["Delete", "Backspace"], label: "Delete the selection" },
  { id: "prev-frame", keys: ["Left"], label: "Back one frame" },
  { id: "next-frame", keys: ["Right"], label: "Forward one frame" },
  { id: "back-second", keys: ["Shift+Left"], label: "Back one second" },
  { id: "forward-second", keys: ["Shift+Right"], label: "Forward one second" },
  { id: "home", keys: ["Home"], label: "Go to the start" },
  { id: "end", keys: ["End"], label: "Go to the end" },
  { id: "undo", keys: ["Ctrl+Z"], label: "Undo" },
  { id: "redo", keys: ["Ctrl+Y", "Ctrl+Shift+Z"], label: "Redo" },
  { id: "save", keys: ["Ctrl+S"], label: "Save the project" },
  { id: "save-as", keys: ["Ctrl+Shift+S"], label: "Save the project as…" },
  { id: "export", keys: ["Ctrl+E"], label: "Export the video…" },
  { id: "zoom-in", keys: ["+", "="], label: "Zoom the timeline in" },
  { id: "zoom-out", keys: ["-"], label: "Zoom the timeline out" },
  { id: "loop", keys: ["L"], label: "Loop playback" },
];

const NAMES: Record<string, string> = { " ": "Space", ArrowLeft: "Left", ArrowRight: "Right" };

/** The table's spelling of a key press, e.g. "Ctrl+Shift+Z". */
function canonical(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }): string {
  const isLetter = e.key.length === 1 && e.key.toLowerCase() !== e.key.toUpperCase();
  const key = NAMES[e.key] ?? (e.key.length === 1 ? e.key.toUpperCase() : e.key);
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  // Shift changes the character itself for symbols ("=" becomes "+"), so it only counts for
  // letters and named keys
  if (e.shiftKey && (isLetter || e.key.length > 1)) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

const BY_KEY = new Map<string, ActionId>();
for (const s of SHORTCUTS) for (const k of s.keys) BY_KEY.set(k, s.id);

export function actionFor(
  e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean },
  inField: boolean,
): ActionId | null {
  const id = BY_KEY.get(canonical(e)) ?? null;
  if (inField && id !== "save" && id !== "save-as") return null;
  return id;
}
