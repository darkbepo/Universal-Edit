// The "About this file" sections, built from plain facts: a line-by-line port of the desktop's
// properties/info.py (same labels, same order). Each app reads files with its own engines into
// the same plain objects (see that file for the keys); both test suites check
// tests/fixtures/properties_cases.json. Dates arrive as display text (local time).
import * as fmt from "./format";

export type Row = [string, string];
export interface Section { title: string; rows: Row[] }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Facts = Record<string, any>;

function section(title: string): Section & { add(label: string, value: unknown): void } {
  const s = { title, rows: [] as Row[], add(label: string, value: unknown) {
    if (value === null || value === undefined) return;
    const text = String(value).trim();
    if (text) s.rows.push([label, text]);
  } };
  return s;
}

// FFmpeg's and the browser's (Mediabunny / WebCodecs) codec ids → what people call them
export const CODECS: Record<string, string> = {
  h264: "H.264", avc: "H.264", hevc: "H.265 (HEVC)", vp8: "VP8", vp9: "VP9", av1: "AV1",
  mpeg4: "MPEG-4 Part 2", mpeg2video: "MPEG-2", mpeg1video: "MPEG-1", mjpeg: "Motion JPEG",
  prores: "ProRes", theora: "Theora", wmv3: "Windows Media Video", wmv2: "Windows Media Video",
  flv1: "Flash Video", aac: "AAC", mp3: "MP3", opus: "Opus", vorbis: "Vorbis", flac: "FLAC",
  alac: "ALAC (Apple Lossless)", ac3: "Dolby Digital (AC-3)", eac3: "Dolby Digital Plus (E-AC-3)",
  dts: "DTS", wmav2: "Windows Media Audio", wmav1: "Windows Media Audio", amr_nb: "AMR", amrnb: "AMR",
};

const COLOUR: Record<string, string> = { colour: "Colour", greyscale: "Greyscale", bw: "Black and white", cmyk: "CMYK (for print)" };

const ORIENTATION: Record<number, string> = {
  2: "Mirrored", 3: "Upside down", 4: "Upside down, mirrored", 5: "Turned 90° left, mirrored",
  6: "Turned 90° right", 7: "Turned 90° right, mirrored", 8: "Turned 90° left",
};

const TAGS: [string, string][] = [
  ["title", "Title"], ["artist", "Artist"], ["album", "Album"], ["album_artist", "Album artist"],
  ["track", "Track"], ["disc", "Disc"], ["date", "Year"], ["genre", "Genre"], ["composer", "Composer"],
  ["comment", "Comment"],
];

const int = (v: unknown) => Math.trunc(Number(v) || 0);

export function codecName(codec: string): string {
  const c = (codec || "").toLowerCase();
  if (c.startsWith("pcm")) return "PCM (uncompressed)";
  return CODECS[c] ?? c.toUpperCase();
}

export function fileSection(f: Facts): Section {
  const s = section("File");
  s.add("Name", f.name);
  s.add("Type", f.type);
  if (f.size !== null && f.size !== undefined) s.add("Size", fmt.sizeExact(int(f.size)));
  s.add("Modified", f.modified);
  s.add("Folder", f.folder);
  return s;
}

export function pictureSections(p: Facts): Section[] {
  const pic = section("Picture");
  const w = int(p.width), h = int(p.height);
  if (w && h) {
    pic.add("Dimensions", fmt.pixels(w, h));
    if (w * h >= 100_000) pic.add("Megapixels", fmt.megapixels(w, h));
  }
  if (p.kind in COLOUR) pic.add("Colour", COLOUR[p.kind] + (p.alpha ? ", with transparency" : ""));
  const frames = int(p.frames) || 1;
  if (frames > 1) pic.add("Frames", `${fmt.count(frames)} (animated)`);
  const dpi = p.dpi as number[] | undefined;
  if (dpi && dpi[0] && dpi[1]) {
    const x = Math.round(dpi[0]), y = Math.round(dpi[1]);
    pic.add("Resolution", x === y ? `${x} dpi` : `${x} × ${y} dpi`);
  }
  pic.add("Orientation", ORIENTATION[int(p.orientation) || 1]);

  const cam = section("Camera");
  let make = String(p.make ?? "").trim();
  const model = String(p.model ?? "").trim();
  if (make && model.toLowerCase().startsWith(make.toLowerCase().split(/\s+/)[0])) make = ""; // "Canon" + "Canon EOS R6"
  cam.add("Camera", [make, model].filter(Boolean).join(" "));
  cam.add("Lens", p.lens);
  cam.add("Taken", p.taken);
  if (p.exposure) cam.add("Exposure", fmt.exposure(Number(p.exposure)));
  if (p.fnumber) cam.add("Aperture", fmt.aperture(Number(p.fnumber)));
  if (p.iso) cam.add("ISO", int(p.iso));
  if (p.focal) cam.add("Focal length", fmt.focal(Number(p.focal)));
  if (p.flash !== null && p.flash !== undefined) cam.add("Flash", int(p.flash) & 1 ? "Fired" : "Didn't fire");

  const det = section("Details");
  det.add("Description", p.description);
  det.add("Author", p.artist);
  det.add("Copyright", p.copyright);

  return [pic, cam, det, locationSection(p)];
}

function locationSection(d: Facts): Section {
  const loc = section("Location");
  if (d.lat !== null && d.lat !== undefined && d.lon !== null && d.lon !== undefined) {
    loc.add("Coordinates", fmt.location(Number(d.lat), Number(d.lon)));
    if (d.altitude !== null && d.altitude !== undefined) loc.add("Altitude", `${Math.round(Number(d.altitude))} m`);
  }
  return loc;
}

export function mediaSections(m: Facts): Section[] {
  const out: Section[] = [];
  const duration = Number(m.duration) || 0;
  const size = int(m.size);
  const rate = duration && size ? fmt.bitrate((size * 8) / duration) : null;
  const v = m.video as Facts | undefined;
  const audio = (m.audio ?? []) as Facts[];
  if (v) {
    const vs = section("Video");
    if (duration) vs.add("Length", fmt.duration(duration));
    if (v.width && v.height) vs.add("Dimensions", fmt.pixels(int(v.width), int(v.height)));
    if (v.fps) vs.add("Frame rate", fmt.fps(Number(v.fps)));
    vs.add("Codec", v.codec ? codecName(v.codec) : null);
    const rot = ((int(v.rotation) % 360) + 360) % 360;
    if (rot) vs.add("Rotation", `${rot}°`);
    vs.add("Bit rate", rate);
    vs.add("Recorded", m.recorded);
    out.push(vs);
  }
  audio.forEach((a, i) => {
    const s = section(audio.length === 1 ? "Audio" : `Audio track ${i + 1}`);
    if (!v && i === 0) {
      if (duration) s.add("Length", fmt.duration(duration));
      s.add("Bit rate", rate);
    }
    s.add("Codec", a.codec ? codecName(a.codec) : null);
    if (a.channels) s.add("Channels", fmt.channels(int(a.channels)));
    if (a.sample_rate) s.add("Sample rate", fmt.sampleRate(int(a.sample_rate)));
    const lang = String(a.language ?? "").toLowerCase();
    if (lang && lang !== "und") s.add("Language", lang);
    out.push(s);
  });
  const subs = (m.subtitles ?? []) as Facts[];
  if (subs.length) {
    const s = section("Subtitles");
    s.add("Tracks", subs.length);
    s.add("Languages", subs.map((x) => String(x.language ?? "").toLowerCase()).filter((x) => x && x !== "und").join(", "));
    out.push(s);
  }
  const tags = (m.tags ?? {}) as Facts;
  const ts = section("Tags");
  for (const [key, label] of TAGS) {
    let value = tags[key];
    if (key === "date" && value) {
      const head = String(value).trim().slice(0, 4);
      value = /^\d{4}$/.test(head) ? head : value;
    }
    ts.add(label, value);
  }
  if (m.cover) ts.add("Cover picture", "Yes");
  out.push(ts);
  out.push(locationSection(m));
  return out;
}

export function documentSections(d: Facts): Section[] {
  const s = section("Document");
  if (d.pages) s.add("Pages", fmt.count(int(d.pages)));
  if (d.slides) s.add("Slides", fmt.count(int(d.slides)));
  if (d.words) s.add("Words", fmt.count(int(d.words)));
  const size = d.page_size as number[] | undefined;
  if (size && size[0] && size[1]) s.add("Page size", fmt.paper(Number(size[0]), Number(size[1])));
  s.add("Title", d.title);
  s.add("Subject", d.subject);
  s.add("Author", d.author);
  s.add("Last saved by", d.last_saved_by);
  s.add("Company", d.company);
  s.add("Keywords", d.keywords);
  s.add("Created", d.created);
  s.add("Modified", d.modified);
  s.add("Made with", d.application);
  s.add("Version", d.pdf_version);
  if (d.encrypted) s.add("Password", "Protected");
  return [s];
}

export function fontSections(f: Facts): Section[] {
  const s = section("Font");
  s.add("Family", f.family);
  s.add("Style", f.style);
  let version = String(f.version ?? "").trim();
  if (version.toLowerCase().startsWith("version ")) version = version.slice(8);
  s.add("Version", version);
  s.add("Designer", f.designer);
  s.add("Maker", f.manufacturer);
  s.add("Copyright", f.copyright);
  if (f.glyphs) s.add("Glyphs", fmt.count(int(f.glyphs)));
  if (f.outlines) s.add("Outlines", f.outlines === "ttf" ? "TrueType" : "PostScript (CFF)");
  const axes = (f.axes ?? []) as string[];
  if (axes.length) s.add("Variable", `Yes (${axes.join(", ")})`);
  return [s];
}

/** Every section with at least one row, "File" first. */
export function build(facts: Facts): Section[] {
  const sections: Section[] = [fileSection(facts.file ?? {})];
  if ("picture" in facts) sections.push(...pictureSections(facts.picture));
  if ("media" in facts) sections.push(...mediaSections(facts.media));
  if ("document" in facts) sections.push(...documentSections(facts.document));
  if ("font" in facts) sections.push(...fontSections(facts.font));
  if ("model" in facts) sections.push({ title: "3D model", rows: (facts.model as Row[]).map((r) => [r[0], r[1]]) });
  if (facts.note) sections.push({ title: "Note", rows: [["", facts.note]] });
  return sections.filter((s) => s.rows.length).map((s) => ({ title: s.title, rows: s.rows }));
}
