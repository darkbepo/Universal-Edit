// The editors shown as tiles on the empty start screen (same list in src/umc/core/editors.py;
// tests/test_editor_hub.py checks they match). available false = greyed out, "Coming soon".
import type { IconName } from "../ui/icon_data";

export interface EditorInfo {
  id: string;
  title: string;
  blurb: string;
  icon: IconName; // Tabler icon (ui/icon_data.ts)
  available: boolean;
}

export const EDITORS: EditorInfo[] = [
  { id: "image", title: "Image editor", blurb: "Paint, layers, retouch and effects", icon: "photo", available: true },
  { id: "model", title: "3D viewer", blurb: "Look at and measure 3D models", icon: "cube", available: true },
  { id: "video", title: "Video editor", blurb: "Trim, join and mix clips", icon: "movie", available: false },
  { id: "audio", title: "Audio editor", blurb: "Cut, mix and fade sound", icon: "music", available: false },
  { id: "document", title: "Document editor", blurb: "Write and format documents", icon: "document", available: false },
];

export const COMING_SOON = "Coming soon";
