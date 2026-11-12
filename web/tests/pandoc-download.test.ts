// The Pandoc engine reports how much of its download has arrived (the document editor shows it).
import { afterEach, expect, test, vi } from "vitest";

vi.mock("pandoc-wasm-core", () => ({ createPandocInstance: async () => ({}) }));
vi.mock("pandoc-wasm-binary?url", () => ({ default: "pandoc.wasm" }));

afterEach(() => vi.unstubAllGlobals());

test("the download reports whole percents, once each", async () => {
  const chunks = [new Uint8Array(30), new Uint8Array(1), new Uint8Array(69)];
  vi.stubGlobal("fetch", async () =>
    new Response(new ReadableStream({
      start(c) {
        for (const chunk of chunks) c.enqueue(chunk);
        c.close();
      },
    }), { headers: { "content-length": "100" } }),
  );
  const { getPandoc } = await import("../src/engines/pandoc");
  const seen: string[] = [];
  await getPandoc((m) => seen.push(m));
  expect(seen).toEqual([
    "Loading document engine (first time only)…",
    "Loading document engine (first time only)… 0 %",
    "Loading document engine (first time only)… 30 %",
    "Loading document engine (first time only)… 31 %",
    "Loading document engine (first time only)… 100 %",
  ]);
});
