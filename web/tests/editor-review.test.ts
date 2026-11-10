// Regression tests for the final review's findings.
import { afterEach, describe, expect, it } from "vitest";
import { defaults } from "../src/core/options";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { __stats, makeLayer, solid } from "../src/editor/layer";
import { sampleThumbnail } from "../src/editor/ora";
import type { ToolContext } from "../src/editor/tools/base";
import { makeTools } from "../src/editor/tools/index";
import { stubRaster } from "./editor-shapes.test";

const ctx = (options: Record<string, unknown>): ToolContext => ({ primary: [255, 0, 0, 255], secondary: [0, 0, 255, 255], options: options as never, shift: false, button: "left", font: "DejaVu Sans" });

describe("every tool can be cancelled mid-gesture", () => {
  for (const tool of makeTools(stubRaster)) {
    it(tool.id, () => {
      const doc: EditorDoc = { ...newDoc(40, 40) };
      doc.layers = [makeLayer("bg", 40, 40, solid(40, 40, [10, 20, 30, 255]))];
      const before = Array.from(doc.layers[0].data);
      const c = ctx(defaults(tool.options));
      tool.press(doc, 10, 10, c);
      tool.move(doc, 30, 25, c);
      tool.cancel(doc);
      expect(Array.from(doc.layers[0].data)).toEqual(before);
      expect(tool.release(doc, 30, 25, c)).toBeNull();
    });
  }
});

describe("layer thumbnails", () => {
  it("are sampled without copying the layer", () => {
    const big = makeLayer("big", 3000, 2000, solid(3000, 2000, [0, 128, 255, 255]));
    __stats.largestCrop = 0;
    const t0 = performance.now();
    const t = sampleThumbnail(big, 40);
    expect(performance.now() - t0).toBeLessThan(50);
    expect(__stats.largestCrop).toBe(0);
    expect([t.width, t.height]).toEqual([40, 27]);
    expect(Array.from(t.data.subarray(0, 4))).toEqual([0, 128, 255, 255]);
  });
});

// ---------------------------------------------------------------- worker queue
class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: ErrorEvent) => void) | null = null;
  constructor() {
    FakeWorker.instances.push(this);
  }
  postMessage(req: { id: number; files: { name: string }[] }) {
    setTimeout(() => this.onmessage?.({ data: { type: "done", id: req.id, outputs: [{ name: req.files[0].name, data: new Uint8Array(), type: "x" }] } } as MessageEvent), 20);
  }
  terminate() {}
}

describe("ConversionClient", () => {
  const saved = (globalThis as { Worker?: unknown }).Worker;
  afterEach(() => {
    (globalThis as { Worker?: unknown }).Worker = saved;
  });

  it("runs overlapping jobs one after another instead of losing one", async () => {
    (globalThis as { Worker?: unknown }).Worker = FakeWorker;
    const { ConversionClient } = await import("../src/worker/client");
    const client = new ConversionClient();
    const target = { id: "png", label: "PNG", ext: "png", group: "Image", converterId: "image" };
    const f = (name: string) => ({ name, ext: "png", family: "image" as const, file: null as unknown as File });
    const a = client.run(target, [f("a")], {}, () => {});
    const b = client.run(target, [f("b")], {}, () => {});
    const [ra, rb] = await Promise.all([a, b]);
    expect([ra[0].name, rb[0].name]).toEqual(["a", "b"]);
  });
});
