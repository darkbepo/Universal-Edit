// Hidden converter for Properties mode: one file → "facts.json" (the plain facts the page turns
// into "About this file" sections, properties/info.ts). Never offered on the convert page.
// Engines: ImageMagick (pictures + EXIF), Mediabunny (video / music), MuPDF (PDF), fflate (Office /
// ODF), our own font reader, the 3D viewer's reader. What can't be read becomes a note.
import { ConversionError, type Converter, type InputFile } from "../core/types";
import type { Facts } from "../properties/info";
import { BROWSER_NOTE, clean, dms, exifDate, fontFacts, iso6709, NOTE, ODF_EXTS, OFFICE_EXTS, odfFacts, officeFacts, pdfDate, rational } from "../properties/read";

export const PROPERTIES_TARGET = { id: "properties", label: "Properties", ext: "json", group: "", converterId: "properties-info" };

async function picture(f: InputFile): Promise<Facts> {
  const { getMagick, readFrames } = await import("../engines/magick");
  const M = await getMagick();
  const bytes = new Uint8Array(await f.file.arrayBuffer());
  return readFrames(M, bytes, f.ext, (imgs) => {
    const img = imgs[0];
    const attr = (name: string) => {
      const v = img.getAttribute(`exif:${name}`);
      return v === null ? undefined : v.replace(/\0/g, "").trim() || undefined;
    };
    const p: Facts = { width: img.width, height: img.height };
    if (img.colorType === M.ColorType.Bilevel) p.kind = "bw";
    else if (img.colorSpace === M.ColorSpace.CMYK) p.kind = "cmyk";
    else if (img.colorSpace === M.ColorSpace.Gray) p.kind = "greyscale";
    else p.kind = "colour";
    p.alpha = img.hasAlpha;
    p.frames = f.ext === "gif" || f.ext === "webp" || f.ext === "png" || f.ext === "apng" ? imgs.length : 1;
    const dens = img.density;
    if (dens.units === M.DensityUnit.PixelsPerInch) p.dpi = [dens.x, dens.y];
    else if (dens.units === M.DensityUnit.PixelsPerCentimeter) p.dpi = [dens.x * 2.54, dens.y * 2.54];
    p.orientation = Number(attr("Orientation")) || 1;
    p.make = attr("Make");
    p.model = attr("Model");
    p.description = attr("ImageDescription");
    p.artist = attr("Artist");
    p.copyright = attr("Copyright");
    p.taken = exifDate(attr("DateTimeOriginal") ?? attr("DateTime") ?? "");
    p.lens = attr("LensModel");
    p.exposure = rational(attr("ExposureTime"));
    p.fnumber = rational(attr("FNumber"));
    p.iso = rational(attr("PhotographicSensitivity") ?? attr("ISOSpeedRatings"));
    p.focal = rational(attr("FocalLength"));
    const flash = attr("Flash");
    if (flash !== undefined && Number.isFinite(Number(flash))) p.flash = Number(flash);
    const lat = dms(attr("GPSLatitude")), lon = dms(attr("GPSLongitude"));
    if (lat !== undefined && lon !== undefined) {
      p.lat = attr("GPSLatitudeRef") === "S" ? -lat : lat;
      p.lon = attr("GPSLongitudeRef") === "W" ? -lon : lon;
      const alt = rational(attr("GPSAltitude"));
      if (alt !== undefined) p.altitude = attr("GPSAltitudeRef") === "1" ? -alt : alt;
    }
    return clean(p);
  });
}

async function media(f: InputFile): Promise<Facts> {
  const mb = await import("mediabunny");
  const { date } = await import("../properties/format");
  const input = new mb.Input({ source: new mb.BlobSource(f.file), formats: mb.ALL_FORMATS });
  try {
    const m: Facts = { duration: await input.computeDuration(), size: f.file.size };
    const video = await input.getPrimaryVideoTrack();
    if (video) {
      const stats = await video.computePacketStats();
      m.video = { codec: video.codec ?? "", width: video.codedWidth, height: video.codedHeight, fps: stats.averagePacketRate, rotation: video.rotation };
    }
    const audio = await input.getAudioTracks();
    m.audio = audio.map((a) => ({ codec: a.codec ?? "", channels: a.numberOfChannels, sample_rate: a.sampleRate, language: a.languageCode }));
    const tags = await input.getMetadataTags();
    const track = tags.trackNumber ? (tags.tracksTotal ? `${tags.trackNumber}/${tags.tracksTotal}` : String(tags.trackNumber)) : undefined;
    const disc = tags.discNumber ? (tags.discsTotal ? `${tags.discNumber}/${tags.discsTotal}` : String(tags.discNumber)) : undefined;
    const raw = (tags.raw ?? {}) as Record<string, unknown>;
    const rawText = (...keys: string[]) => keys.map((k) => raw[k]).find((v): v is string => typeof v === "string" && v.trim() !== "");
    m.tags = clean({
      title: tags.title, artist: tags.artist, album: tags.album, album_artist: tags.albumArtist, track, disc,
      date: tags.date ? String(tags.date.getUTCFullYear()) : undefined, genre: tags.genre,
      composer: rawText("TCOM", "©wrt", "COMPOSER", "composer"), comment: tags.comment ?? tags.description,
    });
    m.cover = Boolean(tags.images?.length);
    const where = ["location", "©xyz", "com.apple.quicktime.location.ISO6709"].map((k) => raw[k]).find((v) => typeof v === "string") as string | undefined;
    const ll = where ? iso6709(where) : null;
    if (ll) [m.lat, m.lon] = ll;
    if (video && tags.creationTime) m.recorded = date(tags.creationTime);
    return m;
  } finally {
    input.dispose?.();
  }
}

async function documentFacts(f: InputFile): Promise<Facts> {
  if (f.ext === "pdf") {
    const { openDocument } = await import("../engines/mupdf");
    const doc = await openDocument(f.file, "pdf");
    try {
      const meta = (key: string) => doc.getMetaData(key) || undefined;
      const locked = doc.needsPassword();
      const encryption = meta("encryption");
      let size: number[] | undefined;
      if (!locked && doc.countPages()) {
        const [x0, y0, x1, y1] = doc.loadPage(0).getBounds();
        size = [x1 - x0, y1 - y0];
      }
      return clean({
        pages: doc.countPages(),
        page_size: size,
        encrypted: locked || (encryption !== undefined && encryption !== "None"),
        pdf_version: meta("format"),
        title: meta("info:Title"),
        subject: meta("info:Subject"),
        author: meta("info:Author"),
        keywords: meta("info:Keywords"),
        application: meta("info:Creator") ?? meta("info:Producer"),
        created: pdfDate(meta("info:CreationDate") ?? ""),
        modified: pdfDate(meta("info:ModDate") ?? ""),
      });
    } finally {
      doc.destroy?.();
    }
  }
  const { unzipSync, strFromU8 } = await import("fflate");
  const want = OFFICE_EXTS.has(f.ext) ? ["docProps/core.xml", "docProps/app.xml"] : ["meta.xml"];
  const parts = unzipSync(new Uint8Array(await f.file.arrayBuffer()), { filter: (e) => want.includes(e.name) });
  const text = (name: string) => (parts[name] ? strFromU8(parts[name]) : "");
  return OFFICE_EXTS.has(f.ext) ? officeFacts(text("docProps/core.xml"), text("docProps/app.xml")) : odfFacts(text("meta.xml"));
}

async function font(f: InputFile): Promise<Facts> {
  const { toSfnt } = await import("../engines/fonts");
  return fontFacts(await toSfnt(new Uint8Array(await f.file.arrayBuffer()), f.name));
}

async function model(f: InputFile, progress: (m: string) => void): Promise<[string, string][]> {
  const { model3dViewConverter, VIEW_TARGET } = await import("./model3d");
  const { describe } = await import("../editor3d/mesh");
  const out = await model3dViewConverter.run([f], VIEW_TARGET, {}, { progress: (_x, m) => m && progress(m) });
  const info = out.find((o) => o.name === "info.json");
  if (!info) throw new ConversionError(NOTE);
  return describe(JSON.parse(new TextDecoder().decode(info.data)));
}

/** The facts of one file (without "file": the page adds those from the File). */
export async function readFacts(f: InputFile, progress: (m: string) => void = () => {}): Promise<Facts> {
  try {
    if (f.family === "image") return { picture: await picture(f) };
    if (f.family === "video" || f.family === "audio") return { media: await media(f) };
    if (f.family === "document" && (f.ext === "pdf" || OFFICE_EXTS.has(f.ext) || ODF_EXTS.has(f.ext))) return { document: await documentFacts(f) };
    if (f.family === "font") return { font: await font(f) };
    if (f.family === "model") return { model: await model(f, progress) };
  } catch (e) {
    console.warn("properties: couldn't read", f.name, e);
    const unreadable = f.family === "video" || f.family === "audio";
    return { note: unreadable ? BROWSER_NOTE : NOTE };
  }
  return {};
}

export const propertiesConverter: Converter = {
  id: "properties-info",
  family: "image",
  anyFamily: true,
  options: [],
  targets: () => [],
  async run(files, _target, _o, ctx) {
    const facts = await readFacts(files[0], (m) => ctx.progress(0.5, m));
    return [{ name: "facts.json", data: new TextEncoder().encode(JSON.stringify(facts)), type: "application/json" }];
  },
};
