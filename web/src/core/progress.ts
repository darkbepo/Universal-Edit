// Per-file progress for the queue (same rule as umc.core.job.file_fractions on the desktop).
// Converters report progress in equal slices per input file (i/n … (i+1)/n), so file i is
// overall·n − i, clamped to 0..1.
export function fileFractions(overall: number, n: number): number[] {
  const o = Math.max(0, Math.min(1, overall));
  return Array.from({ length: n }, (_, i) => Math.max(0, Math.min(1, o * n - i)));
}

export type RowState = "idle" | "waiting" | "running" | "done" | "error";

/** Row states for the fractions: finished → done, the first unfinished → running, the rest waiting. */
export function rowStates(fractions: number[]): RowState[] {
  return fractions.map((f, i) => (f >= 1 ? "done" : f > 0 || i === 0 || fractions[i - 1] >= 1 ? "running" : "waiting"));
}
