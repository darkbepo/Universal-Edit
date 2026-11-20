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

export const SPEED_TARGET = { id: "speed", label: "Audio editor", ext: "wav", group: "", converterId: "audio-edit-speed" };

/** Speed / tempo / pitch of one float WAV (options `mode`, `value`; audioedit/speed.ts). */
export const audioEditSpeedConverter: Converter = {
  id: "audio-edit-speed",
  family: "audio",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const speed = await import("../audioedit/speed");
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const { wavFormat } = await import("../audioedit/wav");
    const mode = String(o.mode) as import("../audioedit/speed").SpeedMode;
    if (!speed.MODES.includes(mode)) throw new ConversionError(`Unknown change “${mode}”.`);
    const value = Number(o.value);
    const f = files[0];
    const data = new Uint8Array(await f.file.arrayBuffer());
    const { rate, channels } = wavFormat(data);
    const msg = `Changing the ${mode}…`;
    const ff = await getFFmpeg((m) => ctx.progress(0, m));
    ctx.progress(0, msg);
    const { path, release } = await ff.mount(f.file);
    try {
      const seconds = speed.newSeconds(mode, value, (data.length - 44) / 4 / channels / rate, rate);
      await ff.run(["-i", path, "-af", speed.filterChain(mode, value, rate), "-c:a", "pcm_f32le", "-ar", String(rate), "-f", "wav", "/changed.wav"],
        seconds, (p) => ctx.progress(p, msg), `Couldn't change the ${mode} of the sound.`);
      const out = await ff.readAndDelete("/changed.wav");
      ctx.progress(1, "Done");
      return [{ name: "changed.wav", data: out, type: "audio/wav" }];
    } finally {
      await release();
    }
  },
};

export const DENOISE_TARGET = { id: "denoise", label: "Audio editor", ext: "wav", group: "", converterId: "audio-edit-denoise" };

/** Noise reduction of one float WAV (options `profile` = JSON list, `reduce_db`,
 *  `sensitivity_db`, `smoothing`; audioedit/noise.ts). Runs here so the page stays responsive. */
export const audioEditDenoiseConverter: Converter = {
  id: "audio-edit-denoise",
  family: "audio",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const noise = await import("../audioedit/noise");
    const { readWav, writeWav } = await import("../audioedit/wav");
    const { rate, samples } = readWav(new Uint8Array(await files[0].file.arrayBuffer()));
    const profile = JSON.parse(String(o.profile ?? "[]")) as number[];
    if (profile.length !== noise.BANDS) throw new ConversionError("The learned noise doesn't fit this sound. Learn it again.");
    const msg = "Reducing noise…";
    ctx.progress(0, msg);
    const out = noise.reduce(samples, rate, profile, {
      reduceDb: Number(o.reduce_db), sensitivityDb: Number(o.sensitivity_db), smoothing: Number(o.smoothing),
      progress: (p) => ctx.progress(p, msg),
    });
    ctx.progress(1, "Done");
    return [{ name: "clean.wav", data: writeWav(rate, out, 32), type: "audio/wav" }];
  },
};
