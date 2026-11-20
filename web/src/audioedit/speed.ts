// Speed / tempo / pitch for the audio editor: the FFmpeg filter for each change.
// Line-by-line port of src/umc/audioedit/speed.py; both check tests/fixtures/audioedit_speed_cases.json.
// - speed: faster and higher (or slower and lower), like a record player: asetrate + aresample
// - tempo: faster or slower, same pitch: atempo (one takes 0.5…2, so it is chained)
// - pitch: higher or lower, same length: speed, then tempo back to the original length

export type SpeedMode = "speed" | "tempo" | "pitch";
export const MODES: SpeedMode[] = ["speed", "tempo", "pitch"];
export const PERCENT_MIN = 25;
export const PERCENT_MAX = 400;
export const SEMITONES_MIN = -12;
export const SEMITONES_MAX = 12;
/** Shorter parts are refused (atempo needs a few blocks of sound). */
export const MIN_SECONDS = 0.05;
export const TOO_SHORT = "Select a longer part (at least a twentieth of a second).";
/** Silence added after the part, so atempo hands back its last blocks too. */
export const PAD_SECONDS = 0.5;

/** `value` (percent for speed / tempo, semitones for pitch) inside its range. */
export function clamp(mode: SpeedMode, value: number): number {
  if (mode === "pitch") return Math.max(SEMITONES_MIN, Math.min(SEMITONES_MAX, value));
  return Math.max(PERCENT_MIN, Math.min(PERCENT_MAX, value));
}

/** True when the change would do nothing (100 %, 0 semitones). */
export function isIdentity(mode: SpeedMode, value: number): boolean {
  return clamp(mode, value) === (mode === "pitch" ? 0 : 100);
}

/** A filter number: up to 6 decimals, no trailing zeros. */
function num(x: number): string {
  const text = x.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  return text || "0";
}

/** atempo filters multiplying to `factor` (each one 0.5…2). */
export function atempoChain(factor: number): string[] {
  const parts: string[] = [];
  while (factor > 2) { parts.push("atempo=2"); factor /= 2; }
  while (factor < 0.5) { parts.push("atempo=0.5"); factor /= 0.5; }
  if (Math.abs(factor - 1) > 1e-9) parts.push(`atempo=${num(factor)}`);
  return parts;
}

/** The sample rate asetrate claims for speed / pitch (whole numbers only). */
export function stretchedRate(mode: SpeedMode, value: number, rate: number): number {
  value = clamp(mode, value);
  const ratio = mode === "speed" ? value / 100 : 2 ** (value / 12);
  return Math.max(1, Math.floor(rate * ratio + 0.5));
}

/** The -af text for this change of a sound at `rate` Hz. */
export function filterChain(mode: SpeedMode, value: number, rate: number): string {
  value = clamp(mode, value);
  let parts: string[];
  if (mode === "tempo") parts = atempoChain(value / 100);
  else {
    const fake = stretchedRate(mode, value, rate);
    parts = [`asetrate=${fake}`, `aresample=${rate}`];
    if (mode === "pitch") parts.push(...atempoChain(rate / fake));
  }
  return parts.join(",") || "anull";
}

/** About how long `seconds` of sound will be afterwards. */
export function newSeconds(mode: SpeedMode, value: number, seconds: number, rate: number): number {
  value = clamp(mode, value);
  if (mode === "tempo") return (seconds * 100) / value;
  if (mode === "speed") return (seconds * rate) / stretchedRate(mode, value, rate);
  return seconds;
}

/** How many frames `frames` become (the result is cut / padded to this). */
export function expectedFrames(mode: SpeedMode, value: number, frames: number, rate: number): number {
  value = clamp(mode, value);
  if (mode === "tempo") return Math.floor((frames * 100) / value + 0.5);
  if (mode === "speed") return Math.floor((frames * rate) / stretchedRate(mode, value, rate) + 0.5);
  return frames;
}

/** `part` with PAD_SECONDS of silence after it (what goes to FFmpeg). */
export function padded(part: Float32Array[], rate: number): Float32Array[] {
  const extra = Math.ceil(PAD_SECONDS * rate);
  return part.map((ch) => {
    const out = new Float32Array(ch.length + extra);
    out.set(ch);
    return out;
  });
}

/** FFmpeg's result cut (or padded with silence) to exactly `frames`. */
export function fitLength(x: Float32Array[], frames: number): Float32Array[] {
  return x.map((ch) => {
    if (ch.length >= frames) return ch.slice(0, frames);
    const out = new Float32Array(frames);
    out.set(ch);
    return out;
  });
}
