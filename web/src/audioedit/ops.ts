// Sample maths for the audio editor (port of src/umc/audioedit/ops.py): pure functions on planar
// samples (one Float32Array per channel). Tests: web/tests/audioedit.test.ts = tests/test_audioedit.py.
export type Samples = Float32Array[];

export const GAIN_MIN_DB = -48, GAIN_MAX_DB = 48;
export const NORMALIZE_DB = -1; // peak level Normalize aims for

export const dbToGain = (db: number) => 10 ** (db / 20);

export const frames = (x: Samples) => x[0]?.length ?? 0;

export function slice(x: Samples, a: number, b: number): Samples {
  return x.map((ch) => ch.slice(a, b));
}

/** Largest absolute sample (0 for silence / nothing). */
export function peak(x: Samples): number {
  let p = 0;
  for (const ch of x) for (let i = 0; i < ch.length; i++) { const a = Math.abs(ch[i]); if (a > p) p = a; }
  return p;
}

/** Peak level in dBFS, null for silence. */
export function peakDb(x: Samples): number | null {
  const p = peak(x);
  return p > 0 ? 20 * Math.log10(p) : null;
}

export function gain(x: Samples, db: number): Samples {
  const g = Math.fround(dbToGain(db));
  return x.map((ch) => ch.map((v) => v * g));
}

/** The gain (dB) that brings the peak to `target`; null for silence. */
export function normalizeDb(x: Samples, target = NORMALIZE_DB): number | null {
  const p = peakDb(x);
  return p === null ? null : target - p;
}

/** Linear fade over the whole range: 0 → 1 (in) or 1 → 0 (out). */
export function fade(x: Samples, fadeIn: boolean): Samples {
  const n = frames(x);
  const d = Math.max(n - 1, 1);
  return x.map((ch) => ch.map((v, i) => v * (fadeIn ? i / d : 1 - i / d)));
}

export function reverse(x: Samples): Samples {
  return x.map((ch) => ch.slice().reverse());
}

export function silence(channels: number, n: number): Samples {
  return Array.from({ length: channels }, () => new Float32Array(n));
}

/** Pasting between mono and stereo: mono is copied to both sides, stereo is averaged. */
export function fitChannels(x: Samples, channels: number): Samples {
  if (x.length === channels) return x;
  if (channels === 1) {
    const out = new Float32Array(frames(x));
    for (const ch of x) for (let i = 0; i < out.length; i++) out[i] += ch[i] / x.length;
    return [out];
  }
  return x.length === 1 ? Array.from({ length: channels }, () => x[0].slice()) : x.slice(0, channels);
}

export function join(parts: Samples[]): Samples {
  const channels = parts[0].length;
  const total = parts.reduce((n, p) => n + frames(p), 0);
  return Array.from({ length: channels }, (_, c) => {
    const out = new Float32Array(total);
    let at = 0;
    for (const p of parts) { out.set(p[c], at); at += p[c].length; }
    return out;
  });
}

/** Like 1:05.250 (m:ss.mmm), with hours when needed: 1:02:03.000. */
export function formatTime(seconds: number): string {
  let ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3_600_000); ms -= h * 3_600_000;
  const m = Math.floor(ms / 60_000); ms -= m * 60_000;
  const s = Math.floor(ms / 1000); ms -= s * 1000;
  const p2 = (n: number) => String(n).padStart(2, "0");
  const p3 = String(ms).padStart(3, "0");
  return h ? `${h}:${p2(m)}:${p2(s)}.${p3}` : `${m}:${p2(s)}.${p3}`;
}
