// Video editor project model and timeline maths: a line-by-line port of src/umc/videoedit/project.py.
// Plain data, no DOM. Field names stay snake_case so the project file is the same as the desktop's.

export const TRANSITIONS: [string, string][] = [
  ["none", "None"],
  ["fade", "Crossfade"],
  ["fadeblack", "Fade through black"],
  ["wipeleft", "Wipe left"],
  ["wiperight", "Wipe right"],
  ["slideleft", "Slide left"],
  ["slideright", "Slide right"],
];
export const ANCHORS: [string, string][] = [
  ["top-left", "Top left"],
  ["top", "Top"],
  ["top-right", "Top right"],
  ["left", "Left"],
  ["center", "Centre"],
  ["right", "Right"],
  ["bottom-left", "Bottom left"],
  ["bottom", "Bottom"],
  ["bottom-right", "Bottom right"],
];
export const FITS: [string, string][] = [
  ["letterbox", "Letterbox"],
  ["crop", "Crop to fill"],
  ["stretch", "Stretch"],
];
export const ROTATIONS: [number, string][] = [
  [0, "None"],
  [90, "90°"],
  [180, "180°"],
  [270, "270°"],
];

const PRESET_SIZES: Record<string, [number, number]> = {
  "720p": [1280, 720],
  "1080p": [1920, 1080],
  "4k": [3840, 2160],
  vertical: [1080, 1920],
  square: [1080, 1080],
};

export interface Transition {
  readonly kind: string;
  readonly duration: number;
}
export interface Source {
  readonly id: string;
  readonly path: string;
  readonly kind: "video" | "image" | "audio";
  readonly duration: number;
  readonly width: number;
  readonly height: number;
  readonly fps: number;
  readonly has_audio: boolean;
  readonly size: number;
  readonly relpath?: string | null;
}
export interface VideoClip {
  readonly type: "video";
  readonly id: string;
  readonly source: string;
  readonly in_: number;
  readonly out: number;
  readonly speed: number;
  readonly volume: number;
  readonly mute: boolean;
  readonly fade_in: number;
  readonly fade_out: number;
  readonly fit: string;
  readonly rotate: number;
  readonly flip_h: boolean;
  readonly flip_v: boolean;
  readonly crop_left: number;
  readonly crop_top: number;
  readonly crop_right: number;
  readonly crop_bottom: number;
  readonly brightness: number;
  readonly contrast: number;
  readonly saturation: number;
  readonly transition_in: Transition;
}
export interface PictureClip {
  readonly type: "picture";
  readonly id: string;
  readonly source: string;
  readonly duration: number;
  readonly fit: string;
  readonly rotate: number;
  readonly flip_h: boolean;
  readonly flip_v: boolean;
  readonly fade_in: number;
  readonly fade_out: number;
  readonly brightness: number;
  readonly contrast: number;
  readonly saturation: number;
  readonly transition_in: Transition;
}
export interface AudioClip {
  readonly type: "audio";
  readonly id: string;
  readonly source: string;
  readonly start: number;
  readonly in_: number;
  readonly out: number;
  readonly volume: number;
  readonly fade_in: number;
  readonly fade_out: number;
  readonly loop_until: number;
}
export interface TextItem {
  readonly type: "text";
  readonly id: string;
  readonly text: string;
  readonly start: number;
  readonly end: number;
  readonly anchor: string;
  readonly x_offset: number;
  readonly y_offset: number;
  readonly font: string;
  readonly size: number;
  readonly bold: boolean;
  readonly italic: boolean;
  readonly colour: string;
  readonly outline_colour: string;
  readonly outline: number;
  readonly box: boolean;
  readonly box_colour: string;
  readonly box_opacity: number;
  readonly fade_in: number;
  readonly fade_out: number;
}
export type MainItem = VideoClip | PictureClip;
export interface ProjectSettings {
  readonly resolution: string;
  readonly width: number;
  readonly height: number;
  readonly fps: string;
  readonly background: string;
}
export interface Project {
  readonly settings: ProjectSettings;
  readonly sources: readonly Source[];
  readonly main: readonly MainItem[];
  readonly text_rows: readonly (readonly TextItem[])[];
  readonly music_rows: readonly (readonly AudioClip[])[];
}
export interface Span {
  readonly start: number;
  readonly end: number;
  readonly row_kind: "main" | "text" | "music";
  readonly row: number;
  readonly index: number;
}

export const DEFAULT_SETTINGS: ProjectSettings = {
  resolution: "first",
  width: 1920,
  height: 1080,
  fps: "first",
  background: "#000000",
};
export const NO_TRANSITION: Transition = { kind: "none", duration: 0.5 };

let idMaker: (() => string) | null = null;
/** Tests: deterministic ids. `null` goes back to random ones. */
export function setIdMaker(make: (() => string) | null): void {
  idMaker = make;
}
export function newId(): string {
  if (idMaker) return idMaker();
  const b = crypto.getRandomValues(new Uint8Array(4));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

export function videoClip(id: string, source: string, in_: number, out: number): VideoClip {
  return {
    type: "video", id, source, in_, out, speed: 1.0, volume: 100, mute: false, fade_in: 0, fade_out: 0,
    fit: "letterbox", rotate: 0, flip_h: false, flip_v: false,
    crop_left: 0, crop_top: 0, crop_right: 0, crop_bottom: 0,
    brightness: 0, contrast: 0, saturation: 0, transition_in: NO_TRANSITION,
  };
}
export function pictureClip(id: string, source: string, duration = 3.0): PictureClip {
  return {
    type: "picture", id, source, duration, fit: "letterbox", rotate: 0, flip_h: false, flip_v: false,
    fade_in: 0, fade_out: 0, brightness: 0, contrast: 0, saturation: 0, transition_in: NO_TRANSITION,
  };
}
export function audioClip(id: string, source: string, start: number, in_: number, out: number): AudioClip {
  return { type: "audio", id, source, start, in_, out, volume: 100, fade_in: 0, fade_out: 0, loop_until: 0 };
}
export function textItem(id: string, start: number, end: number): TextItem {
  return {
    type: "text", id, text: "Text", start, end, anchor: "bottom", x_offset: 0, y_offset: 0,
    font: "DejaVu Sans", size: 8.0, bold: false, italic: false, colour: "#ffffff",
    outline_colour: "#000000", outline: 2.0, box: false, box_colour: "#000000", box_opacity: 50,
    fade_in: 0, fade_out: 0,
  };
}

export function newProject(sources: Source[] = []): Project {
  return { settings: DEFAULT_SETTINGS, sources, main: [], text_rows: [[]], music_rows: [[]] };
}

export function sourceOf(p: Project, id: string): Source {
  for (const s of p.sources) if (s.id === id) return s;
  throw new Error(id);
}

export function clipDuration(item: MainItem | AudioClip): number {
  if (item.type === "video") return (item.out - item.in_) / item.speed;
  if (item.type === "picture") return item.duration;
  if (item.loop_until > 0) return item.loop_until - item.start;
  return item.out - item.in_;
}

/** Where every item sits on the timeline, keyed by item id. */
export function layout(p: Project): Map<string, Span> {
  const spans = new Map<string, Span>();
  let prevEnd = 0.0;
  p.main.forEach((item, i) => {
    const tr = item.transition_in;
    const overlap = tr.kind !== "none" ? tr.duration : 0.0;
    const start = i === 0 ? 0.0 : prevEnd - overlap;
    const end = start + clipDuration(item);
    spans.set(item.id, { start, end, row_kind: "main", row: 0, index: i });
    prevEnd = end;
  });
  p.text_rows.forEach((row, r) =>
    row.forEach((t, i) => spans.set(t.id, { start: t.start, end: t.end, row_kind: "text", row: r, index: i })),
  );
  p.music_rows.forEach((row, r) =>
    row.forEach((a, i) =>
      spans.set(a.id, { start: a.start, end: a.start + clipDuration(a), row_kind: "music", row: r, index: i }),
    ),
  );
  return spans;
}

export function length(p: Project): number {
  let m = 0.0;
  for (const s of layout(p).values()) if (s.end > m) m = s.end;
  return m;
}

export function find(
  p: Project,
  id: string,
): { kind: "main" | "text" | "music"; row: number; index: number; item: MainItem | TextItem | AudioClip } {
  for (let i = 0; i < p.main.length; i++) if (p.main[i].id === id) return { kind: "main", row: 0, index: i, item: p.main[i] };
  for (let r = 0; r < p.text_rows.length; r++)
    for (let i = 0; i < p.text_rows[r].length; i++)
      if (p.text_rows[r][i].id === id) return { kind: "text", row: r, index: i, item: p.text_rows[r][i] };
  for (let r = 0; r < p.music_rows.length; r++)
    for (let i = 0; i < p.music_rows[r].length; i++)
      if (p.music_rows[r][i].id === id) return { kind: "music", row: r, index: i, item: p.music_rows[r][i] };
  throw new Error(id);
}

const even = (n: number): number => Math.max(2, n - (n % 2));

function firstVideo(p: Project): Source | null {
  for (const item of p.main) {
    const src = sourceOf(p, item.source);
    if (src.kind === "video") return src;
  }
  return null;
}

export function outputSize(p: Project): [number, number] {
  const res = p.settings.resolution;
  if (res in PRESET_SIZES) return [...PRESET_SIZES[res]];
  if (res === "custom") return [even(p.settings.width), even(p.settings.height)];
  const first = firstVideo(p);
  if (!first) return [1920, 1080];
  return [even(first.width), even(first.height)];
}

export function outputFps(p: Project): number {
  const fps = p.settings.fps;
  if (fps !== "first") return parseFloat(fps);
  const first = firstVideo(p);
  return first ? Number(first.fps.toFixed(3)) : 30.0;
}
