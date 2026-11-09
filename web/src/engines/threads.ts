// Thread limits for the multi-threaded ffmpeg.wasm core.
//
// @ffmpeg/core-mt starts a fixed pool of 32 Web Workers (one per thread). A thread beyond the
// pool needs a new Worker, which can only start once FFmpeg yields to the event loop — but
// FFmpeg is blocked waiting for that thread, so the conversion hangs at 0 % forever. FFmpeg's
// automatic thread counts scale with the CPU (x264 alone uses 1.5 × cores, plus the decoder and
// filter threads), so a 12-core PC re-encoding a phone video ran out while CI's small runners
// never did. Every run therefore gets explicit, small thread counts.
//
// The encoder cap is 4, not higher: with 6 x264 threads a 1080×1920 encode crashed the worker
// (each frame thread holds whole frames, and the wasm heap is limited).
//
// With two or more inputs the decoders and filters get one thread each: crossfade slideshows
// (one looped PNG input per picture + an xfade chain + x264) hung after frame 1 with 2 pictures
// at 4 decoder threads, and with 5 pictures at 4 filter threads. FFmpeg uses more pool workers
// than the flags alone suggest, so stay well below the pool.

/** Workers @ffmpeg/core-mt pre-starts (`pthreadPoolSize` in ffmpeg-core.js). */
export const POOL_SIZE = 32;

const MAX_DECODER = 4;
const MAX_FILTER = 4;
const MAX_ENCODER = 4;

/** ``args`` (single output path last) with thread counts that fit the worker pool. */
export function limitThreads(args: string[], cores: number): string[] {
  const c = Math.max(1, Math.floor(cores) || 1);
  const inputs = args.filter((a) => a === "-i").length;
  const decoder = String(inputs > 1 ? 1 : Math.min(MAX_DECODER, c));
  const filter = String(inputs > 1 ? 1 : Math.min(MAX_FILTER, c));
  const encoder = String(Math.min(MAX_ENCODER, c));
  const out = ["-filter_threads", filter, "-filter_complex_threads", filter];
  for (const a of args.slice(0, -1)) {
    if (a === "-i") out.push("-threads", decoder);
    out.push(a);
  }
  out.push("-threads", encoder, ...args.slice(-1));
  return out;
}

/** Rough worst-case number of pool threads a ``limitThreads`` command line can use. */
export function threadBudget(args: string[]): number {
  let total = 0;
  let inputs = 0;
  for (let i = 0; i < args.length; i++) {
    const next = Number(args[i + 1]);
    if (args[i] === "-i") inputs++;
    else if (args[i] === "-filter_threads" || args[i] === "-filter_complex_threads") total += next;
    else if (args[i] === "-threads") total += next;
  }
  // One reader thread per input when there are several, one x264 lookahead thread, one spare.
  return total + (inputs > 1 ? inputs : 0) + 2;
}
