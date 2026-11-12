// The waveform's min / max per screen column (port of src/umc/audioedit/peaks.py). A summary of
// min / max per BLOCK frames is built once per change; zoomed out, columns are read from it,
// zoomed in (fewer than BLOCK frames per column) from the samples.
import type { Samples } from "./ops";

export const BLOCK = 256;

export interface Peaks { mins: Float32Array[]; maxs: Float32Array[] }

export function build(samples: Samples): Peaks {
  const mins: Float32Array[] = [], maxs: Float32Array[] = [];
  for (const ch of samples) {
    const blocks = Math.ceil(ch.length / BLOCK);
    const lo = new Float32Array(blocks), hi = new Float32Array(blocks);
    for (let b = 0; b < blocks; b++) {
      let mn = Infinity, mx = -Infinity;
      const end = Math.min(ch.length, (b + 1) * BLOCK);
      for (let i = b * BLOCK; i < end; i++) { const v = ch[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
      lo[b] = mn; hi[b] = mx;
    }
    mins.push(lo); maxs.push(hi);
  }
  return { mins, maxs };
}

/** Column i covers frames [start + i·fpp, start + (i+1)·fpp); columns past the end are left
 * out. Every frame a column touches counts (a block shared by two columns counts for both). */
export function columns(samples: Samples, peaks: Peaks, start: number, fpp: number, width: number): { mins: Float32Array[]; maxs: Float32Array[] } {
  const n = samples[0]?.length ?? 0;
  const firsts: number[] = [];
  for (let i = 0; i < width; i++) {
    const f = Math.floor(start + i * fpp);
    if (f >= n) break;
    firsts.push(Math.max(0, f));
  }
  const k = firsts.length;
  const fromBlocks = fpp >= BLOCK;
  const mins: Float32Array[] = [], maxs: Float32Array[] = [];
  samples.forEach((ch, c) => {
    const lo = new Float32Array(k), hi = new Float32Array(k);
    for (let i = 0; i < k; i++) {
      const a = firsts[i];
      const end = Math.min(n, Math.max(a + 1, Math.ceil(start + (i + 1) * fpp)));
      let mn = Infinity, mx = -Infinity;
      if (fromBlocks) {
        const bl = peaks.mins[c], bh = peaks.maxs[c];
        for (let b = Math.floor(a / BLOCK); b < Math.ceil(end / BLOCK); b++) { if (bl[b] < mn) mn = bl[b]; if (bh[b] > mx) mx = bh[b]; }
      } else {
        for (let j = a; j < end; j++) { const v = ch[j]; if (v < mn) mn = v; if (v > mx) mx = v; }
      }
      lo[i] = mn; hi[i] = mx;
    }
    mins.push(lo); maxs.push(hi);
  });
  return { mins, maxs };
}
