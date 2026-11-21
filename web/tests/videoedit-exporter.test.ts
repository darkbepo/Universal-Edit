import { describe, expect, it } from "vitest";
import { FrameReader, chunksDue } from "../src/ui/videoeditor/exporter";
import type { Timed } from "../src/ui/videoeditor/framestream";

/** Frames every 0.04 s from `first` on (a frame at or after `from`), optionally ending at `end`. */
function fake(opts: { first?: number; end?: number; throwOpen?: boolean } = {}) {
  const first = opts.first ?? 0;
  const log = { opened: [] as number[], returned: 0 };
  const open = (from: number): AsyncIterator<Timed<number>> => {
    log.opened.push(from);
    if (opts.throwOpen) throw new Error("can't open");
    let k = Math.max(0, Math.floor((from - first) / 0.04 + 1e-9));
    return {
      async next() {
        const ts = Math.round((first + k * 0.04) * 1000) / 1000;
        if (opts.end !== undefined && ts > opts.end + 1e-9) return { done: true, value: undefined };
        k++;
        return { done: false, value: { timestamp: ts, duration: 0.04, value: ts } };
      },
      async return() {
        log.returned++;
        return { done: true, value: undefined };
      },
    };
  };
  return { open, log };
}

describe("FrameReader", () => {
  it("returns the newest frame at or before t, reading forward", async () => {
    const f = fake();
    const r = new FrameReader(f.open);
    expect(await r.at(0.05)).toBeCloseTo(0.04);
    expect(await r.at(0.08)).toBeCloseTo(0.08);
    expect(await r.at(0.5)).toBeCloseTo(0.48);
    expect(f.log.opened).toEqual([0.05]);
  });

  it("counts a frame a hair after t as at t", async () => {
    const f = fake();
    const r = new FrameReader(f.open);
    expect(await r.at(0.08 - 1e-7)).toBeCloseTo(0.08);
  });

  it("gives the first frame when t is before it", async () => {
    const f = fake({ first: 0.02 });
    const r = new FrameReader(f.open);
    expect(await r.at(0)).toBeCloseTo(0.02);
    expect(await r.at(0.03)).toBeCloseTo(0.02);
    expect(await r.at(0.07)).toBeCloseTo(0.06);
  });

  it("reopens when going back more than 1 s", async () => {
    const f = fake();
    const r = new FrameReader(f.open);
    expect(await r.at(2)).toBeCloseTo(2);
    expect(await r.at(1.5)).toBeCloseTo(2); // within 1 s back: no reopen, keeps the frame it has
    expect(f.log.opened).toEqual([2]);
    expect(await r.at(0.5)).toBeCloseTo(0.48);
    expect(f.log.opened).toEqual([2, 0.5]);
    expect(f.log.returned).toBe(1);
  });

  it("reopens on a jump of more than 2 s past the read position", async () => {
    const f = fake();
    const r = new FrameReader(f.open);
    await r.at(0.1);
    expect(await r.at(3.1)).toBeCloseTo(3.08);
    expect(f.log.opened).toEqual([0.1, 3.1]);
  });

  it("keeps returning the last frame once the iterator ends", async () => {
    const f = fake({ end: 0.2 });
    const r = new FrameReader(f.open);
    expect(await r.at(0.1)).toBeCloseTo(0.08);
    expect(await r.at(1)).toBeCloseTo(0.2);
    expect(await r.at(5)).toBeCloseTo(0.2);
    expect(f.log.opened).toEqual([0.1]);
  });

  it("returns null when open throws, without opening again for the same position", async () => {
    const f = fake({ throwOpen: true });
    const r = new FrameReader(f.open);
    expect(await r.at(1)).toBeNull();
    expect(await r.at(1.04)).toBeNull();
    expect(f.log.opened).toEqual([1]);
  });

  it("close() releases the iterator and returns null afterwards", async () => {
    const f = fake();
    const r = new FrameReader(f.open);
    await r.at(0.1);
    r.close();
    expect(f.log.returned).toBe(1);
    expect(await r.at(0.2)).toBeNull();
  });
});

describe("chunksDue", () => {
  it("adds a 1 s chunk once the video has passed its end", () => {
    expect(chunksDue(0.5, 0, 10)).toEqual([]);
    expect(chunksDue(1, 0, 10)).toEqual([0]);
    expect(chunksDue(2.5, 1, 10)).toEqual([1]);
    expect(chunksDue(3, 1, 10)).toEqual([1, 2]);
  });

  it("the last (short) chunk is due once the video reaches the length", () => {
    expect(chunksDue(9.9, 9, 9.96)).toEqual([]);
    expect(chunksDue(9.96, 9, 9.96)).toEqual([9]);
    expect(chunksDue(9.96, 10, 9.96)).toEqual([]);
  });

  it("nothing for a zero length", () => {
    expect(chunksDue(1, 0, 0)).toEqual([]);
  });
});
