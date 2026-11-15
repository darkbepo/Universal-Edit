// Properties mode's helper in the worker (never offered on the convert page): copies one song
// with new tags (properties/music.ts ffmpegArgs — the sound is copied, not re-encoded).
// Options: ``edit`` = JSON {values, remove_cover}, ``cover`` = the new cover picture File or null.
import { ConversionError, type Converter } from "../core/types";

export const MUSIC_TAGS_TARGET = { id: "music-tags", label: "Music tags", ext: "", group: "", converterId: "music-tags" };

export const musicTagsConverter: Converter = {
  id: "music-tags",
  family: "audio",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const music = await import("../properties/music");
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const f = files[0];
    const parsed = JSON.parse(String(o.edit ?? "{}")) as { values?: Record<string, string>; remove_cover?: boolean };
    const coverFile = o.cover instanceof File ? o.cover : null;
    const cover = coverFile ? new Uint8Array(await coverFile.arrayBuffer()) : null;
    const edit = { values: parsed.values ?? {}, cover, remove_cover: Boolean(parsed.remove_cover) };
    const ff = await getFFmpeg((m) => ctx.progress(0, m));
    const msg = `Changing the details of “${f.name}”…`;
    ctx.progress(0, msg);
    const input = await ff.mount(f.file);
    const pic = coverFile ? await ff.mount(coverFile) : null;
    const output = `/tags-out.${f.ext}`;
    try {
      const info = await ff.probe(input.path);
      let args: string[];
      try {
        args = music.ffmpegArgs(f.ext, input.path, output, edit, pic?.path ?? null, info.hasVideo);
      } catch (e) {
        if (e instanceof music.MusicError) throw new ConversionError(e.message);
        throw e;
      }
      await ff.run(args, info.duration, (p) => ctx.progress(p, msg), `The details of “${f.name}” couldn't be changed.`);
      const data = await ff.readAndDelete(output);
      ctx.progress(1, "Done");
      return [{ name: f.name, data, type: f.file.type || "application/octet-stream" }];
    } finally {
      await input.release();
      await pic?.release();
    }
  },
};
