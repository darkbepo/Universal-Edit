// Properties mode's helper in the worker for videos (never offered on the convert page).
// Options: ``mode`` "read" → details.json (properties/video.ts detailsFromProbe of FFmpeg's -i
// output); "save" → the video copied with new details (``edit`` = JSON {values, languages,
// defaults}, ``offset`` = the page's UTC offset in minutes for the typed date — the worker's
// clock zone is the same, but the page computed it for that date). Every track is copied, not
// re-encoded.
import { ConversionError, type Converter } from "../core/types";

export const VIDEO_TAGS_TARGET = { id: "video-tags", label: "Video details", ext: "", group: "", converterId: "video-tags" };

const MUXERS: Record<string, string> = { mp4: "mp4", m4v: "ipod", mov: "mov", mkv: "matroska", webm: "webm" };

export const videoTagsConverter: Converter = {
  id: "video-tags",
  family: "video",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const video = await import("../properties/video");
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const f = files[0];
    const fail = (e: unknown): never => {
      if (e instanceof video.VideoError) throw new ConversionError(e.message);
      throw e;
    };
    const ff = await getFFmpeg((m) => ctx.progress(0, m));
    const input = await ff.mount(f.file);
    try {
      let details: ReturnType<typeof video.detailsFromProbe>;
      try {
        details = video.detailsFromProbe(video.probeFromLog(await ff.inputLog(input.path)), f.ext);
      } catch (e) {
        return fail(e);
      }
      if (o.mode !== "save") {
        return [{ name: "details.json", data: new TextEncoder().encode(JSON.stringify(details)), type: "application/json" }];
      }
      if (f.file.size > video.MAX_BYTES) {
        throw new ConversionError(`“${f.name}” is too big to change in the browser (over 1.5 GB) — use the desktop app.`);
      }
      const parsed = JSON.parse(String(o.edit ?? "{}")) as Partial<{ values: Record<string, string>; languages: Record<string, string>; defaults: Record<string, number | null> }>;
      const edit = {
        values: parsed.values ?? {},
        languages: Object.fromEntries(Object.entries(parsed.languages ?? {}).map(([k, v]) => [Number(k), v])),
        defaults: parsed.defaults ?? {},
      };
      const offset = Number(o.offset);
      const output = `/video-out.${f.ext}`;
      let args: string[];
      try {
        video.checkEdit(details, edit);
        const clean = video.cleanValues(edit.values, Number.isFinite(offset) ? () => offset : undefined);
        args = video.ffmpegArgs(f.ext, input.path, output, details, edit, clean, await ff.major());
      } catch (e) {
        return fail(e);
      }
      args = [...args.slice(0, -1), "-f", MUXERS[f.ext.toLowerCase()], output];
      const msg = `Changing the details of “${f.name}”…`;
      ctx.progress(0, msg);
      const info = await ff.probe(input.path);
      await ff.run(args, info.duration, (p) => ctx.progress(p, msg), `The details of “${f.name}” couldn't be changed.`);
      const data = await ff.readAndDelete(output);
      ctx.progress(1, "Done");
      return [{ name: f.name, data, type: f.file.type || "application/octet-stream" }];
    } finally {
      await input.release();
    }
  },
};
