// Recording from the microphone, the part that isn't the browser's (port of
// src/umc/audioedit/record.py; same test cases in web/tests/audioedit-record.test.ts).
// A Take collects the chunks the microphone hands over (planar float32) up to a frame limit, and
// knows the loudest sample of the last chunk for the level meter. When it ends, the take is
// fitted to the sound's channels and sample rate and goes in at the cursor, or in place of the
// selection, as one undo step called LABEL.

export const LABEL = "Record";
export const NO_INPUT = "No microphone was found, so nothing can be recorded.";
export const NO_ROOM = "The sound is as long as the audio editor allows, so nothing more can be recorded.";
export const FULL = "Recording stopped: the sound is as long as the audio editor allows.";
export const DENIED = "The browser wasn't allowed to use the microphone. Allow it (the icon in the address bar) and try again.";
export const NEW_CHANNELS = 2; // an empty editor records this many channels (at the browser's rate)

/** How many frames a take may have so the sound stays within `maxSamples`: the selection is
 *  replaced, and the take ends up with the sound's channels (or its own when there's no sound
 *  yet, docChannels = 0). */
export function room(maxSamples: number, docFrames: number, docChannels: number, selected: number, channels: number): number {
  const width = docChannels || channels;
  return Math.max(0, Math.floor(maxSamples / Math.max(1, width)) - (docFrames - selected));
}

export class Take {
  frames = 0;
  level = 0; // loudest sample of the last chunk, 0…1
  private chunks: Float32Array[][] = [];

  constructor(readonly rate: number, readonly channels: number, readonly maxFrames: number) {}

  get full(): boolean { return this.frames >= this.maxFrames; }
  get seconds(): number { return this.frames / this.rate; }

  /** Add planar samples; false once the take is full (the rest is dropped). */
  add(chunk: Float32Array[]): boolean {
    const n = Math.min(chunk[0]?.length ?? 0, Math.max(0, this.maxFrames - this.frames));
    if (n > 0) {
      const part = Array.from({ length: this.channels }, (_, c) => Float32Array.from((chunk[c] ?? chunk[0]).subarray(0, n)));
      this.chunks.push(part);
      this.frames += n;
      let peak = 0;
      for (const ch of part) for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(ch[i]));
      this.level = Math.min(1, peak);
    }
    return !this.full;
  }

  /** Everything recorded, planar float32. */
  samples(): Float32Array[] {
    return Array.from({ length: this.channels }, (_, c) => {
      const out = new Float32Array(this.frames);
      let at = 0;
      for (const part of this.chunks) { out.set(part[c], at); at += part[c].length; }
      return out;
    });
  }
}
