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
  { id: "video", title: "Video editor", blurb: "Trim, join and mix clips", icon: "movie", available: false },
  { id: "audio", title: "Audio editor", blurb: "Cut, fade and clean up sound", icon: "music", available: true },
  { id: "document", title: "Document editor", blurb: "Write and format documents", icon: "document", available: true },
  { id: "code", title: "Code editor", blurb: "Quick edits to code and text files", icon: "code", available: true },
  { id: "model", title: "3D viewer", blurb: "Look at and measure 3D models", icon: "cube", available: true },
  { id: "vector", title: "Vector editor", blurb: "Edit shapes and paths in SVG drawings", icon: "vector", available: false },
  { id: "font", title: "Font editor", blurb: "Change letters, spacing and font names", icon: "font", available: false },
  { id: "subtitle", title: "Subtitle editor", blurb: "Fix subtitle text and timing", icon: "subtitle", available: false },
  { id: "data", title: "Data editor", blurb: "Edit CSV, JSON, XML and spreadsheets", icon: "table", available: false },
];

export const COMING_SOON = "Coming soon";
