import { describe, expect, it } from "vitest";
import { FrameStream, type Timed } from "../src/ui/videoeditor/framestream";

const settle = async () => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

function fake(opts: { failAfter?: number; end?: number } = {}) {
  const log = { opened: [] as number[], read: [] as number[], returned: 0 };
  const open = (from: number): AsyncIterator<Timed<number>> => {
    log.opened.push(from);
    let k = Math.floor(Math.round(from * 100) / 10);
    let n = 0;
    return {
      async next() {
        if (opts.failAfter !== undefined && n >= opts.failAfter) throw new Error("boom");
        const ts = Math.round(k * 10) / 100;
        if (opts.end !== undefined && ts > opts.end) return { done: true, value: undefined };
        k++;
        n++;
        log.read.push(ts);
        return { done: false, value: { timestamp: ts, duration: 0.1, value: ts } };
      },
      async return() {
        log.returned++;
        return { done: true, value: undefined };
      },
    };
  };
  return { open, log };
}

describe("FrameStream", () => {
  it("returns the newest frame at or before t once reads settle", async () => {
    const f = fake();
    const s = new FrameStream(f.open);
    expect(s.at(0.25)).toBeNull();
    await settle();
    expect(s.at(0.25)).toBeCloseTo(0.2);
  });

  it("reads at most `ahead` frames past t", async () => {
    const f = fake();
    const s = new FrameStream(f.open, 3);
    s.at(0.25);
    await settle();
    s.at(0.25);
    await settle();
    expect(f.log.read.filter((x) => x > 0.25 + 1e-9).length).toBe(3);
  });

  it("reads on as t advances", async () => {
    const f = fake();
    const s = new FrameStream(f.open);
    s.at(0);
    await settle();
    for (let t = 0.05; t < 0.95; t += 0.05) {
      s.at(t);
      await settle();
    }
    expect(s.at(0.95)).toBeCloseTo(0.9);
    expect(f.log.opened).toEqual([0]);
  });

  it("reopens far ahead and far behind", async () => {
    const f = fake();
    const s = new FrameStream(f.open);
    s.at(0);
    await settle();
    s.at(5);
    await settle();
    expect(f.log.opened).toEqual([0, 5]);
    expect(f.log.returned).toBe(1);
    expect(s.at(5.05)).toBeCloseTo(5);
    await settle();
    s.at(2);
    await settle();
    expect(f.log.opened).toEqual([0, 5, 2]);
  });

  it("close stops reading and returns null", async () => {
    const f = fake();
    const s = new FrameStream(f.open);
    s.at(0);
    s.close();
    await settle();
    expect(f.log.returned).toBe(1);
    expect(s.at(0.3)).toBeNull();
    await settle();
    expect(f.log.opened).toEqual([0]);
    expect(f.log.read.length).toBeLessThanOrEqual(1);
  });

  it("keeps the last frame when the iterator throws", async () => {
    const f = fake({ failAfter: 3 });
    const s = new FrameStream(f.open);
    s.at(0.2);
    await settle();
    expect(s.at(0.2)).toBeCloseTo(0.2);
    await settle();
    expect(() => s.at(0.5)).not.toThrow();
    expect(s.at(0.5)).toBeCloseTo(0.4);
    await settle();
    expect(f.log.read.length).toBe(3);
  });

  it("keeps the last frame at the end of the stream", async () => {
    const f = fake({ end: 0.3 });
    const s = new FrameStream(f.open);
    s.at(0);
    await settle();
    expect(s.at(1)).toBeCloseTo(0.3);
  });
});
