// The Pandoc engine's download: whole percents of a plain download, an indeterminate message for
// a compressed one (its Content-Length isn't what the stream gives), and the bytes intact.
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const got: Uint8Array[] = [];
vi.mock("pandoc-wasm-core", () => ({
  createPandocInstance: async (wasm: ArrayBuffer) => {
    got.push(new Uint8Array(wasm));
    return {};
  },
}));
vi.mock("pandoc-wasm-binary?url", () => ({ default: "pandoc.wasm" }));

const L = "Loading document engine (first time only)…";

function serve(chunks: number[][], headers: Record<string, string>, status = 200) {
  vi.stubGlobal("fetch", async () =>
    new Response(
      new ReadableStream({
        start(c) {
          for (const chunk of chunks) c.enqueue(new Uint8Array(chunk));
          c.close();
        },
      }),
      { status, headers },
    ),
  );
}

async function load(): Promise<{ seen: string[]; error: unknown }> {
  vi.resetModules();
  const { getPandoc } = await import("../src/engines/pandoc");
  const seen: string[] = [];
  let error: unknown = null;
  await getPandoc((m) => seen.push(m)).catch((e) => (error = e));
  return { seen, error };
}

beforeEach(() => (got.length = 0));
afterEach(() => vi.unstubAllGlobals());

test("a plain download reports whole percents, once each, and keeps the bytes", async () => {
  const bytes = Array.from({ length: 100 }, (_, i) => i);
  serve([bytes.slice(0, 30), bytes.slice(30, 31), bytes.slice(31)], { "content-length": "100" });
  const { seen } = await load();
  expect(seen).toEqual([L, `${L} 0 %`, `${L} 30 %`, `${L} 31 %`, `${L} 100 %`]);
  expect(Array.from(got[0])).toEqual(bytes);
});

test("a compressed download (or one longer than said) shows no percent, bytes intact", async () => {
  serve([[1, 2], [3]], { "content-length": "1", "content-encoding": "gzip" });
  let r = await load();
  expect(r.seen).toEqual([L]);
  expect(Array.from(got[0])).toEqual([1, 2, 3]);
  serve([[1, 2], [3]], { "content-length": "1" }); // a server that got it wrong
  r = await load();
  expect(Array.from(got[1])).toEqual([1, 2, 3]);
  expect(r.seen.map((m) => Number(/(\d+) %$/.exec(m)?.[1] ?? 0)).every((p) => p <= 100)).toBe(true);
});

test("no Content-Length: no percent, bytes intact", async () => {
  serve([[5], [6, 7]], {});
  const { seen } = await load();
  expect(seen).toEqual([L]);
  expect(Array.from(got[0])).toEqual([5, 6, 7]);
});

test("a failed download is a plain error", async () => {
  serve([[0]], {}, 404);
  const { error } = await load();
  expect((error as Error).message).toBe("Couldn't download the document converter. Check the internet connection and try again.");
  expect(got.length).toBe(0);
});

test("a connection dropping mid-download is the same plain error", async () => {
  const MSG = "Couldn't download the document converter. Check the internet connection and try again.";
  for (const headers of [{ "content-length": "100" }, {}] as Record<string, string>[]) {
    vi.stubGlobal("fetch", async () =>
      new Response(
        new ReadableStream({
          start(c) {
            c.enqueue(new Uint8Array([1, 2]));
            c.error(new TypeError("network error"));
          },
        }),
        { status: 200, headers },
      ),
    );
    const { error } = await load();
    expect((error as Error).message).toBe(MSG);
  }
  expect(got.length).toBe(0);
});
