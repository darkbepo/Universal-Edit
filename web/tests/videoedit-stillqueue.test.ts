// The still preview's request queue (ui/videoeditor/stillqueue.ts) and its canvas size (preview.ts).
import { describe, expect, it } from "vitest";
import { DEBOUNCE_MS, StillQueue } from "../src/ui/videoeditor/stillqueue";
import { previewSize } from "../src/ui/videoeditor/preview";

/** A hand-driven clock: `advance` fires the timers that are due. */
class FakeTimer {
  t = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  private next = 1;
  set(fn: () => void, ms: number): unknown {
    const id = this.next++;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  }
  clear(h: unknown): void {
    this.timers.delete(h as number);
  }
  now(): number {
    return this.t;
  }
  advance(ms: number): void {
    const end = this.t + ms;
    for (;;) {
      const due = [...this.timers].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.t = due[1].at;
      due[1].fn();
    }
    this.t = end;
  }
  get pending(): number {
    return this.timers.size;
  }
}

/** A run function whose runs we finish by hand. */
function runs() {
  const r = {
    started: [] as { job: string; isCurrent: () => boolean; finish: () => void }[],
    active: 0,
    most: 0,
    run: (job: string, isCurrent: () => boolean) =>
      new Promise<void>((res) => {
        r.active++;
        r.most = Math.max(r.most, r.active);
        r.started.push({ job, isCurrent, finish: () => { r.active--; res(); } });
      }),
  };
  return r;
}
const flush = () => new Promise<void>((res) => setTimeout(res, 0));

describe("StillQueue", () => {
  it("runs a single request at once", () => {
    const timer = new FakeTimer();
    const r = runs();
    const q = new StillQueue(r.run, timer);
    q.request("a");
    expect(r.started.map((s) => s.job)).toEqual(["a"]);
    expect(r.started[0].isCurrent()).toBe(true);
  });

  it("debounces fast requests: the first runs, then only the last, after DEBOUNCE_MS", async () => {
    expect(DEBOUNCE_MS).toBe(40);
    const timer = new FakeTimer();
    const r = runs();
    const q = new StillQueue(r.run, timer);
    q.request("a");
    timer.advance(10);
    q.request("b");
    timer.advance(10);
    q.request("c");
    expect(r.started.map((s) => s.job)).toEqual(["a"]);
    expect(r.started[0].isCurrent()).toBe(false); // superseded while it runs
    r.started[0].finish();
    await flush();
    expect(r.started.map((s) => s.job)).toEqual(["a"]); // still waiting for the quiet time
    timer.advance(DEBOUNCE_MS - 1);
    expect(r.started).toHaveLength(1);
    timer.advance(1);
    expect(r.started.map((s) => s.job)).toEqual(["a", "c"]);
    expect(r.started[1].isCurrent()).toBe(true);
  });

  it("never runs two at the same time", async () => {
    const timer = new FakeTimer();
    const r = runs();
    const q = new StillQueue(r.run, timer);
    q.request("a");
    timer.advance(100);
    q.request("b"); // slow request, but "a" still runs: it waits
    expect(r.started).toHaveLength(1);
    r.started[0].finish();
    await flush();
    expect(r.started.map((s) => s.job)).toEqual(["a", "b"]);
    timer.advance(10);
    q.request("c");
    timer.advance(DEBOUNCE_MS);
    expect(r.started).toHaveLength(2); // "b" still runs
    r.started[1].finish();
    await flush();
    expect(r.started.map((s) => s.job)).toEqual(["a", "b", "c"]);
    expect(r.most).toBe(1);
  });

  it("idle() resolves after the last run", async () => {
    const timer = new FakeTimer();
    const r = runs();
    const q = new StillQueue(r.run, timer);
    await q.idle(); // nothing to do
    q.request("a");
    timer.advance(5);
    q.request("b");
    let done = false;
    void q.idle().then(() => { done = true; });
    r.started[0].finish();
    await flush();
    expect(done).toBe(false);
    timer.advance(DEBOUNCE_MS);
    await flush();
    expect(done).toBe(false);
    r.started[1].finish();
    await flush();
    expect(done).toBe(true);
  });

  it("a failing run doesn't stop the queue", async () => {
    const timer = new FakeTimer();
    const seen: string[] = [];
    const q = new StillQueue(async (job: string) => {
      seen.push(job);
      if (job === "a") throw new Error("broken frame");
    }, timer);
    q.request("a");
    await q.idle();
    timer.advance(100);
    q.request("b");
    await q.idle();
    expect(seen).toEqual(["a", "b"]);
  });

  it("clear() drops what's waiting and makes the running job stale", async () => {
    const timer = new FakeTimer();
    const r = runs();
    const q = new StillQueue(r.run, timer);
    q.request("a");
    timer.advance(5);
    q.request("b");
    q.clear();
    expect(r.started[0].isCurrent()).toBe(false);
    expect(timer.pending).toBe(0);
    r.started[0].finish();
    timer.advance(100);
    await q.idle();
    expect(r.started.map((s) => s.job)).toEqual(["a"]);
  });
});

describe("previewSize", () => {
  it("caps the height at 720 device pixels", () => {
    expect(previewSize({ width: 800, height: 600 }, 2, [1920, 1080])).toEqual([1280, 720]);
  });

  it("fits a portrait frame in a phone's 16:9 box", () => {
    const [w, h] = previewSize({ width: 375, height: 211 }, 3, [1080, 1920]);
    expect(h).toBeLessThanOrEqual(633);
    expect(Math.abs(w - (h * 1080) / 1920)).toBeLessThanOrEqual(1);
    expect(w % 2).toBe(0);
    expect(h % 2).toBe(0);
  });

  it("fills the box when the box is the narrow side", () => {
    expect(previewSize({ width: 320, height: 400 }, 1, [1280, 720])).toEqual([320, 180]);
  });

  it("is never smaller than 2 × 2", () => {
    expect(previewSize({ width: 0, height: 0 }, 1, [1920, 1080])).toEqual([2, 2]);
    expect(previewSize({ width: 1, height: 300 }, 1, [1920, 1080])).toEqual([2, 2]);
  });
});
