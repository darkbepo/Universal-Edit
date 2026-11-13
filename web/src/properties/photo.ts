// Changing a photo's details — a line-by-line port of the desktop's properties/photo.py (same
// fields, same tags, byte-identical files: tests/fixtures/photo_edit_cases.json). JPEG, PNG and
// WebP only; the picture data is never decoded or re-saved.
import * as containers from "./containers";
import * as exif from "./exif";
import * as xmp from "./xmp";

const { TAG } = exif;
export const WRITABLE_EXTS = new Set(["jpg", "png", "webp"]);
export const FIELDS = ["title", "comment", "author", "copyright", "tags", "taken"] as const;
export type Field = (typeof FIELDS)[number];
export const ROTATIONS: [number, string][] = [[1, "Upright"], [6, "Turned 90° right"], [3, "Upside down"], [8, "Turned 90° left"]];

const XMP_PROPS: Record<string, string[]> = {
  title: ["dc:title", "dc:description"],
  comment: ["exif:UserComment"],
  author: ["dc:creator", "tiff:Artist"],
  copyright: ["dc:rights"],
  tags: ["dc:subject"],
  taken: ["exif:DateTimeOriginal", "exif:DateTimeDigitized", "xmp:CreateDate", "photoshop:DateCreated"],
  orientation: ["tiff:Orientation"],
  location: ["exif:GPS*"],
};

export class PhotoError extends Error {}

export interface PhotoDetails {
  title: string; comment: string; author: string; copyright: string; tags: string;
  taken: string; orientation: number; has_location: boolean;
}

export interface PhotoEdit {
  values?: Partial<Record<Field, string>>;
  orientation?: number | null;
  remove_location?: boolean;
  remove_all?: boolean;
}

export const canChange = (ext: string) => WRITABLE_EXTS.has(ext.toLowerCase());

export function tagsText(text: string): string {
  return text.split(/[;,]/).map((p) => p.trim()).filter(Boolean).join("; ");
}

/** "2026-11-12 14:03" (or with seconds) → "2026:11:12 14:03:00"; throws PhotoError. */
export function exifDate(display: string): string {
  const m = /^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$/.exec(display);
  if (!m) throw new PhotoError("Write the date as year-month-day and time, like 2026-11-12 14:03.");
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
  const h = Number(m[4] ?? 0), mi = Number(m[5] ?? 0), s = Number(m[6] ?? 0);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (!(mo >= 1 && mo <= 12 && d >= 1 && d <= days[mo - 1] && h < 24 && mi < 60 && s < 60)) {
    throw new PhotoError("That date doesn't exist. Write it like 2026-11-12 14:03.");
  }
  const two = (n: number) => String(n).padStart(2, "0");
  return `${String(y).padStart(4, "0")}:${two(mo)}:${two(d)} ${two(h)}:${two(mi)}:${two(s)}`;
}

export function displayDate(text: string | null): string {
  const m = /^(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d)/.exec((text ?? "").trim());
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : "";
}

function parse(raw: Uint8Array | null): exif.Tiff | null {
  if (raw === null) return null;
  try {
    return new exif.Tiff(raw);
  } catch (e) {
    if (e instanceof exif.ExifError) return null;
    throw e;
  }
}

/** What the form shows. Missing EXIF values fall back to the XMP packet's. */
export function readDetails(data: Uint8Array): PhotoDetails {
  const d: PhotoDetails = { title: "", comment: "", author: "", copyright: "", tags: "", taken: "", orientation: 1, has_location: false };
  const t = parse(containers.getExif(data));
  const packet = containers.getXmp(data) ?? "";
  if (t) {
    d.title = t.text(t.ifd0, TAG.xp_title) ?? t.text(t.ifd0, TAG.description) ?? "";
    d.comment = t.text(t.ifd0, TAG.xp_comment) ?? "";
    d.author = t.text(t.ifd0, TAG.xp_author) ?? t.text(t.ifd0, TAG.artist) ?? "";
    d.copyright = t.text(t.ifd0, TAG.copyright) ?? "";
    d.tags = tagsText(t.text(t.ifd0, TAG.xp_keywords) ?? "");
    d.taken = displayDate(t.text(t.exif, TAG.taken));
    d.orientation = t.short(t.ifd0, TAG.orientation) || 1;
    d.has_location = t.gps !== null;
  }
  if (packet) {
    d.title = d.title || xmp.get(packet, "dc:title") || xmp.get(packet, "dc:description") || "";
    d.author = d.author || xmp.get(packet, "dc:creator") || "";
    d.copyright = d.copyright || xmp.get(packet, "dc:rights") || "";
    d.tags = d.tags || tagsText(xmp.get(packet, "dc:subject") ?? "");
    d.has_location = d.has_location || /exif:GPSL(atitude|ongitude)/.test(packet);
  }
  return d;
}

/** The file with ``edit`` applied (throws PhotoError in plain language). */
export function apply(data: Uint8Array, edit: PhotoEdit): Uint8Array {
  if (containers.kindOf(data) === null) throw new PhotoError("Only JPG, PNG and WebP pictures can be changed here.");
  try {
    return applyEdit(data, edit);
  } catch (e) {
    if (e instanceof exif.ExifError || e instanceof containers.ContainerError) {
      throw new PhotoError(`The picture's details couldn't be changed (${e.message}).`);
    }
    throw e;
  }
}

function applyEdit(data: Uint8Array, edit0: PhotoEdit): Uint8Array {
  let edit = edit0;
  let raw = containers.getExif(data);
  if (raw !== null && parse(raw) === null) raw = null; // damaged: start a fresh block
  if (edit.remove_all) {
    const old = parse(containers.getExif(data));
    const orientation = old ? old.short(old.ifd0, TAG.orientation) || 1 : 1;
    if (orientation !== 1 && (edit.orientation === undefined || edit.orientation === null)) {
      edit = { ...edit, orientation };
    }
  }
  if (raw === null || edit.remove_all) raw = exif.emptyTiff();
  const t = new exif.Tiff(raw);
  const ifd0 = new Map<number, exif.Change>();
  const sub = new Map<number, exif.Change>();
  const textPair = (tagAscii: number | null, tagXp: number | null, value: string) => {
    if (tagAscii !== null) ifd0.set(tagAscii, value ? exif.asciiValue(value) : null);
    if (tagXp !== null) ifd0.set(tagXp, value ? exif.xpValue(value) : null);
  };
  const values = edit.values ?? {};
  const v: Partial<Record<Field, string>> = {};
  for (const k of FIELDS) if (values[k] !== undefined) v[k] = (values[k] as string).trim();
  if (v.title !== undefined) textPair(TAG.description, TAG.xp_title, v.title);
  if (v.comment !== undefined) textPair(null, TAG.xp_comment, v.comment);
  if (v.author !== undefined) textPair(TAG.artist, TAG.xp_author, v.author);
  if (v.copyright !== undefined) textPair(TAG.copyright, null, v.copyright);
  if (v.tags !== undefined) textPair(null, TAG.xp_keywords, tagsText(v.tags));
  if (v.taken !== undefined) {
    const stamp = v.taken ? exifDate(v.taken) : "";
    sub.set(TAG.taken, stamp ? exif.asciiValue(stamp) : null);
    sub.set(TAG.digitized, stamp ? exif.asciiValue(stamp) : null);
  }
  if (edit.orientation !== undefined && edit.orientation !== null) {
    if (![1, 2, 3, 4, 5, 6, 7, 8].includes(edit.orientation)) throw new PhotoError("That rotation isn't possible.");
    ifd0.set(TAG.orientation, exif.shortValue(edit.orientation, t.le));
  }
  const newExif = exif.rewrite(raw, ifd0, sub, Boolean(edit.remove_location || edit.remove_all));
  const packet = containers.getXmp(data);
  let newXmp: containers.XmpArg = containers.KEEP;
  if (edit.remove_all) {
    newXmp = null;
  } else if (packet !== null) {
    const props = (Object.keys(v) as Field[]).flatMap((k) => XMP_PROPS[k]);
    if (edit.orientation !== undefined && edit.orientation !== null) props.push(...XMP_PROPS.orientation);
    if (edit.remove_location) props.push(...XMP_PROPS.location);
    if (props.length) newXmp = xmp.remove(packet, props);
  }
  return containers.put(data, shrink(newExif), newXmp, Boolean(edit.remove_all));
}

function shrink(tiff: Uint8Array): Uint8Array | null {
  const t = new exif.Tiff(tiff);
  return !t.ifd0.entries.length && t.ifd0.next === 0 ? null : tiff;
}
