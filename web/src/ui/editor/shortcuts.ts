// Every editor shortcut in one table (port of src/umc/gui/editor/shortcuts.py): the page matches
// key presses against it and the "?" window lists it. Tool letters come from the tools.
// Differences from the desktop: Chrome reserves Ctrl+Shift+N (new incognito window), so a new
// layer is Ctrl+Alt+N here; ⌘ counts as Ctrl on a Mac.

export interface Shortcut {
  id: string;
  keys: string[]; // "Ctrl+Shift+C"; the first one is shown in the list
  label: string;
  group: string;
}

export const SHORTCUTS: Shortcut[] = [
  { id: "undo", keys: ["Ctrl+Z"], label: "Undo", group: "Edit" },
  { id: "redo", keys: ["Ctrl+Y", "Ctrl+Shift+Z"], label: "Redo", group: "Edit" },
  { id: "copy", keys: ["Ctrl+C"], label: "Copy (selection, or the whole layer)", group: "Edit" },
  { id: "copy_merged", keys: ["Ctrl+Shift+C"], label: "Copy what you see (all layers)", group: "Edit" },
  { id: "cut", keys: ["Ctrl+X"], label: "Cut", group: "Edit" },
  { id: "paste", keys: ["Ctrl+V"], label: "Paste as a new layer", group: "Edit" },
  { id: "delete", keys: ["Delete", "Backspace"], label: "Clear the selection · in the layers list: delete layers", group: "Edit" },
  { id: "save", keys: ["Ctrl+S"], label: "Save", group: "Edit" },
  { id: "save_as", keys: ["Ctrl+Shift+S"], label: "Save as…", group: "Edit" },
  { id: "select_all", keys: ["Ctrl+A"], label: "Select all", group: "Selection" },
  { id: "deselect", keys: ["Ctrl+D"], label: "Deselect", group: "Selection" },
  // Ctrl+Shift+I opens the browser's developer tools and Shift+F6 moves focus between its panes,
  // so these use Photoshop's other keys here
  { id: "invert_selection", keys: ["Shift+F7", "Ctrl+Alt+I"], label: "Invert the selection", group: "Selection" },
  { id: "feather", keys: ["Ctrl+Alt+D"], label: "Feather (soften) the selection…", group: "Selection" },
  { id: "apply", keys: ["Enter"], label: "Apply the crop · finish a polygon", group: "Selection" },
  { id: "new_layer", keys: ["Ctrl+Alt+N"], label: "New layer", group: "Layers" },
  { id: "duplicate", keys: ["Ctrl+J"], label: "Duplicate layers", group: "Layers" },
  { id: "merge", keys: ["Ctrl+E"], label: "Merge down / merge the selected layers", group: "Layers" },
  { id: "layer_up", keys: ["Ctrl+]"], label: "Move layers up", group: "Layers" },
  { id: "layer_down", keys: ["Ctrl+["], label: "Move layers down", group: "Layers" },
  { id: "layer_top", keys: ["Ctrl+Shift+]"], label: "Move layers to the top", group: "Layers" },
  { id: "layer_bottom", keys: ["Ctrl+Shift+["], label: "Move layers to the bottom", group: "Layers" },
  { id: "select_all_layers", keys: ["Ctrl+Alt+A"], label: "Select all layers", group: "Layers" },
  { id: "zoom_in", keys: ["Ctrl++", "Ctrl+="], label: "Zoom in", group: "View" },
  { id: "zoom_out", keys: ["Ctrl+-"], label: "Zoom out", group: "View" },
  { id: "fit", keys: ["Ctrl+0"], label: "Fit to window", group: "View" },
  { id: "actual", keys: ["Ctrl+1"], label: "Actual pixels (100 %)", group: "View" },
  { id: "help", keys: ["?", "F1"], label: "Show this list", group: "View" },
  { id: "swap_colours", keys: ["X"], label: "Swap colours", group: "Tools" },
  { id: "smaller", keys: ["["], label: "Smaller brush", group: "Tools" },
  { id: "bigger", keys: ["]"], label: "Bigger brush", group: "Tools" },
];

/** Shown in the list but handled elsewhere (arrow keys, mouse). */
export const INFO: [string, string, string][] = [
  ["Shift / Alt + select", "Add to / take from the selection (both: overlap)", "Selection"],
  ["Double-click", "Finish a polygon lasso", "Selection"],
  ["Arrow keys", "Move tool: nudge 1 px (Shift: 10 px)", "Tools"],
  ["Shift + drag", "Move tool: keep to one direction · shapes: keep square", "Tools"],
  ["Esc", "Cancel the current drag / crop", "Tools"],
  ["Space + drag", "Pan (also the middle mouse button)", "View"],
  ["Mouse wheel", "Zoom around the cursor", "View"],
  ["⌘ on a Mac", "Works like Ctrl. ⌘+Shift+[ / ] switch browser tabs there: use the layer menu's Move to top / bottom", "Layers"],
];

export const GROUPS = ["Edit", "Selection", "Layers", "View", "Tools"];

interface Spec {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  key: string; // lower case
}

const CODES: Record<string, string> = { "[": "BracketLeft", "]": "BracketRight", "=": "Equal", "+": "Equal", "-": "Minus" };

export function parseKey(text: string): Spec {
  const parts = text === "Ctrl++" ? ["Ctrl", "+"] : text.split("+");
  const key = parts[parts.length - 1].toLowerCase();
  const mods = new Set(parts.slice(0, -1).map((m) => m.toLowerCase()));
  return { ctrl: mods.has("ctrl"), shift: mods.has("shift"), alt: mods.has("alt"), key };
}

/** Does the key press match `text`? Letters and digits match by the character typed, so every
 *  keyboard layout gets its own Ctrl+Z / Ctrl+Y; only when a modifier turned the key into
 *  another character (⌥+A types "å" on a Mac) do they fall back to the physical key. Symbols
 *  match by key or physical key, so they work whatever Shift is needed to type them. */
export function matches(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">, text: string): boolean {
  const s = parseKey(text);
  if ((e.ctrlKey || e.metaKey) !== s.ctrl || e.altKey !== s.alt) return false;
  const key = e.key.toLowerCase();
  const typed = /^[a-z0-9]$/.test(key); // a plain letter / digit: trust it, never the position
  if (/^[a-z]$/.test(s.key)) return e.shiftKey === s.shift && (typed ? key === s.key : e.code === `Key${s.key.toUpperCase()}`);
  if (/^[0-9]$/.test(s.key)) return e.shiftKey === s.shift && (typed ? key === s.key : e.code === `Digit${s.key}`);
  if (s.key.length > 1) return e.shiftKey === s.shift && key === s.key; // Enter, Delete, F1…
  if (s.key === "?") return key === "?";
  if (s.key === "+") return key === "+" || (e.code === "Equal" && e.shiftKey) || e.code === "NumpadAdd";
  if (s.key === "-" && e.code === "NumpadSubtract") return e.shiftKey === s.shift;
  // [ ] = - : with Shift they type { } + _ on many layouts, so also accept the physical key
  if (e.shiftKey !== s.shift) return false;
  return key === s.key || e.code === CODES[s.key];
}

/** The shortcut for a key press; entries needing Shift are tried first (Ctrl+Shift+] vs Ctrl+]). */
export function shortcutFor(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">): Shortcut | null {
  const all = SHORTCUTS.flatMap((sc) => sc.keys.map((k) => [sc, k] as const));
  all.sort((a, b) => Number(parseKey(b[1]).shift) - Number(parseKey(a[1]).shift));
  return all.find(([, k]) => matches(e, k))?.[0] ?? null;
}

/** (keys, what it does) per group, for the "?" window. */
export function shortcutRows(tools: { shortcut: string; label: string }[]): Map<string, [string, string][]> {
  const rows = new Map<string, [string, string][]>(GROUPS.map((g) => [g, []]));
  for (const sc of SHORTCUTS) rows.get(sc.group)?.push([sc.keys[0], sc.label]);
  for (const [letter, group] of toolGroups(tools)) {
    const label = group.map((t) => t.label).join(" / ");
    rows.get("Tools")?.push([letter, group.length > 1 ? `${label} (press again to switch)` : label]);
  }
  for (const [keys, label, group] of INFO) rows.get(group)?.push([keys, label]);
  return rows;
}

/** Tools by shortcut letter, in tool-bar order (a letter shared by several cycles them). */
export function toolGroups<T extends { shortcut: string }>(tools: T[]): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const t of tools) if (t.shortcut) groups.set(t.shortcut, [...(groups.get(t.shortcut) ?? []), t]);
  return groups;
}
