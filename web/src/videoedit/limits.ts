// Every browser limit of the website video editor, in one place, with the plain messages the page
// shows instead of failing silently. The desktop app has none of these. Pure maths, no DOM.

export { MAX_LENGTH, MAX_ITEMS } from "./edits";

const GB = 1024 ** 3;
const DEFAULTS = { pictureSide: 16000, copyBytes: GB, downloadBytes: 2 * GB };

/** What the browser can take: pictures' longer side, a file we must copy into memory, a download export. */
export const LIMITS: { pictureSide: number; copyBytes: number; downloadBytes: number } = { ...DEFAULTS };

/** e2e lowers the limits so they can be reached with small files; null restores the defaults. */
export function setLimitsForTests(partial: Partial<typeof LIMITS> | null): void {
  Object.assign(LIMITS, DEFAULTS, partial ?? {});
}

/** Types the browser reads straight from the file, without copying it into memory. */
export const DIRECT_VIDEO: ReadonlySet<string> = new Set(["mp4", "mov", "mkv", "webm", "m4v"]);
export const DIRECT_AUDIO: ReadonlySet<string> = new Set(["mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac"]);
export const DIRECT_PICTURE: ReadonlySet<string> = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp", "avif"]);

const direct = (ext: string): boolean => {
  const e = ext.toLowerCase().replace(/^\./, "");
  return DIRECT_VIDEO.has(e) || DIRECT_AUDIO.has(e) || DIRECT_PICTURE.has(e);
};

/** A copy in memory is needed when the type isn't a direct one, or the browser failed to decode it. */
export function needsCopy(ext: string, decodes: boolean): boolean {
  return !direct(ext) || !decodes;
}

/** Locale-independent: "1 GB", "0.5 GB", "2.5 GB". */
const formatGb = (bytes: number): string => `${(Math.round((bytes / GB) * 10) / 10).toString()} GB`;
/** Locale-independent thousands separator: "16,000". */
const formatCount = (n: number): string => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

export function sourceProblem(f: { name: string; size: number; copy: boolean; width?: number; height?: number }): string | null {
  if (f.copy && f.size > LIMITS.copyBytes) {
    return `${f.name} is too big for the browser (over ${formatGb(LIMITS.copyBytes)} for this kind of file) — use the desktop app.`;
  }
  if (Math.max(f.width ?? 0, f.height ?? 0) > LIMITS.pictureSide) {
    return `${f.name} is too big for the browser (over ${formatCount(LIMITS.pictureSide)} pixels on a side) — use the desktop app.`;
  }
  return null;
}

export type Quality = "very-high" | "high" | "medium" | "low";
const BITS_PER_PIXEL: Record<Quality, number> = { "very-high": 0.2, high: 0.1, medium: 0.05, low: 0.025 };

/** About how many bytes an export will be (container overhead included). */
export function estimateBytes(o: {
  seconds: number; width: number; height: number; fps: number;
  quality: Quality | null; videoBitrate?: number; audioBitrate: number; audio: boolean;
}): number {
  const video = o.videoBitrate !== undefined
    ? o.videoBitrate * o.seconds
    : BITS_PER_PIXEL[o.quality ?? "medium"] * o.width * o.height * o.fps * o.seconds;
  const audio = o.audio ? o.audioBitrate * o.seconds : 0;
  return ((video + audio) / 8) * 1.02;
}

/** `streams` = saving straight to disk is available, so there is no memory limit. */
export function exportProblem(estimated: number, streams: boolean): string | null {
  if (streams || estimated <= LIMITS.downloadBytes) return null;
  return `This video would be about ${formatGb(estimated)} — too big for this browser (${formatGb(LIMITS.downloadBytes)} without saving straight to disk). Use Chrome or Edge, a lower quality, or the desktop app.`;
}

export const LIMITS_NOTE =
  "Browser limits: files the browser can't read directly must be under 1 GB, pictures under 16,000 pixels a side, and without Chrome or Edge an export must stay under 2 GB. The desktop app has none of these.";
