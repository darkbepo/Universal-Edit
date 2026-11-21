import { describe, expect, it } from "vitest";
import { BLOCK_SECONDS, SoundStore, type Decoded, type Decoder } from "../src/ui/videoeditor/sound";

/** A ramp: sample i (at `rate`) has the value i / 1e6, in 1 s pieces; ends at `seconds`. */
function ramp(rate: number, seconds = 20, calls: string[] = []): Decoder {
  return async function* (id, from, to) {
    calls.push(`${id}:${from}-${to}`);
    const end = Math.min(to, seconds);
    for (let t = Math.floor(from); t < end; t += 1) {
      const n = rate;
      const ch = new Float32Array(n);
      for (let i = 0; i < n; i++) ch[i] = (t * rate + i) / 1e6;
      yield { timestamp: t, rate, channels: [ch] } satisfies Decoded;
    }
  };
}

describe("SoundStore", () => {
  it("is null before ensure and returns the ramp after", async () => {
    const s = new SoundStore(ramp(48000));
    expect(s.lookup("a", 1, 4800)).toBeNull();
    await s.ensure([{ sourceId: "a", from: 1, to: 2 }]);
    const got = s.lookup("a", 1, 4800)!;
    expect(got).toHaveLength(2);
    expect(got[0]).toHaveLength(4800);
    expect(got[0][0]).toBeCloseTo(48000 / 1e6, 6);
    expect(got[1][4799]).toBeCloseTo((48000 + 4799) / 1e6, 6);
  });

  it("is continuous across a block edge", async () => {
    const s = new SoundStore(ramp(48000));
    await s.ensure([{ sourceId: "a", from: 3.9, to: 4.1 }]);
    const n = Math.round(0.2 * 48000);
    const got = s.lookup("a", 3.9, n)![0];
    const start = Math.round(3.9 * 48000);
    for (let i = 0; i < n; i += 97) expect(got[i]).toBeCloseTo((start + i) / 1e6, 6);
    expect(got[n - 1]).toBeCloseTo((start + n - 1) / 1e6, 6);
  });

  it("is null when only one of the covering blocks is there", async () => {
    const s = new SoundStore(ramp(48000));
    await s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
    expect(s.lookup("a", 3.9, 48000)).toBeNull();
  });

  it("decodes a block once for two ensures", async () => {
    const calls: string[] = [];
    const s = new SoundStore(ramp(48000, 20, calls));
    await Promise.all([
      s.ensure([{ sourceId: "a", from: 0, to: 1 }]),
      s.ensure([{ sourceId: "a", from: 1, to: 2 }]),
    ]);
    await s.ensure([{ sourceId: "a", from: 2, to: 3 }]);
    expect(calls).toHaveLength(1);
  });

  it("discards a decode under way when the source is forgotten", async () => {
    const s = new SoundStore(ramp(48000));
    const p = s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
    s.forget("a");
    await p;
    expect(s.lookup("a", 0, 100)).toBeNull();
  });

  it("evicts the least recently used block over budget", async () => {
    const block = 2 * BLOCK_SECONDS * 48000 * 4;
    const s = new SoundStore(ramp(48000), { budgetBytes: block * 2 });
    await s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
    await s.ensure([{ sourceId: "a", from: 4, to: 5 }]);
    expect(s.lookup("a", 0, 10)).not.toBeNull(); // touch block 0
    await s.ensure([{ sourceId: "a", from: 8, to: 9 }]);
    expect(s.lookup("a", 0, 10)).not.toBeNull();
    expect(s.lookup("a", 4, 10)).toBeNull();
    expect(s.lookup("a", 8, 10)).not.toBeNull();
  });

  it("brings a 24 kHz source to 48 kHz length", async () => {
    const s = new SoundStore(ramp(24000));
    await s.ensure([{ sourceId: "a", from: 1, to: 2 }]);
    const got = s.lookup("a", 1, 48000)!;
    expect(got[0]).toHaveLength(48000);
    expect(got[0][0]).toBeCloseTo(24000 / 1e6, 6);
    expect(got[0][2]).toBeCloseTo(24001 / 1e6, 6);
  });

  it("is silent before 0 and past the end, and does nothing after dispose", async () => {
    const s = new SoundStore(ramp(48000, 2));
    await s.ensure([{ sourceId: "a", from: -0.1, to: 0.1 }, { sourceId: "a", from: 3, to: 3.5 }]);
    const a = s.lookup("a", -0.01, 960)!;
    expect(a[0][0]).toBe(0);
    expect(a[0][479]).toBe(0);
    expect(a[0][480]).toBeCloseTo(0, 6);
    expect(a[0][481]).toBeCloseTo(1 / 1e6, 6);
    expect(s.lookup("a", 3, 100)![0].every((v) => v === 0)).toBe(true);
    s.dispose();
    await s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
    expect(s.lookup("a", 0, 10)).toBeNull();
  });
});

/** `size`-frame pieces at `rate` covering [from, to) from the piece holding `from`; `value(i)` at source frame i. */
function pieces(rate: number, size: number, value: (i: number) => number, seconds = 20): Decoder {
  return async function* (_id, from, to) {
    const end = Math.min(to, seconds) * rate;
    for (let p = Math.floor((from * rate) / size); p * size < end; p++) {
      const ch = new Float32Array(size);
      for (let i = 0; i < size; i++) ch[i] = value(p * size + i);
      yield { timestamp: (p * size) / rate, rate, channels: [ch] } satisfies Decoded;
    }
  };
}

describe("SoundStore blocks from small pieces", () => {
  const BLOCK = BLOCK_SECONDS * 48000;
  for (const [rate, size] of [[22050, 1024], [44100, 4096]] as const) {
    it(`leaves no gap with ${size}-frame pieces at ${rate} Hz`, async () => {
      const s = new SoundStore(pieces(rate, size, () => 1));
      await s.ensure([{ sourceId: "a", from: 0, to: 8 }]);
      for (const k of [0, 1]) {
        const got = s.lookup("a", k * BLOCK_SECONDS, BLOCK)!;
        for (const ch of got) expect(ch.findIndex((v) => Math.abs(v - 1) > 1e-6)).toBe(-1);
      }
    });

    it(`keeps a ramp monotone with ${size}-frame pieces at ${rate} Hz`, async () => {
      const s = new SoundStore(pieces(rate, size, (i) => i / 1e6));
      await s.ensure([{ sourceId: "a", from: 4, to: 8 }]);
      const got = s.lookup("a", 4, BLOCK)![0];
      let bad = -1;
      for (let i = 1; i < got.length && bad < 0; i++) if (got[i] < got[i - 1]) bad = i;
      expect(bad).toBe(-1);
      expect(got[0]).toBeCloseTo((4 * rate) / 1e6, 5);
    });
  }

  it("mixes stereo and mono pieces into both channels", async () => {
    const s = new SoundStore(async function* () {
      yield { timestamp: 0, rate: 48000, channels: [new Float32Array(1000).fill(0.5), new Float32Array(1000).fill(-0.5)] };
      yield { timestamp: 1000 / 48000, rate: 48000, channels: [new Float32Array(1000).fill(0.25)] };
    });
    await s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
    const [l, r] = s.lookup("a", 0, 2000)!;
    expect([l[0], r[0], l[1500], r[1500]]).toEqual([0.5, -0.5, 0.25, 0.25]);
  });

  it("a decoder that throws leaves nothing pending, isn't retried, and works again after forget", async () => {
    let calls = 0;
    let fail = true;
    const errors = console.error;
    const logged: unknown[] = [];
    console.error = (...a: unknown[]) => void logged.push(a);
    try {
      const good = ramp(48000);
      const s = new SoundStore(async function* (id, from, to) {
        calls++;
        if (fail) throw new Error("broken");
        yield* good(id, from, to);
      });
      await s.ensure([{ sourceId: "a", from: 0, to: 1 }]).catch(() => undefined);
      const inner = s as unknown as { inflight: Map<string, unknown>; needed: Map<string, unknown> };
      expect(inner.inflight.size).toBe(0);
      expect(inner.needed.size).toBe(0);
      expect(s.lookup("a", 0, 10)).toBeNull();
      await s.ensure([{ sourceId: "a", from: 0, to: 1 }]).catch(() => undefined);
      expect(calls).toBe(1); // a failed block isn't decoded again every chunk
      expect(logged).toHaveLength(1);
      fail = false;
      s.forget("a");
      await s.ensure([{ sourceId: "a", from: 0, to: 1 }]);
      expect(calls).toBe(2);
      expect(s.lookup("a", 0, 10)).not.toBeNull();
    } finally {
      console.error = errors;
    }
  });
});
