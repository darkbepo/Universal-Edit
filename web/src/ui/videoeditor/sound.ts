// The preview mixer's sound: decoded sources kept as 4 s blocks of 48 kHz stereo, so `lookup` (the
// mixer's `AudioLookup`) is synchronous. No DOM and no AudioBuffer (the export worker uses it too);
// the decoder is injected, so this file never touches Mediabunny.
import { toStereo48k } from "../../videoedit/resample";
import { SAMPLE_RATE, type AudioLookup } from "../../videoedit/mix";

export const BLOCK_SECONDS = 4;
export const BUDGET_BYTES = 256 * 1024 * 1024;

/** One decoded piece, as the decoder hands it over. */
export interface Decoded { timestamp: number; rate: number; channels: Float32Array[] }
export type Decoder = (sourceId: string, from: number, to: number) => AsyncIterable<Decoded>;

const BLOCK_FRAMES = BLOCK_SECONDS * SAMPLE_RATE;
const BLOCK_BYTES = 2 * BLOCK_FRAMES * 4;

interface Block { data: Float32Array[]; used: number }

const key = (id: string, k: number) => `${id}#${k}`;

/** The block numbers covering source seconds [from, to) (nothing below 0). */
function blocksOf(from: number, to: number): number[] {
  const f0 = Math.round(from * SAMPLE_RATE);
  const f1 = Math.max(Math.round(to * SAMPLE_RATE), f0 + 1);
  if (f1 <= 0) return [];
  const out: number[] = [];
  for (let k = Math.floor(Math.max(0, f0) / BLOCK_FRAMES); k <= Math.floor((f1 - 1) / BLOCK_FRAMES); k++) out.push(k);
  return out;
}

export class SoundStore {
  private blocks = new Map<string, Block>();
  private inflight = new Map<string, Promise<void>>();
  private generation = new Map<string, number>();
  /** How many pending `ensure` calls need each block (those are never evicted). */
  private needed = new Map<string, number>();
  private tick = 0;
  private gone = false;
  private budget: number;

  constructor(private decode: Decoder, options: { budgetBytes?: number } = {}) {
    this.budget = options.budgetBytes ?? BUDGET_BYTES;
  }

  private gen(id: string): number {
    return this.generation.get(id) ?? 0;
  }

  /** Decode the blocks these ranges (source seconds) touch and don't have yet. */
  async ensure(needs: { sourceId: string; from: number; to: number }[]): Promise<void> {
    if (this.gone) return;
    const wanted: { id: string; k: number }[] = [];
    for (const n of needs) for (const k of blocksOf(n.from, n.to)) wanted.push({ id: n.sourceId, k });
    for (const w of wanted) {
      const kk = key(w.id, w.k);
      this.needed.set(kk, (this.needed.get(kk) ?? 0) + 1);
      const have = this.blocks.get(kk);
      if (have) have.used = ++this.tick;
    }
    try {
      await Promise.all(wanted.map((w) => this.block(w.id, w.k)));
    } finally {
      for (const w of wanted) {
        const kk = key(w.id, w.k);
        const left = (this.needed.get(kk) ?? 1) - 1;
        if (left > 0) this.needed.set(kk, left);
        else this.needed.delete(kk);
      }
    }
  }

  private block(id: string, k: number): Promise<void> {
    const kk = key(id, k);
    if (this.blocks.has(kk)) return Promise.resolve();
    let p = this.inflight.get(kk);
    if (!p) {
      const mine: Promise<void> = this.decodeBlock(id, k).finally(() => {
        if (this.inflight.get(kk) === mine) this.inflight.delete(kk);
      });
      p = mine;
      this.inflight.set(kk, p);
    }
    return p;
  }

  private async decodeBlock(id: string, k: number): Promise<void> {
    const gen = this.gen(id);
    const base = k * BLOCK_FRAMES;
    const data = [new Float32Array(BLOCK_FRAMES), new Float32Array(BLOCK_FRAMES)];
    for await (const piece of this.decode(id, k * BLOCK_SECONDS, (k + 1) * BLOCK_SECONDS)) {
      if (this.gone || this.gen(id) !== gen) return;
      const st = toStereo48k(piece.channels, piece.rate);
      const at = Math.round(piece.timestamp * SAMPLE_RATE) - base;
      const from = Math.max(0, -at);
      const to = Math.min(st[0].length, BLOCK_FRAMES - at);
      if (to <= from) continue;
      for (let c = 0; c < 2; c++) data[c].set(st[c].subarray(from, to), at + from);
    }
    if (this.gone || this.gen(id) !== gen) return;
    this.blocks.set(key(id, k), { data, used: ++this.tick });
    this.evict();
  }

  private evict(): void {
    while (this.blocks.size * BLOCK_BYTES > this.budget) {
      let oldest: string | null = null;
      let age = Infinity;
      for (const [kk, b] of this.blocks) {
        if (this.needed.has(kk) || b.used >= age) continue;
        oldest = kk;
        age = b.used;
      }
      if (oldest === null) return;
      this.blocks.delete(oldest);
    }
  }

  /** `frames` 48 kHz frames from source second `from`; null when a covering block isn't decoded yet. */
  readonly lookup: AudioLookup = (sourceId, from, frames) => {
    const s0 = Math.round(from * SAMPLE_RATE);
    const out = [new Float32Array(frames), new Float32Array(frames)];
    if (frames <= 0 || s0 + frames <= 0) return out;
    const first = Math.floor(Math.max(0, s0) / BLOCK_FRAMES);
    const last = Math.floor((s0 + frames - 1) / BLOCK_FRAMES);
    const found: Block[] = [];
    for (let k = first; k <= last; k++) {
      const b = this.blocks.get(key(sourceId, k));
      if (!b) return null;
      found.push(b);
    }
    found.forEach((b, i) => {
      b.used = ++this.tick;
      const base = (first + i) * BLOCK_FRAMES;
      const a = Math.max(s0, base, 0);
      const z = Math.min(s0 + frames, base + BLOCK_FRAMES);
      for (let c = 0; c < 2; c++) out[c].set(b.data[c].subarray(a - base, z - base), a - s0);
    });
    return out;
  };

  /** Drop a source's blocks; decodes under way finish into nothing. */
  forget(sourceId: string): void {
    this.generation.set(sourceId, this.gen(sourceId) + 1);
    const prefix = `${sourceId}#`;
    for (const kk of [...this.blocks.keys()]) if (kk.startsWith(prefix)) this.blocks.delete(kk);
    for (const kk of [...this.inflight.keys()]) if (kk.startsWith(prefix)) this.inflight.delete(kk);
  }

  dispose(): void {
    this.gone = true;
    this.blocks.clear();
    this.inflight.clear();
  }
}
