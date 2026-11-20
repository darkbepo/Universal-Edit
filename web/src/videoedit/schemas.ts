// The inspector's forms: option schemas for every editable thing, ported from the opt() fields in
// src/umc/videoedit/project.py (checked against tests/fixtures/videoedit_cases.json "schemas").
import { opt, type OptionSpec, type Values } from "../core/options";
import {
  ANCHORS,
  FITS,
  ROTATIONS,
  TRANSITIONS,
  find,
  type AudioClip,
  type PictureClip,
  type Project,
  type ProjectSettings,
  type TextItem,
  type Transition,
  type VideoClip,
} from "./project";

export type Target = ProjectSettings | Transition | VideoClip | PictureClip | AudioClip | TextItem;
export type TargetKind = "project" | "transition" | "video" | "picture" | "audio" | "text";

export const TITLES: Record<TargetKind, string> = {
  project: "Project",
  transition: "Transition",
  video: "Video clip",
  picture: "Picture",
  audio: "Music",
  text: "Text",
};

const fade = (name: string, label: string): OptionSpec =>
  opt.float(name, label, 0.0, { min: 0.0, max: 5.0, step: 0.1, decimals: 1, unit: "s" });
const grade = (name: string, label: string): OptionSpec => opt.slider(name, label, 0, -100, 100);
const volume = (): OptionSpec => opt.slider("volume", "Volume", 100, 0, 200, { unit: "%" });
const custom = (v: Values): boolean => v.resolution === "custom";

const SCHEMAS: Record<TargetKind, OptionSpec[]> = {
  project: [
    opt.choice("resolution", "Resolution", "first", [
      ["first", "Match first video"],
      ["720p", "720p (1280×720)"],
      ["1080p", "1080p (1920×1080)"],
      ["4k", "4K (3840×2160)"],
      ["vertical", "Vertical (1080×1920)"],
      ["square", "Square (1080×1080)"],
      ["custom", "Custom"],
    ]),
    opt.int("width", "Width", 1920, { min: 2, max: 7680, unit: "px", showIf: custom }),
    opt.int("height", "Height", 1080, { min: 2, max: 7680, unit: "px", showIf: custom }),
    opt.choice("fps", "Frame rate", "first", [
      ["first", "Match first video"],
      ["24", "24"],
      ["25", "25"],
      ["30", "30"],
      ["50", "50"],
      ["60", "60"],
    ]),
    opt.color("background", "Background", "#000000"),
  ],
  transition: [
    opt.choice("kind", "Transition", "none", TRANSITIONS),
    opt.float("duration", "Duration", 0.5, { min: 0.1, max: 3.0, step: 0.1, decimals: 1, unit: "s" }),
  ],
  video: [
    opt.float("speed", "Speed", 1.0, { min: 0.25, max: 4.0, step: 0.25, decimals: 2, unit: "×" }),
    volume(),
    opt.bool("mute", "Mute", false),
    fade("fade_in", "Fade in"),
    fade("fade_out", "Fade out"),
    opt.choice("fit", "Fit", "letterbox", FITS),
    opt.choice("rotate", "Rotate", 0, ROTATIONS),
    opt.bool("flip_h", "Flip horizontally", false),
    opt.bool("flip_v", "Flip vertically", false),
    opt.slider("crop_left", "Crop left", 0, 0, 100, { unit: "%" }),
    opt.slider("crop_top", "Crop top", 0, 0, 100, { unit: "%" }),
    opt.slider("crop_right", "Crop right", 0, 0, 100, { unit: "%" }),
    opt.slider("crop_bottom", "Crop bottom", 0, 0, 100, { unit: "%" }),
    grade("brightness", "Brightness"),
    grade("contrast", "Contrast"),
    grade("saturation", "Saturation"),
  ],
  picture: [
    opt.float("duration", "Duration", 3.0, { min: 0.1, max: 60.0, step: 0.1, decimals: 1, unit: "s" }),
    opt.choice("fit", "Fit", "letterbox", FITS),
    opt.choice("rotate", "Rotate", 0, ROTATIONS),
    opt.bool("flip_h", "Flip horizontally", false),
    opt.bool("flip_v", "Flip vertically", false),
    fade("fade_in", "Fade in"),
    fade("fade_out", "Fade out"),
    grade("brightness", "Brightness"),
    grade("contrast", "Contrast"),
    grade("saturation", "Saturation"),
  ],
  audio: [volume(), fade("fade_in", "Fade in"), fade("fade_out", "Fade out")],
  text: [
    { name: "text", label: "Text", kind: "textarea", default: "Text" },
    opt.choice("anchor", "Position", "bottom", ANCHORS),
    opt.float("x_offset", "Horizontal offset", 0.0, { min: -100, max: 100, step: 0.5, decimals: 1, unit: "%" }),
    opt.float("y_offset", "Vertical offset", 0.0, { min: -100, max: 100, step: 0.5, decimals: 1, unit: "%" }),
    opt.text("font", "Font", "DejaVu Sans"),
    opt.float("size", "Size", 8.0, { min: 1, max: 50, step: 0.5, decimals: 1, unit: "%" }),
    opt.bool("bold", "Bold", false),
    opt.bool("italic", "Italic", false),
    opt.color("colour", "Colour", "#ffffff"),
    opt.color("outline_colour", "Outline colour", "#000000"),
    opt.float("outline", "Outline", 2.0, { min: 0, max: 10, step: 0.5, decimals: 1, unit: "px" }),
    opt.bool("box", "Background box", false),
    opt.color("box_colour", "Box colour", "#000000"),
    opt.slider("box_opacity", "Box opacity", 50, 0, 100, { unit: "%" }),
    fade("fade_in", "Fade in"),
    fade("fade_out", "Fade out"),
  ],
};

export function schemaFor(kind: TargetKind): OptionSpec[] {
  return SCHEMAS[kind];
}

/** The object the inspector edits for a selection id (inspector.target_of). */
export function targetOf(p: Project, selectionId: string | null): { kind: TargetKind; target: Target } {
  if (selectionId === null) return { kind: "project", target: p.settings };
  if (selectionId.startsWith("tr:")) {
    const item = find(p, selectionId.slice(3)).item;
    if (item.type !== "video" && item.type !== "picture") throw new Error(selectionId);
    return { kind: "transition", target: item.transition_in };
  }
  const item = find(p, selectionId).item;
  return { kind: item.type, target: item };
}

export function valuesOf(kind: TargetKind, target: Target): Values {
  const t = target as unknown as Values;
  return Object.fromEntries(schemaFor(kind).map((s) => [s.name, t[s.name]]));
}

/** Fields of `now` that differ from what was `shown` (unknown keys dropped). */
export function changedFields(shown: Values, now: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(now)) if (Object.hasOwn(shown, k) && v !== shown[k]) out[k] = v;
  return out;
}

export function changeLabel(kind: TargetKind, changes: Record<string, unknown>): string {
  if (kind === "transition") return "Change transition";
  const names = Object.keys(changes);
  if (names.length === 1) {
    const label = schemaFor(kind).find((s) => s.name === names[0])?.label ?? names[0];
    return `Change ${label.toLowerCase()}`;
  }
  return "Change settings";
}
