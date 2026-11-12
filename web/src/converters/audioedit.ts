// The audio editor's helper in the worker (never offered on the convert page): FFmpeg decodes
// each file to a 32-bit float WAV, the first one at its own rate (mono stays mono, wider becomes
// stereo), the others at the first one's rate and channels so the page can join them.
// Saving goes through the normal audio converter ("audio-file") with a float WAV as input.
import { ConversionError, type Converter, type OutputFile } from "../core/types";

/** ~15 minutes of 44.1 kHz stereo (float32: ~320 MB in the tab). */
export const MAX_SAMPLES = 80_000_000;
export const TOO_LONG = "This is too long for the audio editor in the browser (about 15 minutes of stereo sound). Trim it in Convert first (Start at / End at), or use the desktop app.";

export const OPEN_TARGET = { id: "open", label: "Audio editor", ext: "wav", group: "", converterId: "audio-edit-open" };

export const audioEditOpenConverter: Converter = {
  id: "audio-edit-open",
  family: "audio",
  options: [],
  targets: () => [],
  async run(files, _target, _o, ctx) {
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const { wavFormat } = await import("../audioedit/wav");
    const ff = await getFFmpeg((m) => ctx.progress(0, m));
    const out: OutputFile[] = [];
    let fmt: { rate: number; channels: number } | null = null;
    let total = 0;
    for (const [i, f] of files.entries()) {
      const msg = `Opening “${f.name}”…`;
      ctx.progress(i / files.length, msg);
      const { path, release } = await ff.mount(f.file);
      try {
        const info = await ff.probe(path);
        if (!info.hasAudio) throw new ConversionError(`“${f.name}” has no sound.`);
        const args = ["-i", path, "-vn", "-sn", "-dn", "-map", "0:a:0", "-c:a", "pcm_f32le"];
        if (fmt) args.push("-ar", String(fmt.rate), "-ac", String(fmt.channels));
        else args.push("-af", "aformat=channel_layouts=mono|stereo");
        await ff.run([...args, "-f", "wav", "/edit.wav"], info.duration, (p) => ctx.progress((i + p) / files.length, msg), `Couldn't read the sound in “${f.name}”.`);
        const data = await ff.readAndDelete("/edit.wav");
        total += data.length / 4;
        if (total > MAX_SAMPLES) throw new ConversionError(TOO_LONG);
        fmt ??= wavFormat(data);
        out.push({ name: `${i}.wav`, data, type: "audio/wav" });
      } finally {
        await release();
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};
